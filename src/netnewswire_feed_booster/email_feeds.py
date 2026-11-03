"""Email sources: per-source addresses, sender checks, and feeds built from accepted mail.

An email source turns a newsletter with no RSS into a feed the hosted bridge
serves on the usual /feeds/<token>/generated/<id>.rss route. Mail reaches the
bridge as a POST from a mail host's inbound hook, one message per request:

    POST /inbound/email
    Authorization: Bearer <RSS_INBOUND_EMAIL_SECRET>
    X-Envelope-To: <the envelope recipient>
    Content-Type: message/rfc822
    <the raw message>

Each source has its own address, `<base>+<slug>-<random>@<mail domain>`, kept
only in the private registry. The bridge finds the source by that address and
rejects unknown ones before reading the message. It accepts mail only from the
source's sender domains, as authenticated by the mail host, keeps only the
sanitized item (never the raw message), and rebuilds the feed from the newest
items. Every check runs before anything is written, so a rejected message
leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy as email_policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import AsyncIterator, Dict, Iterable, List, Optional, Sequence, Tuple

from .email_sanitizer import (
    SanitizePolicy,
    html_to_text,
    list_unsubscribe_urls,
    plain_text_to_html,
    remove_recipients,
    sanitize_email_html,
)
from .feed_store import EmailIntake, Source, slugify
from .rss_safety import ensure_rss_channel, validate_source_id


EMAIL_SOURCE_LABEL = "email-inbound"
INBOUND_SECRET_SETTING = "RSS_INBOUND_EMAIL_SECRET"
INBOUND_ADDRESS_SETTING = "RSS_INBOUND_EMAIL_ADDRESS"
INBOUND_MAX_BYTES_SETTING = "RSS_INBOUND_EMAIL_MAX_BYTES"
DEFAULT_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
# A shorter configured secret counts as none: the route then rejects everything.
MIN_SECRET_CHARS = 32
MAX_ITEM_HTML_CHARS = 200_000
MAX_TITLE_CHARS = 300
# Cloudflare Email Routing prepends its own Authentication-Results header under
# this authserv-id. A sender can write any header it likes further down, so only
# the first header carrying this id is read.
CLOUDFLARE_AUTHSERV_ID = "mx.cloudflare.net"
# Every header that can name who a message was delivered to. Forwarded mail
# carries the original recipient in these, so all of them are scrubbed.
RECIPIENT_HEADERS = (
    "To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To",
    "X-Forwarded-To", "X-Forwarded-For", "Resent-To", "Resent-Cc",
)
LOCAL_PART_LIMIT = 64
RANDOM_HEX_CHARS = 16

_LOCAL_BASE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?")
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))+")
_ADDRESS = re.compile(r"([a-z0-9][a-z0-9._+-]{0,63})@([a-z0-9][a-z0-9.-]{0,252})")
_ADDRESS_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Characters XML 1.0 cannot carry. Mail can contain them; a feed must not.
_NOT_XML = re.compile("[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
_STORE_LOCK = threading.Lock()


class InboundRejected(Exception):
    """A message the bridge refuses, with the HTTP status the inbound hook gets back."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class AcceptedEmail:
    source_id: str
    guid: str
    duplicate: bool = False


@dataclass(frozen=True)
class EmailItem:
    guid: str
    title: str
    published: float
    received: float
    html: str

    def to_json(self) -> str:
        return json.dumps(
            {"guid": self.guid, "title": self.title, "published": self.published, "received": self.received, "html": self.html},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "EmailItem":
        data = json.loads(text)
        return cls(
            guid=str(data["guid"]),
            title=str(data.get("title") or ""),
            published=float(data["published"]),
            received=float(data.get("received") or data["published"]),
            html=str(data.get("html") or ""),
        )


@dataclass(frozen=True)
class AuthResult:
    method: str
    result: str
    properties: Dict[str, str]


# Registry side: addresses and settings.


def parse_address_base(value: str) -> Tuple[str, str]:
    """Split RSS_INBOUND_EMAIL_ADDRESS into its local part and mail domain.

    `inbox@feeds.example.com` gives subaddresses `inbox+<detail>@feeds.example.com`,
    delivered by one routing rule with subaddressing on. A bare domain
    (`feeds.example.com` or `@feeds.example.com`) gives `<detail>@feeds.example.com`,
    for a host that needs one routing rule per address instead.
    """
    value = (value or "").strip().lower()
    local, _, domain = value.rpartition("@") if "@" in value else ("", "", value)
    domain = domain.rstrip(".")
    if local and not _LOCAL_BASE.fullmatch(local):
        raise ValueError(f"{INBOUND_ADDRESS_SETTING} needs a plain local part (letters, digits, '.', '_', '-'; no '+'): {value!r}")
    if not _is_hostname(domain):
        raise ValueError(f"{INBOUND_ADDRESS_SETTING} needs a mail domain such as feeds.example.com: {value!r}")
    return local, domain


def new_inbound_address(base: str, title: str, token: Optional[str] = None) -> str:
    """A fresh, hard-to-guess address for one source: the slug says which, the random part guards it."""
    local_base, domain = parse_address_base(base)
    random_part = token if token is not None else secrets.token_hex(RANDOM_HEX_CHARS // 2)
    prefix = f"{local_base}+" if local_base else ""
    room = LOCAL_PART_LIMIT - len(prefix) - len(random_part) - 1
    slug = slugify(title)[: max(room, 0)].strip("-") or "feed"
    local = f"{prefix}{slug}-{random_part}"
    if len(local) > LOCAL_PART_LIMIT:
        raise ValueError(f"{INBOUND_ADDRESS_SETTING} local part is too long to leave room for a source address")
    return f"{local}@{domain}"


def normalize_mail_domain(value: str) -> str:
    """A sender domain or image host: a lowercase hostname with at least one dot, never an IP."""
    domain = (value or "").strip().lower().lstrip("@").rstrip(".")
    if not _is_hostname(domain):
        raise ValueError(f"Not a domain name: {value!r}")
    return domain


def normalize_recipient(value: str) -> str:
    """The envelope recipient in canonical form, or "" when it is not a plain address."""
    address = (value or "").strip().strip("<>").strip().lower()
    match = _ADDRESS.fullmatch(address)
    if not match or len(address) > 254 or not _is_hostname(match.group(2)):
        return ""
    return address


def email_intake_problem(intake: Optional[EmailIntake]) -> str:
    """Why a registry entry's email settings are unusable, or "" when they are fine."""
    if intake is None:
        return "missing email settings"
    if not normalize_recipient(intake.address) or normalize_recipient(intake.address) != intake.address:
        return "address is not a lowercase plain address"
    if not intake.sender_domains:
        return "no sender domains"
    for domain in [*intake.sender_domains, *intake.image_hosts]:
        try:
            if normalize_mail_domain(domain) != domain:
                return f"domain is not in canonical form: {domain}"
        except ValueError as error:
            return str(error)
    return ""


def is_email_source(source: Source) -> bool:
    return source.source == EMAIL_SOURCE_LABEL and not email_intake_problem(source.email)


def configured_max_message_bytes(value: Optional[str] = None) -> int:
    raw = os.environ.get(INBOUND_MAX_BYTES_SETTING, "") if value is None else value
    limit = int(raw.strip() or DEFAULT_MAX_MESSAGE_BYTES)
    if limit < 1024:
        raise ValueError(f"{INBOUND_MAX_BYTES_SETTING} must be at least 1024")
    return limit


# Bridge side: the checks, in the order the route runs them.


def require_inbound_secret(authorization: Optional[str], expected: str) -> None:
    """401 unless the request carries the configured secret.

    With no secret configured, or one too short to resist guessing, every
    request is rejected, so a copied example file never opens the route.
    """
    expected = (expected or "").strip()
    scheme, _, provided = (authorization or "").strip().partition(" ")
    if len(expected) < MIN_SECRET_CHARS or scheme.lower() != "bearer":
        raise InboundRejected(401, "Unauthorized")
    # Compared as bytes: compare_digest refuses non-ASCII strings, and a header can hold anything.
    if not hmac.compare_digest(provided.strip().encode("utf-8"), expected.encode("utf-8")):
        raise InboundRejected(401, "Unauthorized")


def require_declared_size(content_length: Optional[str], max_bytes: int) -> None:
    """413 when the declared body is over the cap, before any of it is read."""
    if content_length is None or content_length == "":
        return
    try:
        declared = int(content_length)
    except ValueError as error:
        raise InboundRejected(400, "Invalid Content-Length") from error
    if declared > max_bytes:
        raise InboundRejected(413, "Message too large")


def email_sources_by_address(sources: Iterable[Source]) -> Dict[str, Source]:
    """Active email sources by address. An address claimed twice belongs to neither."""
    by_address: Dict[str, Source] = {}
    claimed_twice = set()
    for source in sources:
        if source.status != "active" or not is_email_source(source):
            continue
        address = source.email.address
        if address in by_address:
            claimed_twice.add(address)
        by_address[address] = source
    for address in claimed_twice:
        del by_address[address]
    return by_address


def require_email_source(recipient: Optional[str], sources_by_address: Dict[str, Source]) -> Source:
    """404 for any address the registry does not hold, without looking at the message."""
    address = normalize_recipient(recipient or "")
    if not address:
        raise InboundRejected(400, "Missing or invalid X-Envelope-To")
    source = sources_by_address.get(address)
    if source is None:
        raise InboundRejected(404, "Unknown address")
    return source


async def read_capped_body(chunks: AsyncIterator[bytes], max_bytes: int) -> bytes:
    """Read a request body, stopping with 413 as soon as it passes the cap."""
    body = bytearray()
    async for chunk in chunks:
        body.extend(chunk)
        if len(body) > max_bytes:
            raise InboundRejected(413, "Message too large")
    return bytes(body)


def accept_inbound_email(
    raw: bytes,
    recipient: str,
    source: Source,
    store_root: Path,
    generated_dir: Path,
    now: float,
    max_items: int,
    max_bytes: int,
) -> AcceptedEmail:
    """Check, sanitize, and store one message for `source`, then rebuild its feed.

    Raises InboundRejected before writing anything when the message is too
    large, cannot be parsed, or does not come from the source's sender domains.
    Only the sanitized item is stored; the raw message stays in memory.
    """
    if len(raw) > max_bytes:
        raise InboundRejected(413, "Message too large")
    message = read_message(raw)
    problem = sender_problem(message, source.email.sender_domains if source.email else [])
    if problem:
        raise InboundRejected(403, problem)
    try:
        item = build_email_item(message, source, recipient, now)
    except Exception as error:  # Hostile MIME can break the parser deep inside; say nothing of its content.
        raise InboundRejected(400, "Unreadable message") from error

    item_dir = store_root / validate_source_id(source.id)
    with _STORE_LOCK:
        item_path = item_dir / f"{item.guid}.json"
        if item_path.exists():
            return AcceptedEmail(source.id, item.guid, duplicate=True)
        entries = sorted([*_stored_entries(item_dir), (item, item_path)], key=_newest_first, reverse=True)
        kept, dropped = entries[:max_items], entries[max_items:]
        # Render first: if the feed cannot be built, nothing has been written.
        rss = render_email_feed_rss(source, [entry[0] for entry in kept], now)
        if any(path == item_path for _item, path in kept):
            write_text_atomically(item_path, item.to_json())
        write_text_atomically(generated_dir / f"{source.id}.rss", rss)
        for _item, path in dropped:
            if path != item_path:
                path.unlink(missing_ok=True)
    return AcceptedEmail(source.id, item.guid)


def handle_inbound_email(
    *,
    authorization: Optional[str],
    content_length: Optional[str],
    recipient: Optional[str],
    raw: bytes,
    expected_secret: str,
    sources_by_address: Dict[str, Source],
    store_root: Path,
    generated_dir: Path,
    now: float,
    max_items: int,
    max_bytes: int,
) -> AcceptedEmail:
    """Every check the inbound route makes, in its order, for a body already read."""
    require_inbound_secret(authorization, expected_secret)
    require_declared_size(content_length, max_bytes)
    source = require_email_source(recipient, sources_by_address)
    return accept_inbound_email(raw, normalize_recipient(recipient or ""), source, store_root, generated_dir, now, max_items, max_bytes)


# Parsing and sender authentication.


def read_message(raw: bytes) -> EmailMessage:
    try:
        message = BytesParser(policy=email_policy.default).parsebytes(raw)
    except Exception as error:  # The parser can fail in many ways on hostile input.
        raise InboundRejected(400, "Unreadable message") from error
    if not message.keys():
        raise InboundRejected(400, "Unreadable message")
    return message


def sender_problem(message: EmailMessage, sender_domains: Sequence[str]) -> str:
    """Why the message is not from one of `sender_domains`, or "" when it is.

    The From domain must be a sender domain (or a subdomain of one), and the
    mail host's own Authentication-Results must show a pass that vouches for a
    sender domain: DMARC for the From domain itself, or DKIM's signing domain,
    or SPF's envelope sender domain.
    """
    try:
        from_headers = [str(value) for value in message.get_all("From") or []]
    except Exception:
        return "Unreadable From header"
    if len(from_headers) != 1:
        return "Message must have exactly one From header"
    addresses = [address for _name, address in getaddresses(from_headers) if address]
    if len(addresses) != 1:
        return "From must name exactly one address"
    from_domain = _address_domain(addresses[0])
    if not _within_any(from_domain, sender_domains):
        return "Sender is not one of this source's sender domains"

    results = cloudflare_auth_results(message)
    if results is None:
        return f"No Authentication-Results from {CLOUDFLARE_AUTHSERV_ID}"
    for result in results:
        if result.result != "pass":
            continue
        if result.method == "dmarc":
            vouched = _address_domain(result.properties.get("header.from", "")) or from_domain
            if vouched == from_domain:
                return ""
        elif result.method == "dkim":
            signer = result.properties.get("header.d", "") or _address_domain(result.properties.get("header.i", ""))
            if _within_any(signer.lower(), sender_domains):
                return ""
        elif result.method == "spf":
            if _within_any(_address_domain(result.properties.get("smtp.mailfrom", "")), sender_domains):
                return ""
    return "No passing DMARC, DKIM, or SPF result for a sender domain"


def cloudflare_auth_results(message: EmailMessage) -> Optional[List[AuthResult]]:
    """The results in the first Authentication-Results header from Cloudflare, or None.

    Later headers with the same authserv-id are ignored: a sender can add those.
    """
    try:
        headers = [str(value) for value in message.get_all("Authentication-Results") or []]
    except Exception:
        return None
    for header in headers:
        authserv_id, results = parse_authentication_results(header)
        if authserv_id == CLOUDFLARE_AUTHSERV_ID:
            return results
    return None


def parse_authentication_results(value: str) -> Tuple[str, List[AuthResult]]:
    """Parse one Authentication-Results header (RFC 8601) into its authserv-id and results."""
    segments = _split_outside_quotes(_strip_comments(value), ";")
    if not segments:
        return "", []
    head = segments[0].split()
    authserv_id = head[0].lower() if head else ""
    results: List[AuthResult] = []
    for segment in segments[1:]:
        tokens = re.sub(r"\s*=\s*", "=", segment.strip()).split()
        if not tokens or "=" not in tokens[0]:
            continue
        method, _, result = tokens[0].partition("=")
        properties: Dict[str, str] = {}
        for token in tokens[1:]:
            name, separator, item = token.partition("=")
            if separator and "." in name:
                properties[name.lower()] = item.strip('"')
        results.append(AuthResult(method.split("/")[0].lower(), result.lower(), properties))
    return authserv_id, results


def message_recipients(message: EmailMessage, envelope_recipient: str) -> Tuple[str, ...]:
    """Every address the message names as a recipient, plus the envelope recipient."""
    found = {envelope_recipient.lower()} if envelope_recipient else set()
    for name in RECIPIENT_HEADERS:
        try:
            values = [str(value) for value in message.get_all(name) or []]
        except Exception:
            continue
        for value in values:
            found.update(address.lower() for address in _ADDRESS_IN_TEXT.findall(value))
    return tuple(sorted(found))


def build_email_item(message: EmailMessage, source: Source, recipient: str, now: float) -> EmailItem:
    recipients = message_recipients(message, recipient)
    policy = SanitizePolicy(
        recipients=recipients,
        unsubscribe_urls=list_unsubscribe_urls(_header_text(message, "List-Unsubscribe")),
        image_hosts=frozenset(source.email.image_hosts if source.email else ()),
    )
    body = message.get_body(preferencelist=("html", "plain"))
    markup = ""
    if body is not None:
        content = _part_text(body)
        markup = content if body.get_content_subtype() == "html" else plain_text_to_html(content)
    cleaned = sanitize_email_html(markup, policy)
    if len(cleaned) > MAX_ITEM_HTML_CHARS:
        cleaned = sanitize_email_html(plain_text_to_html(html_to_text(cleaned)[:MAX_ITEM_HTML_CHARS]), policy)

    title = " ".join(remove_recipients(_header_text(message, "Subject"), recipients).split())[:MAX_TITLE_CHARS]
    return EmailItem(
        guid=_message_digest(message, body),
        title=xml_safe(title) or "(no subject)",
        published=_published_at(message, now),
        received=now,
        html=xml_safe(cleaned),
    )


# Storage and rendering.


def xml_safe(text: str) -> str:
    return _NOT_XML.sub("", text or "")


def render_email_feed_rss(source: Source, items: Sequence[EmailItem], now: float) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = source.title
    if source.site_url:
        ET.SubElement(channel, "link").text = source.site_url
    ET.SubElement(channel, "description").text = f"Mail received for {source.title}"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.fromtimestamp(now, tz=timezone.utc))
    for email_item in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = xml_safe(email_item.title)
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = email_item.guid
        ET.SubElement(item, "pubDate").text = format_datetime(datetime.fromtimestamp(email_item.published, tz=timezone.utc))
        ET.SubElement(item, "description").text = xml_safe(email_item.html)
    ET.indent(rss, space="  ")
    return ensure_rss_channel('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n")


def write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


# Helpers.


def _stored_entries(item_dir: Path) -> List[Tuple[EmailItem, Path]]:
    entries: List[Tuple[EmailItem, Path]] = []
    for path in item_dir.glob("*.json") if item_dir.exists() else []:
        try:
            entries.append((EmailItem.from_json(path.read_text(encoding="utf-8")), path))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return entries


def _newest_first(entry: Tuple[EmailItem, Path]) -> Tuple[float, float, str]:
    item = entry[0]
    return (item.published, item.received, item.guid)


def _is_hostname(value: str) -> bool:
    if not value or len(value) > 253 or not _HOSTNAME.fullmatch(value) or value.endswith("-"):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return True
    return False


def _address_domain(value: str) -> str:
    value = (value or "").strip().strip("<>").lower().rstrip(".")
    return value.rpartition("@")[2]


def _within_any(domain: str, domains: Iterable[str]) -> bool:
    domain = (domain or "").lower().rstrip(".")
    if not domain:
        return False
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in (item.lower() for item in domains) if allowed)


def _header_text(message: EmailMessage, name: str) -> str:
    try:
        value = message.get(name)
    except Exception:
        return ""
    return str(value) if value is not None else ""


def _part_text(part: EmailMessage) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, ValueError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")
    return content if isinstance(content, str) else content.decode("utf-8", errors="replace")


def _message_digest(message: EmailMessage, body: Optional[EmailMessage]) -> str:
    message_id = _header_text(message, "Message-ID").strip()
    if not message_id:
        # Without a Message-ID, a redelivered copy still has the same sender, date, subject, and body.
        body_text = _part_text(body) if body is not None else ""
        message_id = "\n".join(
            [_header_text(message, "From"), _header_text(message, "Date"), _header_text(message, "Subject"), body_text]
        )
    return hashlib.sha256(message_id.encode("utf-8", errors="replace")).hexdigest()


def _published_at(message: EmailMessage, now: float) -> float:
    try:
        sent = parsedate_to_datetime(_header_text(message, "Date"))
    except (TypeError, ValueError, IndexError):
        return now
    if sent is None:
        return now
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    published = sent.timestamp()
    # A Date in the future would pin the item to the top of the feed.
    return published if 0 < published <= now + 24 * 60 * 60 else now


def _strip_comments(value: str) -> str:
    out: List[str] = []
    depth = 0
    quoted = False
    escaped = False
    for character in value:
        if escaped:
            if depth == 0:
                out.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
            if depth == 0:
                out.append(character)
        elif quoted:
            out.append(character)
            if character == '"':
                quoted = False
        elif character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif depth == 0:
            if character == '"':
                quoted = True
            out.append(character)
    return " ".join("".join(out).split())


def _split_outside_quotes(value: str, separator: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    quoted = False
    for character in value:
        if character == '"':
            quoted = not quoted
        if character == separator and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    parts.append("".join(current).strip())
    return [part for part in parts if part]
