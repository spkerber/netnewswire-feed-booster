from __future__ import annotations

import csv
import json
import re
from html import unescape
from pathlib import Path
from typing import Iterable, Optional

from .feed_store import Source, slugify


def parse_youtube_channel_html(html: str, profile: str, group: str, fallback_title: str = "") -> Source:
    rss_match = re.search(r'<link rel="alternate" type="application/rss\+xml" title="RSS" href="([^"]+)"', html)
    if not rss_match:
        raise ValueError("Could not find YouTube RSS alternate link")
    feed_url = unescape(rss_match.group(1))
    channel_id = youtube_channel_id_from_feed(feed_url)
    title = fallback_title or extract_meta_content(html, "og:title") or channel_id
    return Source(
        id=slugify(title),
        title=title,
        feed_url=feed_url,
        site_url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else "",
        kind="youtube",
        profiles=[profile],
        groups=[group],
        source="youtube-channel-page",
    )


def parse_youtube_subscriptions_file(path: Path, profile: str, group: str) -> list[Source]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".html", ".htm"}:
        return parse_youtube_subscriptions_html(text, profile=profile, group=group)
    if looks_like_csv(text):
        return parse_youtube_subscriptions_csv(text, profile=profile, group=group)
    return parse_youtube_subscription_lines(text.splitlines(), profile=profile, group=group)


def parse_youtube_subscriptions_html(html: str, profile: str, group: str) -> list[Source]:
    sources: list[Source] = []
    seen_channel_ids: set[str] = set()
    pattern = re.compile(
        r'"channelRenderer":\{'
        r'.*?"channelId":"(?P<channel_id>UC[a-zA-Z0-9_-]{10,})"'
        r'.*?"title":\{"simpleText":"(?P<title>.*?)"\}'
        r'.*?"canonicalBaseUrl":"(?P<canonical_url>/@[^"]+)"',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        channel_id = match.group("channel_id")
        if channel_id in seen_channel_ids:
            continue
        seen_channel_ids.add(channel_id)
        source = youtube_source_from_parts(
            channel_id=channel_id,
            title=json_unescape(match.group("title")),
            channel_url=f"https://www.youtube.com{json_unescape(match.group('canonical_url'))}",
            profile=profile,
            group=group,
        )
        if source:
            sources.append(source)
    return sources


def parse_youtube_subscriptions_csv(text: str, profile: str, group: str) -> list[Source]:
    sources: list[Source] = []
    for row in csv.DictReader(text.splitlines()):
        normalized = {key.strip().lower().replace(" ", "_"): value.strip() for key, value in row.items() if key}
        title = normalized.get("channel_title") or normalized.get("title") or normalized.get("name") or normalized.get("channel") or ""
        channel_url = normalized.get("channel_url") or normalized.get("url") or ""
        source = youtube_source_from_parts(
            channel_id=normalized.get("channel_id") or normalized.get("channelid") or youtube_channel_id_from_url(channel_url),
            title=title,
            channel_url=channel_url,
            profile=profile,
            group=group,
        )
        if source:
            sources.append(source)
    return sources


def parse_youtube_subscription_lines(lines: Iterable[str], profile: str, group: str) -> list[Source]:
    sources: list[Source] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        title = ""
        if "\t" in value:
            value, title = [part.strip() for part in value.split("\t", 1)]
        source = youtube_source_from_parts(
            channel_id=youtube_channel_id_from_url(value) or (value if value.startswith("UC") else ""),
            title=title,
            channel_url=value,
            profile=profile,
            group=group,
        )
        if source:
            sources.append(source)
    return sources


def extract_meta_content(html: str, property_name: str) -> str:
    match = re.search(rf'<meta property="{re.escape(property_name)}" content="([^"]*)"', html, re.IGNORECASE)
    return unescape(match.group(1)).strip() if match else ""


def looks_like_csv(text: str) -> bool:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return "," in first_line and any(label in first_line.lower() for label in ["channel", "title", "url"])


def youtube_source_from_parts(
    channel_id: str,
    title: str,
    channel_url: str,
    profile: str,
    group: str,
) -> Optional[Source]:
    if not channel_id:
        return None
    title = title or channel_id
    site_url = channel_url if channel_url.startswith("http") else f"https://www.youtube.com/channel/{channel_id}"
    return Source(
        id=slugify(f"YouTube {title} {channel_id}"),
        title=title,
        feed_url=f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        site_url=site_url,
        kind="youtube",
        profiles=[profile],
        groups=[group],
        source="youtube-subscriptions-import",
    )


def youtube_channel_id_from_feed(feed_url: str) -> str:
    match = re.search(r"[?&]channel_id=([^&]+)", feed_url)
    return match.group(1) if match else ""


def youtube_channel_id_from_url(url: str) -> str:
    match = re.search(r"(?:youtube\.com/channel/|^)(UC[a-zA-Z0-9_-]{10,})", url)
    return match.group(1) if match else ""


def json_unescape(value: str) -> str:
    return json.loads(f'"{value}"')
