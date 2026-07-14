from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Callable, List
from urllib.parse import urlparse

from .rss_safety import html_text, image_html
from .http_client import fetch_text


FetchText = Callable[[str], str]
DEFAULT_ARCHIVE_URL = "https://hydefm.com/archives/"


@dataclass
class HydeFMArchiveItem:
    title: str
    url: str
    published_at: str
    image_url: str = ""
    genres: List[str] | None = None


def hydefm_text_mirror_url(site_url: str = DEFAULT_ARCHIVE_URL) -> str:
    parsed = urlparse(site_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or hostname not in {"hydefm.com", "www.hydefm.com"} or not parsed.path.startswith("/archives"):
        raise ValueError(f"Unsupported HydeFM archive URL: {site_url}")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return f"https://r.jina.ai/http://{parsed.netloc}{path}"


def fetch_hydefm_archive_markdown(site_url: str = DEFAULT_ARCHIVE_URL, fetcher: FetchText = fetch_text) -> str:
    return fetcher(hydefm_text_mirror_url(site_url))


def parse_hydefm_archive_markdown(markdown: str, site_url: str = DEFAULT_ARCHIVE_URL) -> tuple[str, str, List[HydeFMArchiveItem]]:
    title = "HydeFM Archives"
    description = f"Generated RSS feed from {site_url}."
    items: List[HydeFMArchiveItem] = []
    pending_image = ""
    pending_date = ""
    pending_genres: List[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        image_match = re.search(r"!\[[^\]]*\]\((https://[^)]+)\)", line)
        if image_match and "hydefmradio-archive.s3" in image_match.group(1):
            pending_image = image_match.group(1)
            continue

        date_match = re.match(r"## ([A-Z][a-z]+ \d{1,2}, \d{4})$", line)
        if date_match:
            pending_date = date_match.group(1)
            pending_genres = []
            continue

        genres = re.findall(r"\[([^\]]+)\]\(https?://hydefm\.com/genres/[^)]+\)", line)
        if genres:
            if items and items[-1].published_at == pending_date and not items[-1].genres:
                items[-1].genres = list(dict.fromkeys(genres))
            else:
                pending_genres.extend(genres)
            continue

        item_match = re.match(r"## \[([^\]]+)\]\((https?://hydefm\.com/archive/[^)]+)\)", line)
        if item_match:
            items.append(
                HydeFMArchiveItem(
                    title=item_match.group(1),
                    url=item_match.group(2),
                    published_at=pending_date,
                    image_url=pending_image,
                    genres=list(dict.fromkeys(pending_genres)),
                )
            )
            pending_genres = []

    if not items:
        raise ValueError(f"Could not find HydeFM archive items for {site_url}")
    return title, description, items


def render_hydefm_archive_rss(site_url: str = DEFAULT_ARCHIVE_URL, fetcher: FetchText = fetch_text) -> str:
    markdown = fetch_hydefm_archive_markdown(site_url, fetcher=fetcher)
    title, description, items = parse_hydefm_archive_markdown(markdown, site_url=site_url)
    return render_hydefm_rss(site_url=site_url, title=title, description=description, items=items)


def write_hydefm_archive_rss(out_path: Path, site_url: str = DEFAULT_ARCHIVE_URL, fetcher: FetchText = fetch_text) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_hydefm_archive_rss(site_url, fetcher=fetcher), encoding="utf-8")


def render_hydefm_rss(site_url: str, title: str, description: str, items: List[HydeFMArchiveItem]) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = description

    for archive_item in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = archive_item.title
        ET.SubElement(item, "link").text = archive_item.url
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = archive_item.url
        if archive_item.published_at:
            ET.SubElement(item, "pubDate").text = hydefm_pubdate(archive_item.published_at)
        body_parts = []
        image = image_html(
            archive_item.image_url,
            allowed_hosts={"hydefmradio-archive.s3.us-west-1.amazonaws.com"},
        )
        if image:
            body_parts.append(image)
        if archive_item.genres:
            body_parts.append(f"<p>Genres: {html_text(', '.join(archive_item.genres))}</p>")
        ET.SubElement(item, "description").text = "".join(body_parts) or archive_item.title

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def hydefm_pubdate(value: str) -> str:
    published = datetime.strptime(value, "%B %d, %Y").replace(tzinfo=timezone.utc)
    return format_datetime(published)
