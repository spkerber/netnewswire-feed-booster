from __future__ import annotations

import re
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from .feed_store import Source, slugify
from .http_client import fetch_json, fetch_text


# SoundCloud's public pages and API. Restricting fetches to these hosts means a
# redirect (or a manipulated next_href in a followings response) can't carry a
# fetch somewhere unexpected — the same posture every GeneratedAdapter already has.
SOUNDCLOUD_PAGE_HOSTS = frozenset({"soundcloud.com", "www.soundcloud.com"})
SOUNDCLOUD_API_HOSTS = frozenset({"api-v2.soundcloud.com"})


def _default_page_fetcher(url: str) -> str:
    return fetch_text(url, allowed_hosts=SOUNDCLOUD_PAGE_HOSTS)


def _default_api_json_fetcher(url: str) -> dict[str, Any]:
    return fetch_json(url, allowed_hosts=SOUNDCLOUD_API_HOSTS)


def fetch_soundcloud_following_sources(
    profile_url: str,
    profile: str,
    group: str = "SoundCloud",
    fetcher: Callable[[str], str] = _default_page_fetcher,
    json_fetcher: Callable[[str], dict[str, Any]] = _default_api_json_fetcher,
) -> list[Source]:
    html = fetcher(profile_url.rstrip("/") + "/following")
    user_id = extract_soundcloud_user_id(html)
    client_id = extract_soundcloud_api_client_id(html)
    app_version = extract_soundcloud_app_version(html)
    sources: list[Source] = []
    seen_feed_urls: set[str] = set()
    next_url = soundcloud_followings_api_url(user_id, client_id, app_version=app_version)
    while next_url:
        payload = json_fetcher(next_url)
        for user in payload.get("collection", []):
            source = soundcloud_user_source(user, profile=profile, group=group, source_label="soundcloud-following-import")
            if source and source.feed_url not in seen_feed_urls:
                seen_feed_urls.add(source.feed_url)
                sources.append(source)
        next_url = str(payload.get("next_href") or "")
        if next_url and "client_id=" not in next_url:
            separator = "&" if "?" in next_url else "?"
            next_url = f"{next_url}{separator}{urlencode({'client_id': client_id})}"
    return sorted(sources, key=lambda source: source.title.lower())


def fetch_soundcloud_profile_source(
    profile_url: str,
    profile: str,
    group: str = "SoundCloud",
    fetcher: Callable[[str], str] = _default_page_fetcher,
    json_fetcher: Callable[[str], dict[str, Any]] = _default_api_json_fetcher,
) -> Source:
    """Resolve a single public SoundCloud profile URL to a Source, independent of anyone's following list."""
    normalized_url = profile_url.rstrip("/")
    html = fetcher(normalized_url)
    client_id = extract_soundcloud_api_client_id(html)
    app_version = extract_soundcloud_app_version(html)
    user = json_fetcher(soundcloud_resolve_api_url(normalized_url, client_id, app_version=app_version))
    source = soundcloud_user_source(user, profile=profile, group=group, source_label="soundcloud-profile-oneoff")
    if not source:
        raise ValueError(f"Could not resolve a SoundCloud user from {profile_url}")
    return source


def soundcloud_resolve_api_url(profile_url: str, client_id: str, app_version: str = "") -> str:
    query = {"url": profile_url, "client_id": client_id, "app_locale": "en"}
    if app_version:
        query["app_version"] = app_version
    return f"https://api-v2.soundcloud.com/resolve?{urlencode(query)}"


def extract_soundcloud_user_id(html: str) -> str:
    match = re.search(r"soundcloud://users:(\d+)", html) or re.search(r'"urn"\s*:\s*"soundcloud:users:(\d+)"', html)
    if not match:
        raise ValueError("Could not find SoundCloud user ID")
    return match.group(1)


def extract_soundcloud_api_client_id(html: str) -> str:
    match = re.search(r'"hydratable"\s*:\s*"apiClient"\s*,\s*"data"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', html)
    if not match:
        raise ValueError("Could not find SoundCloud API client ID")
    return match.group(1)


def extract_soundcloud_app_version(html: str) -> str:
    match = re.search(r'window\.__sc_version\s*=\s*"([^"]+)"', html)
    return match.group(1) if match else ""


def soundcloud_followings_api_url(user_id: str, client_id: str, limit: int = 200, offset: int = 0, app_version: str = "") -> str:
    query = {"client_id": client_id, "limit": str(limit), "offset": str(offset), "linked_partitioning": "1", "app_locale": "en"}
    if app_version:
        query["app_version"] = app_version
    return f"https://api-v2.soundcloud.com/users/{user_id}/followings?{urlencode(query)}"


SOUNDCLOUD_SOURCE_NOTES = {
    "soundcloud-following-import": "SoundCloud public profile RSS feed derived from the account following list.",
    "soundcloud-profile-oneoff": "SoundCloud public profile RSS feed added directly from the profile URL.",
}


def soundcloud_user_source(
    user: dict[str, Any],
    profile: str,
    group: str,
    source_label: str = "soundcloud-manual",
) -> Optional[Source]:
    user_id = str(user.get("id") or "").strip()
    username = str(user.get("username") or user.get("permalink") or user_id).strip()
    permalink_url = str(user.get("permalink_url") or "").strip().rstrip("/")
    if not user_id or not username or not permalink_url:
        return None
    return Source(
        id=slugify(f"SoundCloud {username} {user_id}"),
        title=f"SoundCloud: {username}",
        feed_url=f"https://feeds.soundcloud.com/users/soundcloud:users:{user_id}/sounds.rss",
        site_url=permalink_url,
        kind="podcast",
        profiles=[profile],
        groups=[group],
        source=source_label,
        notes=SOUNDCLOUD_SOURCE_NOTES.get(
            source_label, "SoundCloud public profile RSS feed derived from the account following list."
        ),
    )
