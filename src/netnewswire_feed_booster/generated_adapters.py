from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from .feed_store import Source


FetchText = Callable[[str], str]


@dataclass(frozen=True)
class GeneratedAdapter:
    """One public source shape the bridge is allowed to fetch and transform."""

    name: str
    source_label: str
    hosted_route: str
    allowed_hosts: frozenset[str]
    allowed_suffixes: frozenset[str]
    matches: Callable[[Source], bool]
    upstream_url: Callable[[Source], str]
    render: Callable[[Source, str], str]

    def validate(self, source: Source) -> None:
        if not self.matches(source):
            raise ValueError(f"{source.id} is not a valid {self.name} source")
        # Calling upstream_url performs adapter-specific URL-shape validation.
        self.upstream_url(source)


def _require_https_path(source: Source, hosts: set[str], path_prefix: str) -> None:
    parsed = urlparse(source.site_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or hostname not in hosts
        or not parsed.path.startswith(path_prefix)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(f"Unsupported {source.source} URL: {source.site_url}")


def _bandcamp_matches(source: Source) -> bool:
    if source.kind != "bandcamp":
        return False
    parsed = urlparse(source.site_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and (hostname == "bandcamp.com" or hostname.endswith(".bandcamp.com"))
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _bandcamp_upstream_url(source: Source) -> str:
    from .hosted_bandcamp import bandcamp_fetch_url

    if not _bandcamp_matches(source):
        raise ValueError(f"Unsupported Bandcamp source URL: {source.site_url}")
    return bandcamp_fetch_url(source)


def _render_bandcamp(source: Source, content: str) -> str:
    from .hosted_bandcamp import render_bandcamp_source_rss

    return render_bandcamp_source_rss(source, fetcher=lambda _: content)


def _nts_matches(source: Source) -> bool:
    try:
        _require_https_path(source, {"nts.live", "www.nts.live"}, "/shows/")
    except ValueError:
        return False
    return source.source == "nts-local-generated"


def _nts_upstream_url(source: Source) -> str:
    _require_https_path(source, {"nts.live", "www.nts.live"}, "/shows/")
    return source.site_url


def _render_nts(source: Source, content: str) -> str:
    from .nts import render_nts_show_rss

    return render_nts_show_rss(source.site_url, fetcher=lambda _: content)


def _hydefm_matches(source: Source) -> bool:
    if source.source != "radio-local-generated":
        return False
    try:
        _require_https_path(source, {"hydefm.com", "www.hydefm.com"}, "/archives")
    except ValueError:
        return False
    return True


def _hydefm_upstream_url(source: Source) -> str:
    from .hydefm import hydefm_text_mirror_url

    if not _hydefm_matches(source):
        raise ValueError(f"Unsupported HydeFM archive URL: {source.site_url}")
    return hydefm_text_mirror_url(source.site_url)


def _render_hydefm(source: Source, content: str) -> str:
    from .hydefm import render_hydefm_archive_rss

    return render_hydefm_archive_rss(source.site_url, fetcher=lambda _: content)


def _mixcloud_matches(source: Source) -> bool:
    if source.source != "mixcloud-local-generated":
        return False
    parsed = urlparse(source.site_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path_parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and hostname in {"mixcloud.com", "www.mixcloud.com"}
        and len(path_parts) == 1
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _mixcloud_upstream_url(source: Source) -> str:
    from .mixcloud import mixcloud_cloudcasts_url

    if not _mixcloud_matches(source):
        raise ValueError(f"Unsupported Mixcloud profile URL: {source.site_url}")
    return mixcloud_cloudcasts_url(source.site_url)


def _render_mixcloud(source: Source, content: str) -> str:
    import json

    from .mixcloud import render_mixcloud_profile_rss

    return render_mixcloud_profile_rss(source.site_url, fetcher=lambda _: json.loads(content))


BANDCAMP_ADAPTER = GeneratedAdapter(
    name="Bandcamp",
    source_label="bandcamp-local-generated",
    hosted_route="bandcamp",
    allowed_hosts=frozenset({"bandcamp.com"}),
    allowed_suffixes=frozenset({"bandcamp.com"}),
    matches=_bandcamp_matches,
    upstream_url=_bandcamp_upstream_url,
    render=_render_bandcamp,
)
NTS_ADAPTER = GeneratedAdapter(
    name="NTS",
    source_label="nts-local-generated",
    hosted_route="generated",
    allowed_hosts=frozenset({"nts.live", "www.nts.live"}),
    allowed_suffixes=frozenset(),
    matches=_nts_matches,
    upstream_url=_nts_upstream_url,
    render=_render_nts,
)
HYDEFM_ADAPTER = GeneratedAdapter(
    name="HydeFM",
    source_label="radio-local-generated",
    hosted_route="generated",
    allowed_hosts=frozenset({"r.jina.ai"}),
    allowed_suffixes=frozenset(),
    matches=_hydefm_matches,
    upstream_url=_hydefm_upstream_url,
    render=_render_hydefm,
)
MIXCLOUD_ADAPTER = GeneratedAdapter(
    name="Mixcloud",
    source_label="mixcloud-local-generated",
    hosted_route="generated",
    allowed_hosts=frozenset({"api.mixcloud.com"}),
    allowed_suffixes=frozenset(),
    matches=_mixcloud_matches,
    upstream_url=_mixcloud_upstream_url,
    render=_render_mixcloud,
)

GENERATED_ADAPTERS = (BANDCAMP_ADAPTER, NTS_ADAPTER, HYDEFM_ADAPTER, MIXCLOUD_ADAPTER)
GENERATED_SOURCE_LABELS = frozenset(adapter.source_label for adapter in GENERATED_ADAPTERS)


def adapter_for_source(source: Source) -> Optional[GeneratedAdapter]:
    for adapter in GENERATED_ADAPTERS:
        if adapter.matches(source):
            return adapter
    return None


def hosted_route_for_source(source: Source) -> Optional[str]:
    adapter = adapter_for_source(source)
    return adapter.hosted_route if adapter else None
