from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Callable, List
from urllib.parse import urljoin

from .feed_store import Source, source_id_from_title
from .rss_safety import html_text, image_html


BANDCAMP_COLLECTION_API_URL = "https://bandcamp.com/api/fancollection/1/collection_items"
BANDCAMP_USER_AGENT = "netnewswire-feed-booster/0.1"


@dataclass
class BandcampCollectionItem:
    title: str
    artist: str
    url: str
    item_type: str
    collected_at: str = ""
    artwork_url: str = ""
    featured_track_title: str = ""


@dataclass
class BandcampCollectionPagination:
    fan_id: int
    older_than_token: str
    count: int


def parse_bandcamp_collection_html(html: str) -> List[BandcampCollectionItem]:
    blob = extract_bandcamp_pagedata(html)
    collection = blob.get("item_cache", {}).get("collection", {})
    if not isinstance(collection, dict):
        return []

    return _parse_bandcamp_collection_items(collection.values())


def fetch_bandcamp_collection_items(
    html: str,
    max_items: int | None = None,
    post_collection_page: Callable[[int, str, int], dict] | None = None,
) -> List[BandcampCollectionItem]:
    items = parse_bandcamp_collection_html(html)
    if max_items is not None and len(items) >= max_items:
        return items[:max_items]

    pagination = extract_bandcamp_collection_pagination(html)
    if not pagination:
        return items

    post_collection_page = post_collection_page or post_bandcamp_collection_page
    token = pagination.older_than_token
    while token:
        count = pagination.count
        if max_items is not None:
            count = min(count, max(max_items - len(_dedupe_items(items)), 0))
            if count == 0:
                break

        payload = post_collection_page(pagination.fan_id, token, count)
        page_items = payload.get("items") or []
        if isinstance(page_items, list):
            items.extend(_parse_bandcamp_collection_items(page_items))

        token = str(payload.get("last_token") or "").strip()
        if not payload.get("more_available"):
            break

    sorted_items = sorted(_dedupe_items(items), key=_item_sort_key, reverse=True)
    return sorted_items[:max_items] if max_items is not None else sorted_items


def extract_bandcamp_collection_pagination(html: str) -> BandcampCollectionPagination | None:
    blob = extract_bandcamp_pagedata(html)
    fan_id = blob.get("fan_data", {}).get("fan_id")
    collection_data = blob.get("collection_data", {})
    if not isinstance(fan_id, int) or not isinstance(collection_data, dict):
        return None

    older_than_token = str(collection_data.get("last_token") or "").strip()
    item_count = int(collection_data.get("item_count") or 0)
    batch_size = int(collection_data.get("batch_size") or 20)
    embedded_count = len(blob.get("item_cache", {}).get("collection", {}) or {})
    if not older_than_token or item_count <= embedded_count:
        return None

    return BandcampCollectionPagination(fan_id=fan_id, older_than_token=older_than_token, count=batch_size)


def post_bandcamp_collection_page(fan_id: int, older_than_token: str, count: int) -> dict:
    payload = json.dumps(
        {
            "fan_id": fan_id,
            "older_than_token": older_than_token,
            "count": count,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        BANDCAMP_COLLECTION_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": BANDCAMP_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_bandcamp_collection_items(raw_items: object) -> List[BandcampCollectionItem]:
    items: List[BandcampCollectionItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        collection_item = _bandcamp_collection_item_from_payload(item)
        if collection_item:
            items.append(collection_item)

    return sorted(items, key=_item_sort_key, reverse=True)


def _bandcamp_collection_item_from_payload(item: dict) -> BandcampCollectionItem | None:
    title = str(item.get("item_title") or "").strip()
    artist = str(item.get("band_name") or "").strip()
    url = str(item.get("item_url") or "").strip()
    if not title or not artist or not url:
        return None

    artwork_id = item.get("item_art_id")
    artwork_url = f"https://f4.bcbits.com/img/a{artwork_id}_10.jpg" if artwork_id else ""
    return BandcampCollectionItem(
        title=title,
        artist=artist,
        url=url,
        item_type=str(item.get("item_type") or "release"),
        collected_at=str(item.get("purchased") or item.get("added") or item.get("updated") or ""),
        artwork_url=artwork_url,
        featured_track_title=str(item.get("featured_track_title") or ""),
    )


def parse_bandcamp_artist_music_html(html: str, site_url: str) -> List[BandcampCollectionItem]:
    match = re.search(r'data-client-items="([^"]+)"', html, re.DOTALL)
    if not match:
        return parse_bandcamp_legacy_music_grid_html(html, site_url)

    payload = json.loads(unescape(match.group(1)))
    if not isinstance(payload, list):
        return parse_bandcamp_legacy_music_grid_html(html, site_url)

    items: List[BandcampCollectionItem] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        page_url = str(item.get("page_url") or "").strip()
        if not title or not page_url:
            continue

        artwork_id = item.get("art_id")
        artwork_url = f"https://f4.bcbits.com/img/a{artwork_id}_10.jpg" if artwork_id else ""
        items.append(
            BandcampCollectionItem(
                title=title,
                artist=artist,
                url=urljoin(f"{site_url.rstrip('/')}/", page_url),
                item_type=str(item.get("type") or "release"),
                artwork_url=artwork_url,
            )
        )

    return _dedupe_items(items)


def parse_bandcamp_legacy_music_grid_html(html: str, site_url: str) -> List[BandcampCollectionItem]:
    artist = extract_meta_content(html, "og:title") or title_from_html(html)
    items: List[BandcampCollectionItem] = []
    pattern = re.compile(
        r'<li\b(?P<attrs>[^>]*)class="[^"]*\bmusic-grid-item\b[^"]*"[^>]*>\s*'
        r'<a href="(?P<href>[^"]+)">(?P<body>.*?)</a>\s*</li>',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        attrs = match.group("attrs")
        body = match.group("body")
        title_match = re.search(r'<p class="title">\s*(?P<title>.*?)\s*</p>', body, re.DOTALL)
        href = unescape(match.group("href")).strip()
        title = clean_html_text(title_match.group("title")) if title_match else ""
        if not title or not href:
            continue

        item_id_match = re.search(r'data-item-id="(?P<item_type>album|track)-', attrs)
        item_type = item_id_match.group("item_type") if item_id_match else "release"
        artwork_match = re.search(r'<img[^>]+src="(?P<src>[^"]+)"', body)
        artwork_url = unescape(artwork_match.group("src")).strip() if artwork_match else ""
        items.append(
            BandcampCollectionItem(
                title=title,
                artist=artist,
                url=urljoin(f"{site_url.rstrip('/')}/", href),
                item_type=item_type,
                artwork_url=artwork_url,
            )
        )

    if items:
        return _dedupe_items(items)
    return parse_bandcamp_tralbum_html(html)


def parse_bandcamp_tralbum_html(html: str) -> List[BandcampCollectionItem]:
    title = ""
    artist = extract_meta_content(html, "og:site_name")
    url = extract_meta_content(html, "og:url")
    artwork_url = extract_meta_content(html, "og:image")
    item_type = extract_meta_content(html, "og:type") or "release"
    published_at = ""

    match = re.search(r'data-tralbum="([^"]+)"', html, re.DOTALL)
    if match:
        payload = json.loads(unescape(match.group(1)))
        current = payload.get("current", {}) if isinstance(payload, dict) else {}
        if isinstance(current, dict):
            title = str(current.get("title") or "").strip()
            artist = str(current.get("artist") or "").strip() or artist
            published_at = str(current.get("publish_date") or current.get("new_date") or "").strip()
        if isinstance(payload, dict):
            url = str(payload.get("url") or "").strip() or url
            artist = str(payload.get("artist") or "").strip() or artist
            payload_item_type = str(payload.get("item_type") or "").strip()
            if payload_item_type:
                item_type = _bandcamp_item_type(payload_item_type)

    if not title:
        og_title = extract_meta_content(html, "og:title")
        title = og_title.split(", by ", 1)[0].strip() if og_title else ""
    if not title or not artist or not url:
        return []

    return [
        BandcampCollectionItem(
            title=title,
            artist=artist,
            url=url,
            item_type=item_type,
            collected_at=published_at,
            artwork_url=artwork_url,
        )
    ]


def extract_bandcamp_pagedata(html: str) -> dict:
    match = re.search(r'<div id="pagedata" data-blob="([^"]+)"', html, re.DOTALL)
    if not match:
        raise ValueError("Could not find Bandcamp page data")
    return json.loads(unescape(match.group(1)))


def bandcamp_source(profile_url: str, feed_path: Path, title: str, profile: str, group: str) -> Source:
    return Source(
        id=source_id_from_title(title, profile_url),
        title=title,
        feed_url=feed_path.resolve().as_uri(),
        site_url=profile_url.rstrip("/"),
        kind="bandcamp",
        profiles=[profile],
        groups=[group],
        source="bandcamp-generated-feed",
        notes="Generated local RSS feed from the public Bandcamp collection page because Bandcamp does not expose a native RSS endpoint for this fan profile.",
    )


def write_bandcamp_collection_rss(path: Path, profile_url: str, title: str, items: List[BandcampCollectionItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_bandcamp_collection_rss(profile_url=profile_url, title=title, items=items), encoding="utf-8")


def render_bandcamp_collection_rss(profile_url: str, title: str, items: List[BandcampCollectionItem]) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = profile_url
    ET.SubElement(channel, "description").text = f"Latest visible collection items from {profile_url}"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for collection_item in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"{collection_item.artist} - {collection_item.title}"
        ET.SubElement(item, "link").text = collection_item.url
        guid = ET.SubElement(item, "guid", {"isPermaLink": "true"})
        guid.text = collection_item.url
        description_parts = [f"{html_text(collection_item.item_type.title())} by {html_text(collection_item.artist)}."]
        if collection_item.featured_track_title:
            description_parts.append(f"Featured track: {html_text(collection_item.featured_track_title)}.")
        image = image_html(
            collection_item.artwork_url,
            allowed_hosts={"f4.bcbits.com"},
            allowed_suffixes={"bcbits.com"},
        )
        if image:
            description_parts.append(f"<p>{image}</p>")
        ET.SubElement(item, "description").text = " ".join(description_parts)
        pub_date = rss_pubdate(collection_item.collected_at)
        if pub_date:
            ET.SubElement(item, "pubDate").text = pub_date

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def rss_pubdate(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return format_datetime(parsed)


def _bandcamp_item_type(value: str) -> str:
    if value == "a":
        return "album"
    if value == "t":
        return "track"
    return value or "release"


def _dedupe_items(items: List[BandcampCollectionItem]) -> List[BandcampCollectionItem]:
    deduped: dict[str, BandcampCollectionItem] = {}
    for item in items:
        deduped[item.url] = item
    return list(deduped.values())


def extract_meta_content(html: str, property_name: str) -> str:
    match = re.search(rf'<meta property="{re.escape(property_name)}" content="([^"]*)"', html, re.IGNORECASE)
    return unescape(match.group(1)).strip() if match else ""


def title_from_html(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return clean_html_text(match.group(1)).replace("Music | ", "").strip()


def clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _item_sort_key(item: BandcampCollectionItem) -> datetime:
    try:
        parsed = parsedate_to_datetime(item.collected_at)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
