from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set
from urllib.parse import urlparse

from .bandcamp import (
    fetch_bandcamp_collection_items,
    parse_bandcamp_artist_music_html,
    render_bandcamp_collection_rss,
)
from .feed_store import Source
from .http_client import fetch_text


FetchText = Callable[[str], str]


def hosted_bandcamp_feed_url(base_url: str, source_id: str, token: str = "") -> str:
    base = base_url.rstrip("/")
    token = token.strip("/")
    if not token:
        raise ValueError("Hosted Bandcamp feed URLs require a token path segment")
    return f"{base}/feeds/{token}/bandcamp/{source_id}.rss"


def hosted_generated_feed_url(base_url: str, source_id: str, token: str = "") -> str:
    base = base_url.rstrip("/")
    token = token.strip("/")
    if not token:
        raise ValueError("Hosted generated feed URLs require a token path segment")
    return f"{base}/feeds/{token}/generated/{source_id}.rss"


def sources_with_hosted_bandcamp_feeds(sources: Iterable[Source], base_url: str, token: str = "") -> List[Source]:
    rewritten: List[Source] = []
    for source in sources:
        if source.kind == "bandcamp":
            rewritten.append(replace(source, feed_url=hosted_bandcamp_feed_url(base_url, source.id, token=token)))
        elif source.source in {"nts-local-generated", "radio-local-generated"}:
            rewritten.append(replace(source, feed_url=hosted_generated_feed_url(base_url, source.id, token=token)))
        else:
            rewritten.append(source)
    return rewritten


def bandcamp_fetch_url(source: Source) -> str:
    site_url = source.site_url.rstrip("/")
    if is_bandcamp_artist_source(source) and not site_url.endswith("/music"):
        return f"{site_url}/music"
    return site_url


def is_bandcamp_artist_source(source: Source) -> bool:
    parsed = urlparse(source.site_url)
    if parsed.netloc.lower() == "bandcamp.com":
        return False
    if source.id.startswith("bandcamp-fan-") or source.title.lower().startswith("bandcamp fan:"):
        return False
    return source.kind == "bandcamp"


def bandcamp_items_for_source(
    source: Source,
    html: str,
    fan_max_items: Optional[int] = 40,
    full_fan_source_ids: Optional[Set[str]] = None,
) -> list:
    if is_bandcamp_artist_source(source):
        return parse_bandcamp_artist_music_html(html, source.site_url)

    max_items = None if source.id in (full_fan_source_ids or set()) else fan_max_items
    return fetch_bandcamp_collection_items(html, max_items=max_items)


def render_bandcamp_source_rss(
    source: Source,
    fetcher: FetchText = fetch_text,
    fan_max_items: Optional[int] = 40,
    full_fan_source_ids: Optional[Set[str]] = None,
) -> str:
    html = fetcher(bandcamp_fetch_url(source))
    items = bandcamp_items_for_source(
        source,
        html,
        fan_max_items=fan_max_items,
        full_fan_source_ids=full_fan_source_ids,
    )
    if not items:
        raise ValueError(f"No Bandcamp items found for {source.id}: {source.site_url}")
    return render_bandcamp_collection_rss(profile_url=source.site_url, title=source.title, items=items)


def write_bandcamp_source_rss(
    source: Source,
    out_path: Path,
    fetcher: FetchText = fetch_text,
    fan_max_items: Optional[int] = 40,
    full_fan_source_ids: Optional[Set[str]] = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_bandcamp_source_rss(
            source,
            fetcher=fetcher,
            fan_max_items=fan_max_items,
            full_fan_source_ids=full_fan_source_ids,
        ),
        encoding="utf-8",
    )
