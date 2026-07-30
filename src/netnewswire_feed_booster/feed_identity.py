from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid"}


@dataclass(frozen=True)
class FeedIdentity:
    title: str
    home_url: str
    self_url: str
    item_ids: frozenset[str]
    provider: str = ""


def canonical_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return value
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower().rstrip(".")
    port = parsed.port
    netloc = hostname if port in {None, 80 if scheme == "http" else 443} else f"{hostname}:{port}"
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
            and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
        )
    )
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalized_title(value: str) -> str:
    return " ".join(value.casefold().split())


def parse_feed_identity(text: str, fallback_url: str = "") -> FeedIdentity:
    sample = text.lstrip("\ufeff \t\r\n")
    if sample.startswith("{"):
        return _parse_json_feed_identity(sample, fallback_url)
    root = ET.fromstring(sample)
    local_name = _local_name(root.tag)
    if local_name == "rss":
        return _parse_rss_identity(root, fallback_url)
    if local_name == "feed":
        return _parse_atom_identity(root, fallback_url)
    raise ValueError(f"Unsupported feed document: {local_name}")


def identity_match_reason(left: FeedIdentity, right: FeedIdentity) -> str:
    if left.self_url and left.self_url == right.self_url:
        return "canonical feed URL"
    if left.home_url and left.home_url == right.home_url:
        return "canonical publication URL"
    if left.item_ids & right.item_ids:
        return "overlapping stable item IDs"
    return ""


def likely_same_title(left: FeedIdentity, right: FeedIdentity) -> bool:
    return bool(left.title and right.title and normalized_title(left.title) == normalized_title(right.title))


def _parse_rss_identity(root: ET.Element, fallback_url: str) -> FeedIdentity:
    channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
    if channel is None:
        raise ValueError("RSS feed has no channel")
    title = _child_text(channel, "title")
    home_url = canonical_url(_child_text(channel, "link"))
    self_url = canonical_url(_atom_link(channel, "self") or fallback_url)
    item_ids = set()
    for item in channel:
        if _local_name(item.tag) != "item":
            continue
        stable_id = _child_text(item, "guid") or _child_text(item, "link")
        if stable_id:
            item_ids.add(canonical_url(stable_id))
    generator = _child_text(channel, "generator")
    return FeedIdentity(title, home_url, self_url, frozenset(item_ids), _provider(generator, self_url, home_url))


def _parse_atom_identity(root: ET.Element, fallback_url: str) -> FeedIdentity:
    title = _child_text(root, "title")
    home_url = canonical_url(_atom_link(root, "alternate"))
    self_url = canonical_url(_atom_link(root, "self") or fallback_url)
    item_ids = set()
    for entry in root:
        if _local_name(entry.tag) != "entry":
            continue
        stable_id = _child_text(entry, "id") or _atom_link(entry, "alternate")
        if stable_id:
            item_ids.add(canonical_url(stable_id))
    generator = _child_text(root, "generator")
    return FeedIdentity(title, home_url, self_url, frozenset(item_ids), _provider(generator, self_url, home_url))


def _parse_json_feed_identity(text: str, fallback_url: str) -> FeedIdentity:
    payload = json.loads(text)
    if not isinstance(payload, dict) or not str(payload.get("version", "")).startswith("https://jsonfeed.org/version/"):
        raise ValueError("Unsupported JSON feed")
    self_url = canonical_url(str(payload.get("feed_url") or fallback_url))
    home_url = canonical_url(str(payload.get("home_page_url") or ""))
    item_ids = {
        canonical_url(str(item.get("id") or item.get("url") or ""))
        for item in payload.get("items", [])
        if isinstance(item, dict) and (item.get("id") or item.get("url"))
    }
    return FeedIdentity(
        str(payload.get("title") or "").strip(),
        home_url,
        self_url,
        frozenset(item_ids),
        _provider(str(payload.get("generator") or ""), self_url, home_url),
    )


def _provider(generator: str, *urls: str) -> str:
    haystack = " ".join((generator, *urls)).lower()
    if "substack" in haystack:
        return "substack"
    if "youtube.com/feeds/videos.xml" in haystack:
        return "youtube"
    return ""


def _child_text(parent: ET.Element, name: str) -> str:
    for child in parent:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _atom_link(parent: ET.Element, relation: str) -> str:
    for child in parent:
        if _local_name(child.tag) != "link":
            continue
        rel = child.attrib.get("rel", "alternate")
        href = child.attrib.get("href", "")
        if rel == relation and href:
            return href.strip()
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()
