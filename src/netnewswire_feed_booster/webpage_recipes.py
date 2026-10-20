from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from .rss_safety import parse_internet_date
from .webpage_feeds import ParsedWebpageFeed, WebpageFeedItem, WebpageFeedRecipe


class _HydeFMArchiveHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[WebpageFeedItem] = []
        self.loop_div_depth = 0
        self.published_at = ""
        self.image_url = ""
        self.item_title = ""
        self.item_url = ""
        self.genres: List[str] = []
        self.in_heading = False
        self.heading_parts: List[str] = []
        self.heading_url = ""
        self.anchor_url = ""
        self.anchor_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if tag == "div":
            if self.loop_div_depth:
                self.loop_div_depth += 1
            elif attributes.get("data-elementor-type") == "loop-item":
                self.loop_div_depth = 1
                self._reset_item()
            return
        if not self.loop_div_depth:
            return
        if tag == "img" and not self.image_url:
            image_url = str(attributes.get("src") or "")
            if _url_matches(image_url, {"hydefmradio-archive.s3.us-west-1.amazonaws.com"}, "/"):
                self.image_url = image_url
        elif tag == "h2":
            self.in_heading = True
            self.heading_parts = []
            self.heading_url = ""
        elif tag == "a":
            self.anchor_url = str(attributes.get("href") or "")
            self.anchor_parts = []
            if self.in_heading and _url_matches(
                self.anchor_url,
                {"hydefm.com", "www.hydefm.com"},
                "/archive/",
            ):
                self.heading_url = self.anchor_url

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if not self.loop_div_depth:
            return
        if self.in_heading:
            self.heading_parts.append(data)
        if self.anchor_url:
            self.anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.loop_div_depth:
            return
        if tag == "a":
            anchor_text = " ".join("".join(self.anchor_parts).split())
            if anchor_text and _url_matches(
                self.anchor_url,
                {"hydefm.com", "www.hydefm.com"},
                "/genres/",
            ):
                self.genres.append(anchor_text)
            self.anchor_url = ""
            self.anchor_parts = []
        elif tag == "h2":
            heading = " ".join("".join(self.heading_parts).split())
            if self.heading_url:
                self.item_title = heading
                self.item_url = self.heading_url
            elif re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", heading):
                self.published_at = heading
            self.in_heading = False
            self.heading_parts = []
            self.heading_url = ""
        elif tag == "div":
            self.loop_div_depth -= 1
            if self.loop_div_depth == 0:
                self._finish_item()

    def _reset_item(self) -> None:
        self.published_at = ""
        self.image_url = ""
        self.item_title = ""
        self.item_url = ""
        self.genres = []
        self.in_heading = False
        self.heading_parts = []
        self.heading_url = ""
        self.anchor_url = ""
        self.anchor_parts = []

    def _finish_item(self) -> None:
        if self.item_title and self.item_url:
            self.items.append(
                WebpageFeedItem(
                    title=self.item_title,
                    url=self.item_url,
                    published_at=self.published_at,
                    image_url=self.image_url,
                    details={"Genres": list(dict.fromkeys(self.genres))},
                )
            )


def parse_hydefm_archive(html: str, site_url: str) -> ParsedWebpageFeed:
    parser = _HydeFMArchiveHTMLParser()
    parser.feed(html)
    if not parser.items:
        raise ValueError(f"Could not find archive items for {site_url}")
    return ParsedWebpageFeed(
        title="HydeFM Archives",
        description=f"Generated RSS feed from {site_url}.",
        items=tuple(parser.items),
    )


def format_month_day_year(value: str) -> str:
    published = datetime.strptime(value, "%B %d, %Y").replace(tzinfo=timezone.utc)
    return format_datetime(published)


HYDEFM_ARCHIVE_RECIPE = WebpageFeedRecipe(
    id="hydefm-archives",
    name="HydeFM archives",
    default_url="https://hydefm.com/archives/",
    source_id_prefix="radio",
    allowed_site_hosts=frozenset({"hydefm.com", "www.hydefm.com"}),
    allowed_path_prefixes=("/archives",),
    allowed_fetch_hosts=frozenset({"hydefm.com", "www.hydefm.com"}),
    allowed_item_hosts=frozenset({"hydefm.com", "www.hydefm.com"}),
    allowed_image_hosts=frozenset({"hydefmradio-archive.s3.us-west-1.amazonaws.com"}),
    parse=parse_hydefm_archive,
    format_published_at=format_month_day_year,
)


_NEON_SHRINES_SEASON = re.compile(r"Season (\w+) of NEON SHRINES", re.IGNORECASE)
_NEON_SHRINES_DATE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})(?:\s+(\d{1,2}:\d{2}\s*[ap]m))?\b", re.IGNORECASE)
_NEON_SHRINES_SEASON_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
# The page's pre-wrap text breaks song lists with long runs of spaces rather than
# <br>. Two spaces also occur inside song names ("Gang of Four  - Damaged Goods"),
# so only three or more count as a break.
_NEON_SHRINES_LINE_GAP = re.compile(r"\s{3,}")


class _NeonShrinesHTMLParser(HTMLParser):
    """Flatten the Squarespace page into ordered heading, paragraph and Drive-link events."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: List[tuple] = []
        self.capture_tag = ""
        self.parts: List[str] = []
        self.in_footer = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if self.in_footer:
            return
        if tag == "footer":
            # Everything after the last episode's block would otherwise be read as its text.
            self.in_footer = True
            self.capture_tag = ""
            self.parts = []
        elif tag in {"h1", "h2", "p"} and not self.capture_tag:
            self.capture_tag = tag
            self.parts = []
        elif tag == "br" and self.capture_tag:
            self.parts.append("\n")
        elif tag == "a":
            drive_url = _drive_file_url(str(dict(attrs).get("href") or ""))
            if drive_url:
                self.events.append(("a", drive_url))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self.capture_tag:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self.capture_tag:
            lines = [
                " ".join(segment.split())
                for line in "".join(self.parts).split("\n")
                for segment in _NEON_SHRINES_LINE_GAP.split(line)
            ]
            lines = [line for line in lines if line]
            if lines:
                self.events.append((tag, lines))
            self.capture_tag = ""
            self.parts = []


def parse_osgh_neon_shrines(html: str, site_url: str) -> ParsedWebpageFeed:
    parser = _NeonShrinesHTMLParser()
    parser.feed(html)

    season = ""
    episodes: List[dict] = []
    current: Optional[dict] = None
    for kind, value in parser.events:
        if kind == "h1":
            match = _NEON_SHRINES_SEASON.fullmatch(" ".join(value))
            if match:
                season = _NEON_SHRINES_SEASON_NUMBERS.get(match.group(1).lower(), match.group(1).lower())
                current = None
        elif kind == "h2":
            current = {"season": season, "title": " ".join(value), "date": "", "aired": None, "about": [], "songs": [], "links": [], "in_songs": False}
            episodes.append(current)
        elif kind == "p" and current is not None:
            text = " ".join(value)
            date_match = _NEON_SHRINES_DATE.match(text) if current["aired"] is None else None
            aired = _neon_shrines_aired(date_match) if date_match else None
            if aired is not None:
                current["date"] = date_match.group(0)
                current["aired"] = aired
            elif text.lower().startswith("songs:"):
                current["in_songs"] = True
                first_line = value[0][len("songs:"):].strip()
                current["songs"].extend(([first_line] if first_line else []) + value[1:])
            elif current["in_songs"]:
                current["songs"].extend(value)
            else:
                current["about"].append(text)
        elif kind == "a" and current is not None:
            current["links"].append(value)

    dated_items: List[tuple] = []
    seen_ids: set = set()
    for episode in episodes:
        if episode["aired"] is None:
            continue
        # The air date is the identity: titles get edited (typos, guest names), and a
        # new identity would make NetNewsWire show the episode again as unread.
        episode_id = episode["aired"].date().isoformat()
        if episode_id in seen_ids:
            slug = re.sub(r"[^a-z0-9]+", "-", episode["title"].lower()).strip("-")[:80]
            episode_id = f"{episode_id}-{slug}"
        if episode_id in seen_ids:
            continue
        seen_ids.add(episode_id)
        item_url = f"https://www.osghpress.com/neon-shrines?episode={episode_id}"
        title = episode["title"]
        if episode["season"]:
            title = f"S{episode['season']} {title}"
        details = {"Aired": [episode["date"]]}
        if episode["about"]:
            details["About"] = [" ".join(episode["about"])]
        if episode["songs"]:
            details["Songs"] = episode["songs"]
        # Only one Drive link inside an episode's own span maps to it with certainty;
        # Squarespace sometimes stacks a whole season's buttons under the last episode.
        if len(episode["links"]) == 1:
            details["Audio"] = episode["links"]
        item = WebpageFeedItem(title=title, url=item_url, published_at=episode["date"], details=details)
        dated_items.append((episode["aired"], item))

    if not dated_items:
        raise ValueError(f"Could not find NEON SHRINES episodes for {site_url}")
    # The page lists seasons newest first but each season's episodes oldest first.
    # The hosted bridge keeps only the first N items, so put the newest first.
    dated_items.sort(key=lambda entry: entry[0], reverse=True)
    return ParsedWebpageFeed(
        title="NEON SHRINES",
        description=f"Episodes of NEON SHRINES, the OSGH radio show, generated from {site_url}.",
        items=tuple(item for _aired, item in dated_items),
    )


def _neon_shrines_aired(match: re.Match) -> Optional[datetime]:
    """Air dates are Pacific; episodes without a listed time aired in the 4pm slot."""
    from zoneinfo import ZoneInfo

    clock = (match.group(2) or "4:00pm").replace(" ", "").upper()
    try:
        aired = datetime.strptime(f"{match.group(1)} {clock}", "%B %d, %Y %I:%M%p")
    except ValueError:
        # The pattern also fits text like "Volume 2, 2024", which is not a date.
        return None
    return aired.replace(tzinfo=ZoneInfo("America/Los_Angeles"))


def format_neon_shrines_airdate(value: str) -> str:
    match = _NEON_SHRINES_DATE.match(value.strip())
    aired = _neon_shrines_aired(match) if match else None
    if aired is None:
        raise ValueError(f"Unrecognized NEON SHRINES date: {value}")
    return format_datetime(aired)


def _drive_file_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]{10,})/view", parsed.path or "")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "drive.google.com" or not match:
        return ""
    return f"https://drive.google.com/file/d/{match.group(1)}/view"


OSGH_NEON_SHRINES_RECIPE = WebpageFeedRecipe(
    id="osgh-neon-shrines",
    name="NEON SHRINES",
    default_url="https://www.osghpress.com/neon-shrines",
    source_id_prefix="radio",
    allowed_site_hosts=frozenset({"osghpress.com", "www.osghpress.com"}),
    allowed_path_prefixes=("/neon-shrines",),
    allowed_fetch_hosts=frozenset({"osghpress.com", "www.osghpress.com"}),
    allowed_item_hosts=frozenset({"www.osghpress.com"}),
    allowed_image_hosts=frozenset(),
    parse=parse_osgh_neon_shrines,
    format_published_at=format_neon_shrines_airdate,
)

class _MailchimpBlogHTMLParser(HTMLParser):
    """Read the blog index's changelog timeline: dated day groups of post cards."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: List[dict] = []
        self.day = ""
        self.capture = ""
        self.parts: List[str] = []
        self.tags_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        classes = set(str(attributes.get("class") or "").split())
        if tag == "time" and "fern-changelog-day" in classes:
            self.day = str(attributes.get("datetime") or "")
        elif tag == "a" and "fern-changelog-card-link" in classes:
            self.entries.append({"date": self.day, "href": str(attributes.get("href") or ""), "title": "", "excerpt": "", "tags": []})
            self._start("title")
        elif tag == "p" and "fern-changelog-card-excerpt" in classes and self.entries:
            self._start("excerpt")
        elif tag == "div":
            if self.tags_depth:
                self.tags_depth += 1
            elif "fern-changelog-card-tags" in classes:
                self.tags_depth = 1
        # The filter buttons above the timeline use the same badge class, so only
        # badges inside a card's tag row count.
        elif tag == "span" and self.tags_depth and "fern-docs-badge" in classes and self.entries:
            self._start("tag")

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.tags_depth:
            self.tags_depth -= 1
            return
        closing = {"title": "a", "excerpt": "p", "tag": "span"}.get(self.capture)
        if tag != closing:
            return
        text = " ".join("".join(self.parts).split())
        if text:
            if self.capture == "tag":
                self.entries[-1]["tags"].append(text)
            else:
                self.entries[-1][self.capture] = text
        self.capture = ""
        self.parts = []

    def _start(self, capture: str) -> None:
        self.capture = capture
        self.parts = []


def parse_mailchimp_engineering_blog(html: str, site_url: str) -> ParsedWebpageFeed:
    parser = _MailchimpBlogHTMLParser()
    parser.feed(html)

    items: List[WebpageFeedItem] = []
    seen_urls: set = set()
    for entry in parser.entries:
        url = _mailchimp_post_url(entry["href"])
        if not url or not entry["title"] or url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            published_at = entry["date"] if entry["date"] and parse_internet_date(entry["date"]) else ""
        except ValueError:
            published_at = ""
        details = {}
        if entry["excerpt"]:
            details["Summary"] = [entry["excerpt"]]
        if entry["tags"]:
            details["Tags"] = list(dict.fromkeys(entry["tags"]))
        items.append(WebpageFeedItem(title=entry["title"], url=url, published_at=published_at, details=details))

    if not items:
        raise ValueError(f"Could not find Mailchimp Engineering posts for {site_url}")
    return ParsedWebpageFeed(
        title="Mailchimp Engineering",
        description=f"Posts from the Mailchimp Engineering blog, generated from {site_url}.",
        items=tuple(items),
    )


def _mailchimp_post_url(href: str) -> str:
    parsed = urlparse(urljoin("https://mailchimp.com/developer/blog/", (href or "").strip()))
    prefix = "/developer/blog/"
    slug = parsed.path[len(prefix):].strip("/") if parsed.path.startswith(prefix) else ""
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in {"mailchimp.com", "www.mailchimp.com"}
        or not re.fullmatch(r"[a-z0-9-]+", slug)
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return f"https://mailchimp.com{prefix}{slug}"


# Mailchimp removed the blog's own RSS feed (rss.xml returns 404) in 2026; the
# index page still lists every post with its date.
MAILCHIMP_ENGINEERING_BLOG_RECIPE = WebpageFeedRecipe(
    id="mailchimp-engineering-blog",
    name="Mailchimp Engineering",
    default_url="https://mailchimp.com/developer/blog/",
    source_id_prefix="blog",
    allowed_site_hosts=frozenset({"mailchimp.com", "www.mailchimp.com"}),
    allowed_path_prefixes=("/developer/blog",),
    allowed_fetch_hosts=frozenset({"mailchimp.com", "www.mailchimp.com"}),
    allowed_item_hosts=frozenset({"mailchimp.com"}),
    allowed_image_hosts=frozenset(),
    parse=parse_mailchimp_engineering_blog,
)

WEBPAGE_FEED_RECIPES = (HYDEFM_ARCHIVE_RECIPE, OSGH_NEON_SHRINES_RECIPE, MAILCHIMP_ENGINEERING_BLOG_RECIPE)


def webpage_recipe_for_url(site_url: str) -> Optional[WebpageFeedRecipe]:
    for recipe in WEBPAGE_FEED_RECIPES:
        if recipe.matches(site_url):
            return recipe
    return None


def require_webpage_recipe(site_url: str) -> WebpageFeedRecipe:
    recipe = webpage_recipe_for_url(site_url)
    if recipe is None:
        raise ValueError(f"No registered webpage recipe supports this URL: {site_url}")
    return recipe


def _url_matches(value: str, hosts: set[str], path_prefix: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and hostname in hosts
        and parsed.path.startswith(path_prefix)
        and not parsed.username
        and not parsed.password
        and port is None
        and not parsed.fragment
        and not parsed.params
    )
