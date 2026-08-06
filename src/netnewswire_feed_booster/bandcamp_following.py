from __future__ import annotations

import ipaddress
import re
import time
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from .bandcamp import extract_bandcamp_pagedata
from .feed_store import Source, slugify, unique_id
from .http_client import fetch_json_post, fetch_text


BANDCAMP_FOLLOWING_BANDS_API_URL = "https://bandcamp.com/api/fancollection/1/following_bands"
BANDCAMP_FOLLOWING_FANS_API_URL = "https://bandcamp.com/api/fancollection/1/following_fans"
BANDCAMP_FOLLOWING_PAGE_HOSTS = frozenset({"bandcamp.com", "www.bandcamp.com"})
FOLLOWING_PAGINATION_PAUSE_SECONDS = 0.5
MAX_FOLLOWING_PAGES = 500  # backstop: ~10,000 items at the default batch size of 20

_SUBDOMAIN_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HOSTNAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")


def _valid_bandcamp_subdomain(value: str) -> str:
    """Return the subdomain if it's a single, well-formed DNS label; otherwise "".

    Bandcamp's following-list API reports this as a bare label, e.g. "kinodisk" —
    never a full domain, path, or anything containing "/". Validate it before
    interpolating into a URL: an unvalidated subdomain containing "/" would break
    the intended {subdomain}.bandcamp.com host boundary and silently produce a
    different real host (e.g. "evil.example.com/x" -> site_url host becomes
    evil.example.com, a syntactically valid but entirely unintended target).
    """
    value = (value or "").strip().lower()
    return value if _SUBDOMAIN_LABEL_PATTERN.fullmatch(value) else ""


def _valid_bandcamp_hostname(value: str) -> str:
    """Return the hostname if it looks like a real, non-IP-literal domain; otherwise ""."""
    value = (value or "").strip().lower().rstrip(".")
    if not value or "." not in value:
        return ""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return ""
    return value if _HOSTNAME_PATTERN.fullmatch(value) else ""


def _valid_bandcamp_fan_url(value: str) -> str:
    """Return trackpipe_url if it's a well-formed https URL on bandcamp.com; otherwise "".

    Unlike artist/label storefronts, individual fan profiles never live on a
    custom domain — only bandcamp.com/<username> — so this is intentionally
    narrower than _valid_bandcamp_hostname's custom-domain allowance.
    """
    value = (value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        return ""
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "bandcamp.com" or hostname.endswith(".bandcamp.com"):
        return value
    return ""


def _default_page_fetcher(url: str) -> str:
    return fetch_text(url, allowed_hosts=BANDCAMP_FOLLOWING_PAGE_HOSTS)


def import_bandcamp_following(
    profile_url: str,
    profile: str,
    out_dir: Path,
    group: str = "Bandcamp",
    fetcher: Callable[[str], str] = _default_page_fetcher,
    post_page: Optional[Callable[[str, int, str, int], dict]] = None,
) -> List[Source]:
    """Import everyone a Bandcamp fan account follows: artists/labels and other fans.

    Each result is registered without fetching that artist's own page — the title
    and URL come straight from the following-list API, so importing 1000+ follows
    costs a handful of requests, not one per artist. The stored feed_url points at
    where its RSS will live once generated; run refresh-bandcamp-local-feeds
    afterward to actually populate it (that command already batches and paces
    itself for exactly this many sources).

    The two lists are fetched in sequence, not as an all-or-nothing unit: if the
    (usually much larger) artists/labels list succeeds and the fans list then
    fails, the already-fetched bands are still returned — losing a completed,
    possibly-1000+-item fetch because a second, smaller one failed afterward
    would be a worse outcome than a partial import. A failure on the first fetch
    still raises normally, since nothing has been gained yet at that point.
    """
    base_url = profile_url.rstrip("/")
    bands_html = fetcher(f"{base_url}/following/artists_and_labels")
    bands = fetch_bandcamp_following_bands(bands_html, profile=profile, group=group, out_dir=out_dir, post_page=post_page)

    try:
        fans_html = fetcher(f"{base_url}/following/fans")
        fans = fetch_bandcamp_following_fans(fans_html, profile=profile, group=group, out_dir=out_dir, post_page=post_page)
    except Exception as error:
        print(
            f"WARNING\tImported {len(bands)} artists/labels, but could not import followed fans: "
            f"{type(error).__name__}: {error}"
        )
        return bands

    return bands + fans


def fetch_bandcamp_following_bands(
    html: str,
    profile: str,
    group: str,
    out_dir: Path,
    post_page: Optional[Callable[[str, int, str, int], dict]] = None,
) -> List[Source]:
    raw_items = _paginate_bandcamp_following(
        html,
        data_key="following_bands_data",
        cache_key="following_bands",
        api_url=BANDCAMP_FOLLOWING_BANDS_API_URL,
        post_page=post_page,
    )
    sources = [bandcamp_following_band_source(item, profile=profile, group=group, out_dir=out_dir) for item in raw_items]
    resolved = _resolve_id_collisions([source for source in sources if source], out_dir)
    return sorted(resolved, key=lambda source: source.title.lower())


def fetch_bandcamp_following_fans(
    html: str,
    profile: str,
    group: str,
    out_dir: Path,
    post_page: Optional[Callable[[str, int, str, int], dict]] = None,
) -> List[Source]:
    raw_items = _paginate_bandcamp_following(
        html,
        data_key="following_fans_data",
        cache_key="following_fans",
        api_url=BANDCAMP_FOLLOWING_FANS_API_URL,
        post_page=post_page,
    )
    sources = [bandcamp_following_fan_source(item, profile=profile, group=group, out_dir=out_dir) for item in raw_items]
    resolved = _resolve_id_collisions([source for source in sources if source], out_dir)
    return sorted(resolved, key=lambda source: source.title.lower())


def _resolve_id_collisions(sources: List[Source], out_dir: Path) -> List[Source]:
    """Give each source a unique id when two different accounts share a display name.

    subscribe-bandcamp-source ids purely off the title, with no numeric suffix, so
    keep that scheme here too — it's what lets a following-list import merge into
    an artist you already added manually. Only diverge (append -2, -3, ...) when
    this batch genuinely has two different accounts collapsing to the same slug;
    real-world case: entirely different artists sharing a name, or a name that
    doesn't survive slugify (symbols-only, non-Latin) and falls back to "bandcamp".
    """
    seen_ids: set[str] = set()
    for source in sources:
        resolved_id = unique_id(source.id, seen_ids)
        seen_ids.add(resolved_id)
        if resolved_id != source.id:
            source.id = resolved_id
            source.feed_url = (out_dir / f"{resolved_id}.rss").resolve().as_uri()
    return sources


def _paginate_bandcamp_following(
    html: str,
    data_key: str,
    cache_key: str,
    api_url: str,
    post_page: Optional[Callable[[str, int, str, int], dict]],
) -> List[dict]:
    blob = extract_bandcamp_pagedata(html)
    fan_id = blob.get("fan_data", {}).get("fan_id")
    following_data = blob.get(data_key) or {}
    if not isinstance(fan_id, int) or not isinstance(following_data, dict):
        return []

    cache = blob.get("item_cache", {}).get(cache_key) or {}
    items: dict[str, dict] = dict(cache) if isinstance(cache, dict) else {}

    item_count = int(following_data.get("item_count") or 0)
    batch_size = int(following_data.get("batch_size") or 20)
    token = str(following_data.get("last_token") or "").strip()
    poster = post_page or _post_bandcamp_following_page

    pages_fetched = 0
    while token and len(items) < item_count:
        if pages_fetched >= MAX_FOLLOWING_PAGES:
            # Backstop only: termination is otherwise entirely dependent on this
            # undocumented API correctly reporting item_count/more_available/
            # last_token. A misbehaving or malformed response (repeating the same
            # token, or an item_count that never gets reached) would otherwise
            # loop forever. 500 pages is far beyond any real following list this
            # session has seen (1093 items took ~55 pages) — hitting it means
            # something is wrong upstream, not that there's more legitimate data.
            print(f"WARNING\tBandcamp following-list pagination stopped after {MAX_FOLLOWING_PAGES} pages; results may be incomplete")
            break
        payload = poster(api_url, fan_id, token, batch_size)
        pages_fetched += 1
        for item in payload.get("followeers") or []:
            key = str(item.get("band_id") or item.get("fan_id") or "")
            if key:
                items[key] = item
        token = str(payload.get("last_token") or "").strip()
        more_available = bool(payload.get("more_available"))
        if not more_available:
            break
        time.sleep(FOLLOWING_PAGINATION_PAUSE_SECONDS)

    return list(items.values())


def _post_bandcamp_following_page(api_url: str, fan_id: int, older_than_token: str, count: int) -> dict:
    return fetch_json_post(
        api_url,
        {"fan_id": fan_id, "older_than_token": older_than_token, "count": count},
        allowed_hosts=BANDCAMP_FOLLOWING_PAGE_HOSTS,
    )


def bandcamp_following_band_source(item: dict, profile: str, group: str, out_dir: Path) -> Optional[Source]:
    name = str(item.get("name") or "").strip()
    url_hints = item.get("url_hints") or {}
    subdomain = _valid_bandcamp_subdomain(str(url_hints.get("subdomain") or ""))
    custom_domain = _valid_bandcamp_hostname(str(url_hints.get("custom_domain") or ""))
    if not name or not (subdomain or custom_domain):
        return None
    site_url = f"https://{custom_domain}" if custom_domain else f"https://{subdomain}.bandcamp.com"
    return _bandcamp_following_source(name=name, site_url=site_url, is_fan=False, profile=profile, group=group, out_dir=out_dir)


def bandcamp_following_fan_source(item: dict, profile: str, group: str, out_dir: Path) -> Optional[Source]:
    name = str(item.get("name") or "").strip()
    site_url = _valid_bandcamp_fan_url(str(item.get("trackpipe_url") or ""))
    if not name or not site_url:
        return None
    return _bandcamp_following_source(name=name, site_url=site_url, is_fan=True, profile=profile, group=group, out_dir=out_dir)


def _bandcamp_following_source(name: str, site_url: str, is_fan: bool, profile: str, group: str, out_dir: Path) -> Source:
    source_title = f"Bandcamp Fan: {name}" if is_fan else f"Bandcamp: {name}"
    source_id = slugify(source_title)
    out_path = out_dir / f"{source_id}.rss"
    return Source(
        id=source_id,
        title=source_title,
        feed_url=out_path.resolve().as_uri(),
        site_url=site_url,
        kind="bandcamp",
        profiles=[profile],
        groups=[group],
        source="bandcamp-following-import",
        notes="Queued from your Bandcamp following list; run refresh-bandcamp-local-feeds to generate its RSS.",
    )
