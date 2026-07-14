from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

from .feed_store import Source
from .rss_safety import ensure_rss_channel
from .http_client import fetch_text


HTML_ALTERNATE_PATTERN = re.compile(
    r"<link\b(?P<attrs>[^>]*\brel=[\"'][^\"']*\balternate\b[^>]*>)",
    re.IGNORECASE | re.DOTALL,
)
HTML_ATTR_PATTERN = re.compile(r"(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)", re.DOTALL)
FEED_MIME_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/json",
}


@dataclass
class FeedValidationResult:
    source_id: str
    title: str
    url: str
    status: str
    feed_type: str = ""
    detail: str = ""
    discovered_url: str = ""


def detect_feed_type(text: str) -> str:
    sample = text.lstrip("\ufeff \t\r\n")
    if not sample:
        return "empty"
    if sample.startswith("{"):
        try:
            payload = json.loads(sample)
        except json.JSONDecodeError:
            return "unknown"
        if isinstance(payload, dict) and str(payload.get("version", "")).startswith("https://jsonfeed.org/version/"):
            return "json"
        return "unknown"
    if sample.lower().startswith("<!doctype html") or sample.lower().startswith("<html"):
        return "html"
    if sample.startswith("<"):
        try:
            root = ET.fromstring(sample)
        except ET.ParseError:
            lowered = sample[:200].lower()
            if "<rss" in lowered:
                return "malformed-rss"
            if "<feed" in lowered:
                return "malformed-atom"
            return "malformed-xml"
        tag = _local_name(root.tag)
        if tag == "rss":
            return "rss"
        if tag == "feed":
            return "atom"
        if tag == "html":
            return "html"
        return "xml"
    return "unknown"


def validate_feed_text(text: str) -> tuple[str, str]:
    feed_type = detect_feed_type(text)
    if feed_type == "rss":
        ensure_rss_channel(text)
        return feed_type, "ok"
    if feed_type == "atom":
        return feed_type, "ok"
    if feed_type == "json":
        return feed_type, "ok"
    if feed_type.startswith("malformed"):
        raise ValueError(feed_type)
    raise ValueError(f"not a feed: {feed_type}")


def discover_alternate_feed_links(html: str, base_url: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in HTML_ALTERNATE_PATTERN.finditer(html):
        attrs = _attrs(match.group("attrs"))
        href = attrs.get("href", "").strip()
        mime_type = attrs.get("type", "").strip().lower()
        if not href or mime_type not in FEED_MIME_TYPES:
            continue
        absolute = urljoin(base_url, href)
        if absolute not in seen:
            links.append(absolute)
            seen.add(absolute)
    return links


def discover_feed_url(url: str, fetcher: Callable[[str], str] = fetch_text) -> str:
    text = fetcher(url)
    feed_type = detect_feed_type(text)
    if feed_type in {"rss", "atom", "json"}:
        return url
    if feed_type != "html":
        raise ValueError(f"No discoverable feed for {url}: {feed_type}")
    links = discover_alternate_feed_links(text, url)
    if not links:
        raise ValueError(f"No alternate feed links found for {url}")
    return links[0]


def audit_source(source: Source, fetcher: Callable[[str], str] = fetch_text) -> FeedValidationResult:
    try:
        text = _read_source_text(source.feed_url, fetcher=fetcher)
        feed_type, detail = validate_feed_text(text)
        return FeedValidationResult(source.id, source.title, source.feed_url, "ok", feed_type, detail)
    except Exception as error:
        discovered_url = ""
        if source.site_url:
            try:
                discovered_url = discover_feed_url(source.site_url, fetcher=fetcher)
            except Exception:
                discovered_url = ""
        return FeedValidationResult(
            source.id,
            source.title,
            source.feed_url,
            "error",
            detect_feed_type(text) if "text" in locals() else "",
            f"{type(error).__name__}: {error}",
            discovered_url,
        )


def audit_sources(sources: Iterable[Source], fetcher: Callable[[str], str] = fetch_text) -> list[FeedValidationResult]:
    return [audit_source(source, fetcher=fetcher) for source in sources]


def _read_source_text(url: str, fetcher: Callable[[str], str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(parsed.path).read_text(encoding="utf-8", errors="replace")
    if parsed.scheme in {"http", "https"}:
        return fetcher(url)
    raise ValueError(f"Unsupported feed URL scheme: {parsed.scheme}")


def _attrs(raw: str) -> dict[str, str]:
    return {match.group("name").lower(): match.group("value") for match in HTML_ATTR_PATTERN.finditer(raw)}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()
