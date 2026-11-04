"""Reduce an email's HTML to the small, inert subset a feed item may carry.

Email HTML is built to track its reader and to act on their subscription:
unsubscribe and preference links, open-tracking pixels, the recipient's own
address in text and links, and scripts, styles, and forms. A feed item keeps
none of it. This is an allowlist. A tag or attribute not named here is dropped,
and for a few tags (scripts, styles, forms, frames) everything inside goes too.

Links are judged with the text around them, not just their own: "To stop these
emails, click here" puts the telling words outside the link, and a link wrapped
in a click tracker has nothing telling in its URL. So the markup is parsed into
tokens first, and each link is then judged with the text of the block it sits in.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, unquote, unquote_plus, urlencode, urlsplit, urlunsplit


KEPT_TAGS = frozenset(
    {
        "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "dd", "del", "div", "dl", "dt",
        "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "li",
        "ol", "p", "pre", "q", "s", "small", "span", "strong", "sub", "sup", "table", "tbody", "td",
        "tfoot", "th", "thead", "tr", "u", "ul",
    }
)
VOID_TAGS = frozenset({"br", "hr", "img"})
# Dropped along with everything inside them. Void elements (input, meta, link,
# embed) are not listed: they have no content, and are dropped as unknown tags.
DROPPED_WITH_CONTENT = frozenset(
    {
        "applet", "audio", "button", "canvas", "datalist", "form", "frame", "frameset", "head", "iframe",
        "map", "math", "noembed", "noframes", "noscript", "object", "script", "select", "style", "svg",
        "template", "textarea", "title", "video",
    }
)
# Where one block of text ends and the next begins, kept tag or not.
BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "caption", "center", "dd", "div", "dl",
        "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)
KEPT_ATTRIBUTES: Dict[str, frozenset] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "width", "height", "title"}),
    "ol": frozenset({"start"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}
NUMERIC_ATTRIBUTES = frozenset({"width", "height", "colspan", "rowspan", "start"})
# Content an email hides from its reader (preheaders, per-recipient tracking
# text) stays hidden: with styles stripped it would otherwise show in the item.
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all|max-height\s*:\s*0(?![.\d])", re.IGNORECASE)
HIDEABLE_TAGS = frozenset({"div", "p", "span", "table", "td", "tr"})

# Words that mark a link as acting on the subscription or the account, in the
# link's own text or the block around it.
_ACTION_TEXT = re.compile(
    r"\bunsubscrib"
    r"|\bopt[-\s]?out\b"
    r"|\bstop (?:receiving|getting)\b"
    r"|\bstop (?:these|all|future|our) (?:e-?mails|messages|notifications|updates)\b"
    r"|\bno longer (?:wish|want) to (?:receive|get)\b"
    r"|\b(?:don't|do not) want (?:to receive |to get )?(?:these|our|this|future) (?:e-?mails?|messages|notifications|newsletter)\b"
    r"|\bremove (?:me|yourself) from (?:this|our|the) (?:list|mailing list)\b"
    r"|\bmanage (?:your |my |email |the )*(?:subscriptions?|preferences|account|e-?mails?|notifications|settings)\b"
    r"|\b(?:e-?mail|notification|communication|subscription|mailing) (?:preferences|settings)\b"
    r"|\bupdate (?:your |my )*(?:preferences|profile|subscriptions?|e-?mail address|e-?mail settings)\b"
    r"|\b(?:your|my) account\b"
    r"|\baccount settings\b",
    re.IGNORECASE,
)
# The same, in a link's path or query.
_ACTION_PATH = re.compile(r"unsub|opt[-_]?out|manage|preference|subscri|account|settings|profile", re.IGNORECASE)
_ACTION_QUERY = re.compile(r"unsub|opt[-_]?out|preference|manage", re.IGNORECASE)

# Open-tracking hosts and paths, refused even when a source allowlists the host.
KNOWN_TRACKER_SUFFIXES = frozenset(
    {
        "list-manage.com", "sendgrid.net", "mandrillapp.com", "mcsv.net", "rs6.net", "exct.net",
        "hubspotlinks.com", "hs-analytics.net", "convertkit-mail.com", "convertkit-mail2.com",
        "mailgun.org", "sparkpostmail.com", "klclick.com",
    }
)
_TRACKER_PATH = re.compile(r"/(?:open|opens|track|tracking|pixel|beacon|wf/open|o)(?:[/.?]|$)|open\.php|/e/o/", re.IGNORECASE)
TRACKING_PARAMETER_PREFIXES = ("utm_", "mc_", "_hs", "oly_", "vero_", "ck_", "ml_", "pk_", "mtm_")
TRACKING_PARAMETERS = frozenset({"mkt_tok", "rb_clickid", "s_cid", "sc_cid", "__s", "_ke", "trk", "trkid", "fbclid", "gclid", "mailing_id"})

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+")


@dataclass(frozen=True)
class SanitizePolicy:
    """What to remove from one message, beyond the fixed rules.

    `recipients` are the addresses the message was delivered to, removed from
    text and links in every form found. `unsubscribe_urls` are the message's
    List-Unsubscribe targets. `image_hosts` are the hosts (and their subdomains)
    whose remote images may stay; every other remote image is dropped.
    """

    recipients: Tuple[str, ...] = ()
    unsubscribe_urls: frozenset = frozenset()
    image_hosts: frozenset = frozenset()
    _recipient_forms: Tuple[str, ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        recipients = tuple(sorted({address.strip().lower() for address in self.recipients if "@" in (address or "")}))
        object.__setattr__(self, "recipients", recipients)
        object.__setattr__(self, "_recipient_forms", _encoded_forms(recipients))

    def mentions_recipient(self, value: str) -> bool:
        if not self.recipients:
            return False
        candidates = {value.lower()}
        decoded = value
        for _ in range(3):
            decoded = unquote(decoded)
            candidates.add(decoded.lower())
            candidates.add(unquote_plus(decoded).lower())
        if any(recipient in candidate for candidate in candidates for recipient in self.recipients):
            return True
        return any(form in value for form in self._recipient_forms)


def remove_recipients(text: str, recipients: Iterable[str]) -> str:
    """Remove every recipient address from text, matching case-insensitively."""
    addresses = sorted({address.strip() for address in recipients if "@" in (address or "")}, key=len, reverse=True)
    if not addresses or not text:
        return text
    pattern = re.compile("|".join(re.escape(address) for address in addresses), re.IGNORECASE)
    return pattern.sub("", text)


def list_unsubscribe_urls(header_value: str) -> frozenset:
    """The http(s) targets in a List-Unsubscribe header: `<https://...>, <mailto:...>`."""
    urls = set()
    for target in re.findall(r"<([^<>]+)>", header_value or ""):
        target = target.strip()
        if target.lower().startswith(("http://", "https://")):
            urls.add(html.unescape(target))
    return frozenset(urls)


def plain_text_to_html(text: str) -> str:
    """Escape a text/plain body into paragraphs, turning bare URLs into links.

    The result still goes through sanitize_email_html, so linked URLs meet the
    same rules as links in an HTML body.
    """
    paragraphs = [block for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n")) if block.strip()]
    rendered = []
    for block in paragraphs:
        parts = []
        position = 0
        for match in _URL_IN_TEXT.finditer(block):
            parts.append(html.escape(block[position : match.start()], quote=False))
            url = match.group(0).rstrip(".,;:!?)")
            parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url, quote=False)}</a>')
            position = match.start() + len(url)
        parts.append(html.escape(block[position:], quote=False))
        rendered.append("<p>" + "".join(parts).strip().replace("\n", "<br>") + "</p>")
    return "\n".join(rendered)


def html_to_text(markup: str) -> str:
    """Visible text only, for when an item's HTML is too large to keep."""
    tokens = _tokenize(markup)
    pieces = []
    for token in tokens:
        if token[0] == "text":
            pieces.append(token[1])
        elif token[0] == "boundary":
            pieces.append("\n")
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(pieces))).strip()


def sanitize_email_html(markup: str, policy: SanitizePolicy) -> str:
    tokens = _tokenize(markup)
    dropped = _dropped_anchor_tokens(tokens, policy)
    out: List[str] = []
    open_tags: List[str] = []
    for index, token in enumerate(tokens):
        if index in dropped:
            continue
        kind = token[0]
        if kind == "text":
            out.append(html.escape(remove_recipients(token[1], policy.recipients), quote=False))
        elif kind == "start":
            tag, attrs = token[1], token[2]
            rendered = _render_start(tag, attrs, policy)
            if rendered is None:
                continue
            if tag == "a" and "a" in open_tags:
                # Links do not nest; a new one ends the one still open.
                while open_tags:
                    closing = open_tags.pop()
                    out.append(f"</{closing}>")
                    if closing == "a":
                        break
            out.append(rendered)
            if tag not in VOID_TAGS:
                open_tags.append(tag)
        elif kind == "end":
            tag = token[1]
            if tag not in open_tags:
                continue
            while open_tags:
                closing = open_tags.pop()
                out.append(f"</{closing}>")
                if closing == tag:
                    break
    out.extend(f"</{tag}>" for tag in reversed(open_tags))
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


Token = Tuple


class _Tokenizer(HTMLParser):
    """Turns markup into kept tags, text, and block boundaries; nothing else survives."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: List[Token] = []
        self._skip_tag: Optional[str] = None
        self._skip_depth = 0
        self._skip_ends_at_block = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._start(tag, attrs, self_closing=True)
        if not self._skip_tag and tag in KEPT_TAGS and tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def _start(self, tag: str, attrs: List[Tuple[str, Optional[str]]], self_closing: bool) -> None:
        if self._skip_tag:
            if (tag == "body" and self._skip_tag == "head") or (self._skip_ends_at_block and tag in BLOCK_TAGS):
                # A head left open ends where the body starts, and an open p where the next block does.
                self._skip_tag = None
                self._skip_depth = 0
                self._skip_ends_at_block = False
            elif tag == self._skip_tag and not self_closing:
                self._skip_depth += 1
                return
            else:
                return
        hidden = tag in HIDEABLE_TAGS and any(name == "style" and _HIDDEN_STYLE.search(value or "") for name, value in attrs)
        if tag in DROPPED_WITH_CONTENT or hidden:
            if not self_closing:
                self._skip_tag = tag
                self._skip_depth = 1
                self._skip_ends_at_block = tag == "p"
            return
        if tag in BLOCK_TAGS:
            self.tokens.append(("boundary",))
        if tag in KEPT_TAGS:
            allowed = KEPT_ATTRIBUTES.get(tag, frozenset())
            kept = {name: (value or "").strip() for name, value in attrs if name in allowed}
            self.tokens.append(("start", tag, kept))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = None
                    self._skip_ends_at_block = False
            return
        if tag in BLOCK_TAGS:
            self.tokens.append(("boundary",))
        if tag in KEPT_TAGS and tag not in VOID_TAGS:
            self.tokens.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if not self._skip_tag and data:
            self.tokens.append(("text", data))


def _tokenize(markup: str) -> List[Token]:
    parser = _Tokenizer()
    try:
        parser.feed(markup or "")
        parser.close()
    except (AssertionError, ValueError):
        # Older parsers give up on some malformed declarations. Everything
        # before that point has already been tokenized, under the same rules.
        pass
    return parser.tokens


def _dropped_anchor_tokens(tokens: Sequence[Token], policy: SanitizePolicy) -> set:
    """Indexes of every token in a link that acts on the subscription or account."""
    segment_of: List[int] = []
    segment_text: Dict[int, List[str]] = {}
    segment = 0
    for token in tokens:
        if token[0] == "boundary":
            segment += 1
        segment_of.append(segment)
        if token[0] == "text":
            segment_text.setdefault(segment, []).append(token[1])

    dropped: set = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token[0] != "start" or token[1] != "a":
            index += 1
            continue
        end = index + 1
        while end < len(tokens) and not (tokens[end][0] in {"start", "end"} and tokens[end][1] == "a"):
            end += 1
        closes_here = end < len(tokens) and tokens[end][0] == "end"
        last = end if closes_here else end - 1
        anchor_text = "".join(tokens[position][1] for position in range(index, last + 1) if tokens[position][0] == "text")
        block_text = " ".join(
            "".join(segment_text.get(number, []))
            for number in range(segment_of[index], segment_of[last] + 1)
        )
        if _is_action_link(token[2].get("href", ""), anchor_text, block_text, policy):
            dropped.update(range(index, last + 1))
        index = end
    return dropped


def _is_action_link(href: str, anchor_text: str, block_text: str, policy: SanitizePolicy) -> bool:
    if _ACTION_TEXT.search(anchor_text) or _ACTION_TEXT.search(block_text):
        return True
    href = href.strip()
    if not href:
        return False
    if href in policy.unsubscribe_urls:
        return True
    parts = urlsplit(href)
    if parts.scheme.lower() in {"http", "https"}:
        if _ACTION_PATH.search(unquote(parts.path)) or _ACTION_QUERY.search(unquote(parts.query)):
            return True
    return False


def _render_start(tag: str, attrs: Dict[str, str], policy: SanitizePolicy) -> Optional[str]:
    kept: List[Tuple[str, str]] = []
    if tag == "img":
        src = _clean_url(attrs.get("src", ""), policy)
        if not src or not _image_is_kept(src, attrs, policy):
            return None
        kept.append(("src", src))
    elif tag == "a":
        href = _clean_url(attrs.get("href", ""), policy)
        if not href:
            # An unusable or unsafe link keeps its text, not the link.
            return None
        kept.append(("href", href))
    for name in sorted(attrs):
        if name in {"src", "href"}:
            continue
        value = attrs[name]
        if name in NUMERIC_ATTRIBUTES:
            if not re.fullmatch(r"\d{1,4}", value):
                continue
        else:
            value = remove_recipients(value, policy.recipients).strip()
            if not value:
                continue
        kept.append((name, value))
    rendered = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in kept)
    return f"<{tag}{rendered}>" if tag not in VOID_TAGS else f"<{tag}{rendered} />"


def _clean_url(value: str, policy: SanitizePolicy) -> str:
    """An absolute http(s) URL with tracking parameters removed, or "" to drop it."""
    value = (value or "").strip()
    if not value or any(ord(character) < 32 for character in value):
        return ""
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    if policy.mentions_recipient(value):
        return ""
    query = parse_qsl(parts.query, keep_blank_values=True)
    kept_query = [(key, item) for key, item in query if not _is_tracking_parameter(key)]
    if len(kept_query) != len(query):
        value = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept_query), parts.fragment))
    return value


def _is_tracking_parameter(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMETERS or lowered.startswith(TRACKING_PARAMETER_PREFIXES)


def _image_is_kept(src: str, attrs: Dict[str, str], policy: SanitizePolicy) -> bool:
    parts = urlsplit(src)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https":
        return False
    if not any(_host_within(host, allowed) for allowed in policy.image_hosts):
        return False
    if any(_host_within(host, tracker) for tracker in KNOWN_TRACKER_SUFFIXES) or _TRACKER_PATH.search(parts.path):
        return False
    for dimension in ("width", "height"):
        size = attrs.get(dimension, "")
        if re.fullmatch(r"\d{1,4}", size) and int(size) <= 1:
            return False
    return True


def _host_within(host: str, domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    return bool(domain) and (host == domain or host.endswith(f".{domain}"))


def _encoded_forms(recipients: Sequence[str]) -> Tuple[str, ...]:
    """Base64 spellings of each address, as tracking links often carry them."""
    forms = set()
    for address in recipients:
        raw = address.encode("utf-8")
        for encoded in (base64.b64encode(raw), base64.urlsafe_b64encode(raw)):
            forms.add(encoded.decode("ascii").rstrip("="))
    return tuple(sorted(forms))
