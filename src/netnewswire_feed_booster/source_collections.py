from __future__ import annotations

from typing import Optional

from .feed_store import FeedStore, Source, normalize_url


REDACTED_FEED_URL = "[redacted; use --show-sensitive]"


def display_feed_url(source: Source, show_sensitive: bool = False) -> str:
    return source.feed_url if show_sensitive else REDACTED_FEED_URL


def active_sources_with_private(store: FeedStore, private_store: FeedStore, profile: str) -> list[Source]:
    return unique_sources(store.active_sources(profile) + private_store.active_sources(profile))


def filtered_sources_with_private(
    store: FeedStore,
    private_store: FeedStore,
    profile: Optional[str],
    status: Optional[str],
    kind: Optional[str],
) -> list[Source]:
    return unique_sources(
        store.filtered(profile=profile, status=status, kind=kind)
        + private_store.filtered(profile=profile, status=status, kind=kind)
    )


def unique_sources(sources: list[Source]) -> list[Source]:
    by_feed_url: dict[str, Source] = {}
    for source in sources:
        by_feed_url[source.feed_url] = source
    return sorted(by_feed_url.values(), key=lambda source: source.title.lower())


def netnewswire_drift_report(
    current_sources: list[Source],
    expected_sources: list[Source],
    unsubscribed_sources: list[Source],
) -> dict[str, list[Source]]:
    expected_urls = {source.feed_url for source in expected_sources}
    current_urls = {source.feed_url for source in current_sources}
    unsubscribed_urls = {source.feed_url for source in unsubscribed_sources}

    stale_file_bandcamp = [
        source
        for source in current_sources
        if source.feed_url.startswith("file://") and "/exports/bandcamp/" in source.feed_url
    ]
    tokenless_modal = [
        source
        for source in current_sources
        if "/feeds/bandcamp/" in source.feed_url or "/feeds/generated/" in source.feed_url
    ]
    unsubscribed = [source for source in current_sources if source.feed_url in unsubscribed_urls]
    classified_urls = {source.feed_url for source in stale_file_bandcamp + tokenless_modal + unsubscribed}
    unexpected = [
        source
        for source in current_sources
        if source.feed_url not in expected_urls and source.feed_url not in classified_urls
    ]
    missing = [source for source in expected_sources if source.feed_url not in current_urls]

    return {
        "missing": sorted(missing, key=lambda source: source.title.lower()),
        "unexpected": sorted(unexpected, key=lambda source: source.title.lower()),
        "stale_file_bandcamp": sorted(stale_file_bandcamp, key=lambda source: source.title.lower()),
        "tokenless_modal": sorted(tokenless_modal, key=lambda source: source.title.lower()),
        "unsubscribed": sorted(unsubscribed, key=lambda source: source.title.lower()),
    }


def drift_has_failures(drift: dict[str, list[Source]]) -> bool:
    return any(drift.values())


def print_drift_report(drift: dict[str, list[Source]], show_sensitive: bool = False) -> None:
    if not drift_has_failures(drift):
        print("NetNewsWire subscriptions match the hosted OPML export.")
        return

    print("NetNewsWire subscription drift detected:")
    for name, sources in drift.items():
        print(f"{name}: {len(sources)}")
        for source in sources[:20]:
            print(f"  {source.title}\t{display_feed_url(source, show_sensitive)}")
        if len(sources) > 20:
            print(f"  ... {len(sources) - 20} more")


def resolve_source_identifier(sources: list[Source], identifier: str, profile: str) -> Source:
    value = identifier.strip()
    normalized_url = normalize_url(value).rstrip("/")
    candidates = [
        source
        for source in sources
        if profile in source.profiles
        and (
            source.id == value
            or source.feed_url.rstrip("/") == normalized_url
            or source.site_url.rstrip("/") == normalized_url
            or source.title.lower() == value.lower()
        )
    ]
    if not candidates:
        raise KeyError(f"No source found for exact identifier: {identifier}")
    unique_by_id = {source.id: source for source in candidates}
    if len(unique_by_id) > 1:
        matches = ", ".join(sorted(unique_by_id))
        raise ValueError(f"Identifier is ambiguous: {identifier}. Matches: {matches}")
    return next(iter(unique_by_id.values()))
