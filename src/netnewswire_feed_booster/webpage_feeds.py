from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import format_datetime
from typing import Callable, Dict, FrozenSet, List, Tuple
from urllib.parse import ParseResult, urlparse

from .rss_safety import html_text, image_html, parse_internet_date, safe_https_url


@dataclass
class WebpageFeedItem:
    title: str
    url: str
    published_at: str = ""
    image_url: str = ""
    details: Dict[str, List[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedWebpageFeed:
    title: str
    description: str
    items: Tuple[WebpageFeedItem, ...]


ParseWebpage = Callable[[str, str], ParsedWebpageFeed]
FormatPublishedAt = Callable[[str], str]
BuildFetchUrl = Callable[[str], str]


def format_internet_pubdate(value: str) -> str:
    return format_datetime(parse_internet_date(value))


def direct_fetch_url(site_url: str) -> str:
    return site_url


@dataclass(frozen=True)
class WebpageFeedRecipe:
    """A reviewed, site-specific path from one public webpage shape to RSS."""

    id: str
    name: str
    default_url: str
    source_id_prefix: str
    allowed_site_hosts: FrozenSet[str]
    allowed_path_prefixes: Tuple[str, ...]
    allowed_fetch_hosts: FrozenSet[str]
    allowed_item_hosts: FrozenSet[str]
    allowed_image_hosts: FrozenSet[str]
    parse: ParseWebpage
    build_fetch_url: BuildFetchUrl = direct_fetch_url
    format_published_at: FormatPublishedAt = format_internet_pubdate
    allow_query: bool = False

    def matches(self, site_url: str) -> bool:
        try:
            validate_recipe_url(self, site_url)
        except ValueError:
            return False
        return True


def validate_recipe_url(recipe: WebpageFeedRecipe, site_url: str) -> ParseResult:
    parsed = urlparse((site_url or "").strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"Unsupported {recipe.name} URL: {site_url}") from error

    if (
        parsed.scheme != "https"
        or hostname not in recipe.allowed_site_hosts
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.fragment
        or parsed.params
        or (parsed.query and not recipe.allow_query)
        or not any(_path_matches(parsed.path, prefix) for prefix in recipe.allowed_path_prefixes)
    ):
        raise ValueError(f"Unsupported {recipe.name} URL: {site_url}")
    return parsed


def recipe_fetch_url(recipe: WebpageFeedRecipe, site_url: str) -> str:
    """Return the exact upstream URL declared by a registered recipe."""

    validate_recipe_url(recipe, site_url)
    fetch_url = recipe.build_fetch_url(site_url)
    approved = safe_https_url(fetch_url, allowed_hosts=set(recipe.allowed_fetch_hosts))
    parsed = urlparse(approved)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if not approved or port is not None or parsed.fragment or parsed.params:
        raise ValueError(f"{recipe.name} recipe returned an unsupported fetch URL")
    return approved


def parse_webpage_feed(recipe: WebpageFeedRecipe, content: str, site_url: str) -> ParsedWebpageFeed:
    validate_recipe_url(recipe, site_url)
    parsed_feed = recipe.parse(content, site_url)
    if not parsed_feed.title.strip():
        raise ValueError(f"{recipe.name} recipe returned an empty feed title")
    if not parsed_feed.items:
        raise ValueError(f"{recipe.name} recipe returned no feed items")
    if any(not item.title.strip() for item in parsed_feed.items):
        raise ValueError(f"{recipe.name} recipe returned an item without a title")
    return parsed_feed


def render_webpage_feed_rss(recipe: WebpageFeedRecipe, site_url: str, content: str) -> str:
    parsed_feed = parse_webpage_feed(recipe, content, site_url)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = parsed_feed.title
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = parsed_feed.description or parsed_feed.title

    for feed_item in parsed_feed.items:
        item_url = _approved_item_url(recipe, feed_item.url)
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = feed_item.title
        ET.SubElement(item, "link").text = item_url
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = item_url
        if feed_item.published_at:
            ET.SubElement(item, "pubDate").text = recipe.format_published_at(feed_item.published_at)

        body_parts = []
        image = image_html(
            _approved_optional_image_url(recipe, feed_item.image_url),
            allowed_hosts=set(recipe.allowed_image_hosts),
        )
        if image:
            body_parts.append(image)
        for label, values in feed_item.details.items():
            clean_values = [value for value in values if value]
            if clean_values:
                body_parts.append(
                    f"<p>{html_text(label)}: {html_text(', '.join(clean_values))}</p>"
                )
        ET.SubElement(item, "description").text = "".join(body_parts) or feed_item.title

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def _path_matches(path: str, prefix: str) -> bool:
    normalized_prefix = "/" + prefix.strip("/")
    normalized_path = "/" + path.strip("/")
    return normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")


def _approved_item_url(recipe: WebpageFeedRecipe, value: str) -> str:
    approved = safe_https_url(value, allowed_hosts=set(recipe.allowed_item_hosts))
    parsed = urlparse(approved)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if not approved or port is not None or parsed.fragment or parsed.params:
        raise ValueError(f"{recipe.name} recipe returned an unsupported item URL")
    return approved


def _approved_optional_image_url(recipe: WebpageFeedRecipe, value: str) -> str:
    approved = safe_https_url(value, allowed_hosts=set(recipe.allowed_image_hosts))
    parsed = urlparse(approved)
    try:
        port = parsed.port
    except ValueError:
        return ""
    if not approved or port is not None or parsed.fragment or parsed.params:
        return ""
    return approved
