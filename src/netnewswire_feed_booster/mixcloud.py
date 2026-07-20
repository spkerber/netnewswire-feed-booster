from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import format_datetime
from typing import Callable
from urllib.parse import urlparse

from .feed_store import Source, slugify
from .http_client import fetch_json
from .rss_safety import html_text


FetchJson = Callable[[str], dict]


def mixcloud_cloudcasts_url(profile_url: str, limit: int = 100) -> str:
    parsed = urlparse(profile_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"mixcloud.com", "www.mixcloud.com"}:
        raise ValueError(f"Unsupported Mixcloud profile URL: {profile_url}")
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 1 or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"Missing Mixcloud username: {profile_url}")
    username = path_parts[0]
    return f"https://api.mixcloud.com/{username}/cloudcasts/?limit={limit}"


def mixcloud_source(profile_url: str, profile: str, group: str = "Mixcloud") -> Source:
    username = urlparse(profile_url).path.strip("/").split("/")[0]
    return Source(
        id=slugify(f"mixcloud {username}"),
        title=f"Mixcloud: {username}",
        feed_url="",
        site_url=f"https://www.mixcloud.com/{username}/",
        kind="other",
        profiles=[profile],
        groups=[group],
        source="mixcloud-local-generated",
        notes="Generated RSS feed from Mixcloud's public cloudcasts API.",
    )


def render_mixcloud_profile_rss(profile_url: str, fetcher: FetchJson = fetch_json) -> str:
    payload = fetcher(mixcloud_cloudcasts_url(profile_url))
    items = payload.get("data", [])
    if not items:
        raise ValueError(f"No Mixcloud uploads found for {profile_url}")
    username = urlparse(profile_url).path.strip("/").split("/")[0]
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"Mixcloud: {username}"
    ET.SubElement(channel, "link").text = profile_url
    ET.SubElement(channel, "description").text = f"Mixcloud uploads from {username}."
    for item in items:
        title = str(item.get("name") or "Mixcloud upload")
        url = str(item.get("url") or "")
        if not url:
            continue
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, "title").text = title
        ET.SubElement(entry, "link").text = url
        ET.SubElement(entry, "guid", {"isPermaLink": "true"}).text = url
        if item.get("created_time"):
            ET.SubElement(entry, "pubDate").text = format_datetime(datetime.fromisoformat(str(item["created_time"]).replace("Z", "+00:00")))
        ET.SubElement(entry, "description").text = html_text(str(item.get("description") or ""))
    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"
