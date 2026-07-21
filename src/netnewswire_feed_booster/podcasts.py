from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from .feed_store import Source, source_id_from_title
from .http_client import fetch_text


def podcast_source_from_url(feed_or_url: str, title: str, profile: str, group: str = "Podcasts") -> Source:
    value = feed_or_url.strip()
    if is_apple_podcast_url(value):
        feed_url, resolved_title = resolve_apple_podcast_feed(value)
        return Source(
            id=source_id_from_title(title or resolved_title, feed_url),
            title=title or resolved_title,
            feed_url=feed_url,
            site_url=value,
            kind="podcast",
            profiles=[profile],
            groups=[group],
            source="apple-podcasts-url",
        )

    resolved_title = title or fetch_feed_title(value) or title_from_url(value)
    return Source(
        id=source_id_from_title(resolved_title, value),
        title=resolved_title,
        feed_url=value,
        site_url="",
        kind="podcast",
        profiles=[profile],
        groups=[group],
        source="manual-podcast-rss",
    )


def is_apple_podcast_url(value: str) -> bool:
    return "podcasts.apple.com" in value and "/id" in value


def resolve_apple_podcast_feed(url: str) -> tuple[str, str]:
    match = re.search(r"/id(\d+)", url)
    if not match:
        parsed = urlparse(url)
        return openrss_feed_url(parsed.netloc + parsed.path), title_from_url(url)

    lookup_url = "https://itunes.apple.com/lookup?id={}&entity=podcast".format(match.group(1))
    payload = json.loads(fetch_text(lookup_url))
    result = payload.get("results", [{}])[0] if payload.get("results") else {}
    feed_url = result.get("feedUrl")
    title = result.get("collectionName") or title_from_url(url)
    if feed_url:
        return feed_url, title
    parsed = urlparse(url)
    return openrss_feed_url(parsed.netloc + parsed.path), title


def fetch_feed_title(feed_url: str) -> str:
    try:
        text = fetch_text(feed_url)
        root = ET.fromstring(text)
    except Exception:
        return ""

    channel_title = root.findtext("./channel/title")
    if channel_title:
        return channel_title.strip()

    atom_title = root.findtext("{http://www.w3.org/2005/Atom}title")
    return atom_title.strip() if atom_title else ""


def title_from_url(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.strip("/").split("/")
    if "podcast" in path:
        index = path.index("podcast")
        if index + 1 < len(path):
            return path[index + 1].replace("-", " ").title()
    basename = path[-1] if path else parsed.netloc
    return basename.replace("-", " ").replace("_", " ").title() or parsed.netloc


def openrss_feed_url(path_or_url: str) -> str:
    parsed = urlparse(path_or_url)
    if parsed.netloc:
        feed_path = (parsed.netloc + parsed.path).strip("/")
    else:
        feed_path = path_or_url.replace("https://", "").replace("http://", "").strip("/")
    return f"https://openrss.org/feed/{feed_path}"
