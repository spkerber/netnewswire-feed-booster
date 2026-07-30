from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import urlparse

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

WEBPAGE_FEED_RECIPES = (HYDEFM_ARCHIVE_RECIPE,)


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
