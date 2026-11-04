from __future__ import annotations

import re
from dataclasses import replace
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set
from urllib.parse import urlparse

from .bandcamp import (
    PostBandDetails,
    add_bandcamp_release_dates,
    fetch_bandcamp_collection_items,
    parse_bandcamp_artist_music_html,
    render_bandcamp_collection_rss,
)
from .feed_store import Source
from .http_client import fetch_text
from .generated_adapters import hosted_route_for_source


FetchText = Callable[[str], str]

_BOT_CHECK_TITLE = re.compile(r"<title[^>]*>\s*Client Challenge\s*</title>", re.IGNORECASE)
# The path of a URL built by hosted_generated_feed_url or hosted_status_feed_url,
# under any base path.
_HOSTED_FEED_PATH = re.compile(r"(?P<prefix>.*/feeds/)[^/]+/(?P<feed>status\.rss|generated/(?P<source_id>[^/]+)\.rss)")


class BandcampBotCheck(ValueError):
    """Bandcamp answered with its "Client Challenge" page instead of content.

    The page arrives as HTTP 200, so without this check it parses as a page with
    no releases. Bandcamp answers every request from a blocked network the same
    way, so the right response is to stop requesting from it, never to get past
    the check.
    """


def is_bandcamp_bot_check(html: str) -> bool:
    return bool(_BOT_CHECK_TITLE.search(html))


def hosted_generated_feed_url(base_url: str, source_id: str, token: str = "") -> str:
    base = base_url.rstrip("/")
    token = token.strip("/")
    if not token:
        raise ValueError("Hosted generated feed URLs require a token path segment")
    return f"{base}/feeds/{token}/generated/{source_id}.rss"


def hosted_status_feed_url(base_url: str, token: str) -> str:
    """The bridge's own status feed: what it needs from its owner, read in the reader."""
    token = token.strip("/")
    if not token:
        raise ValueError("The hosted status feed URL requires a token path segment")
    return f"{base_url.rstrip('/')}/feeds/{token}/status.rss"


def _hosted_feed_match(feed_url: str) -> Optional[re.Match]:
    parsed = urlparse(feed_url)
    if parsed.scheme not in ("http", "https"):
        return None
    return _HOSTED_FEED_PATH.fullmatch(parsed.path)


def hosted_generated_source_id(feed_url: str) -> Optional[str]:
    """The source id in a hosted generated feed URL, read from its shape alone.

    Any token is accepted, so a reader's subscriptions can be matched to the
    registry without knowing the bridge's token.
    """
    match = _hosted_feed_match(feed_url)
    return match.group("source_id") if match else None


def is_hosted_status_feed_url(feed_url: str) -> bool:
    match = _hosted_feed_match(feed_url)
    return bool(match) and match.group("feed") == "status.rss"


def without_feed_token(feed_url: str) -> str:
    """Mask the token in a hosted feed URL; any other URL comes back unchanged."""
    match = _hosted_feed_match(feed_url)
    if not match:
        return feed_url
    path = f"{match.group('prefix')}[token]/{match.group('feed')}"
    return urlparse(feed_url)._replace(path=path).geturl()


def sources_with_hosted_generated_feeds(sources: Iterable[Source], base_url: str, token: str = "") -> List[Source]:
    """Rewrite every recognized generated source (Bandcamp included) to its hosted URL."""
    rewritten: List[Source] = []
    for source in sources:
        if hosted_route_for_source(source) == "generated":
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
    max_items: Optional[int] = 50,
    post_band_details: Optional[PostBandDetails] = None,
) -> list:
    """Pass `post_band_details` to date an artist's releases (one extra request per artist)."""
    if is_bandcamp_bot_check(html):
        raise BandcampBotCheck(f"Bandcamp served a bot check instead of {source.id}: {source.site_url}")
    if is_bandcamp_artist_source(source):
        items = parse_bandcamp_artist_music_html(html, source.site_url)
        if post_band_details is not None:
            items = add_bandcamp_release_dates(items, html, post_band_details)
            # Page order is the artist's own and often not chronological, so
            # once dates are known the cap keeps the newest releases. Undated
            # items follow in page order; with no dates at all, nothing moves.
            items = sorted(items, key=lambda item: (not item.collected_at, _negated_timestamp(item.collected_at)))
        return items[:max_items] if max_items is not None else items

    fan_limit = max_items if source.id in (full_fan_source_ids or set()) else fan_max_items
    if max_items is not None and fan_limit is not None:
        fan_limit = min(fan_limit, max_items)
    return fetch_bandcamp_collection_items(html, max_items=fan_limit)


def render_bandcamp_source_rss(
    source: Source,
    fetcher: FetchText = fetch_text,
    fan_max_items: Optional[int] = 40,
    full_fan_source_ids: Optional[Set[str]] = None,
    max_items: Optional[int] = 50,
    post_band_details: Optional[PostBandDetails] = None,
) -> str:
    html = fetcher(bandcamp_fetch_url(source))
    items = bandcamp_items_for_source(
        source,
        html,
        fan_max_items=fan_max_items,
        full_fan_source_ids=full_fan_source_ids,
        max_items=max_items,
        post_band_details=post_band_details,
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
    max_items: Optional[int] = 50,
    post_band_details: Optional[PostBandDetails] = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_bandcamp_source_rss(
            source,
            fetcher=fetcher,
            fan_max_items=fan_max_items,
            full_fan_source_ids=full_fan_source_ids,
            max_items=max_items,
            post_band_details=post_band_details,
        ),
        encoding="utf-8",
    )


def _negated_timestamp(value: str) -> float:
    if not value:
        return 0.0
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return -parsed.timestamp()
