from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import re
from typing import Callable, Optional
from urllib.parse import urlparse

from .feed_store import Source


FetchText = Callable[[str], str]
SourceHostPolicy = Callable[[Source], frozenset[str]]


def configured_bandcamp_redirect_hosts(value: Optional[str] = None) -> frozenset[str]:
    """Return exact, privately configured custom-domain redirect hosts."""

    raw = os.environ.get("BANDCAMP_CUSTOM_DOMAINS", "") if value is None else value
    hosts: set[str] = set()
    for candidate in raw.split(","):
        host = candidate.strip().lower().rstrip(".")
        if not host:
            continue
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host:
            raise ValueError(f"Invalid BANDCAMP_CUSTOM_DOMAINS hostname: {candidate}")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError(f"BANDCAMP_CUSTOM_DOMAINS must contain hostnames, not IP addresses: {candidate}")
        hosts.add(host)
    return frozenset(hosts)


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
    legacy_source_labels: frozenset[str] = frozenset()
    source_allowed_hosts: Optional[SourceHostPolicy] = None
    source_allowed_suffixes: Optional[SourceHostPolicy] = None

    def validate(self, source: Source) -> None:
        if not self.matches(source):
            raise ValueError(f"{source.id} is not a valid {self.name} source")
        # Calling upstream_url performs adapter-specific URL-shape validation.
        self.upstream_url(source)

    @property
    def source_labels(self) -> frozenset[str]:
        return frozenset({self.source_label}) | self.legacy_source_labels

    def allowed_hosts_for(self, source: Source) -> frozenset[str]:
        if self.source_allowed_hosts:
            return self.source_allowed_hosts(source)
        return self.allowed_hosts

    def allowed_suffixes_for(self, source: Source) -> frozenset[str]:
        if self.source_allowed_suffixes:
            return self.source_allowed_suffixes(source)
        return self.allowed_suffixes


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


def _is_plausible_custom_domain_hostname(hostname: str) -> bool:
    """A hostname that could plausibly be a real custom-domain storefront: not an
    IP literal, has at least one dot, and matches a conservative DNS-label shape
    (same bar as configured_bandcamp_redirect_hosts). Shared by _bandcamp_matches
    and _bandcamp_source_allowed_hosts so the trust decision doesn't depend on one
    of them having already been called — a Source can reach the fetch layer
    without going through adapter.validate() first (refresh-bandcamp-local-feeds
    does exactly that), so the function that grants fetch access has to be able to
    stand on its own.
    """
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return False
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname)) and "." in hostname


def _bandcamp_matches(source: Source) -> bool:
    if source.kind != "bandcamp":
        return False
    parsed = urlparse(source.site_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    if hostname == "bandcamp.com" or hostname.endswith(".bandcamp.com"):
        return True
    # Custom-domain storefronts: Bandcamp's own following-list API reports these
    # directly for a specific followed artist (url_hints.custom_domain), so a
    # non-bandcamp.com host here was already resolved at subscribe/import time by
    # build_bandcamp_source_from_url or bandcamp_following_band_source — this is
    # trusting our own already-vetted registry, not arbitrary user input. Still
    # require a plausible hostname shape.
    return _is_plausible_custom_domain_hostname(hostname)


def _bandcamp_upstream_url(source: Source) -> str:
    from .hosted_bandcamp import bandcamp_fetch_url

    if not _bandcamp_matches(source):
        raise ValueError(f"Unsupported Bandcamp source URL: {source.site_url}")
    return bandcamp_fetch_url(source)


def _render_bandcamp(source: Source, content: str) -> str:
    from .hosted_bandcamp import render_bandcamp_source_rss

    return render_bandcamp_source_rss(source, fetcher=lambda _: content)


def _bandcamp_source_allowed_hosts(source: Source) -> frozenset[str]:
    """Trust this source's own site_url host for its own fetch, on top of the
    global bandcamp.com allowlist — covers custom-domain storefronts without a
    manually maintained list. This validates the hostname itself (same shape
    check as _bandcamp_matches) rather than assuming some other code path already
    did: refresh-bandcamp-local-feeds calls this directly without ever calling
    adapter.validate() first, and a Bandcamp-kind Source can also be constructed
    by the generic `add` command with no Bandcamp-specific vetting at all. A
    redirect to a DIFFERENT, unexpected host during the fetch is still blocked,
    since only this one validated host is added.
    """
    hostname = (urlparse(source.site_url).hostname or "").lower().rstrip(".")
    base = frozenset({"bandcamp.com"}) | configured_bandcamp_redirect_hosts()
    if hostname.endswith(".bandcamp.com") or _is_plausible_custom_domain_hostname(hostname):
        return base | {hostname}
    return base


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


def _webpage_matches(source: Source) -> bool:
    if source.source not in {"webpage-local-generated", "radio-local-generated"}:
        return False
    from .webpage_recipes import webpage_recipe_for_url

    return webpage_recipe_for_url(source.site_url) is not None


def _webpage_upstream_url(source: Source) -> str:
    from .webpage_feeds import recipe_fetch_url
    from .webpage_recipes import require_webpage_recipe

    if not _webpage_matches(source):
        raise ValueError(f"Unsupported webpage recipe URL: {source.site_url}")
    recipe = require_webpage_recipe(source.site_url)
    return recipe_fetch_url(recipe, source.site_url)


def _render_webpage(source: Source, content: str) -> str:
    from .webpage_feeds import render_webpage_feed_rss
    from .webpage_recipes import require_webpage_recipe

    recipe = require_webpage_recipe(source.site_url)
    return render_webpage_feed_rss(recipe, source.site_url, content)


def _webpage_allowed_hosts(source: Source) -> frozenset[str]:
    from .webpage_recipes import require_webpage_recipe

    return require_webpage_recipe(source.site_url).allowed_fetch_hosts


def _hydefm_compat_matches(source: Source) -> bool:
    from .webpage_recipes import HYDEFM_ARCHIVE_RECIPE, webpage_recipe_for_url

    recipe = webpage_recipe_for_url(source.site_url)
    return (
        source.source in {"radio-local-generated", "webpage-local-generated"}
        and recipe is not None
        and recipe.id == HYDEFM_ARCHIVE_RECIPE.id
    )


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
    # Bandcamp shares the "generated" route and cache with every other adapter.
    # It still needs its own refresh path (see modal_bandcamp_app.py and
    # refresh-bandcamp-local-feeds): fan-collection pagination, a fan item
    # cap, and a full-fan override list don't fit the shared render(source,
    # content) -> str signature. Dispatch on adapter identity (is BANDCAMP_ADAPTER),
    # not on hosted_route, wherever that distinction matters.
    hosted_route="generated",
    # Base allowlist for sources without their own custom domain. Bandcamp storefronts
    # on a custom domain are handled per-source below (source_allowed_hosts), not by
    # growing this global set — see _bandcamp_source_allowed_hosts for why that's safe.
    allowed_hosts=frozenset({"bandcamp.com"}) | configured_bandcamp_redirect_hosts(),
    allowed_suffixes=frozenset({"bandcamp.com"}),
    matches=_bandcamp_matches,
    upstream_url=_bandcamp_upstream_url,
    render=_render_bandcamp,
    source_allowed_hosts=_bandcamp_source_allowed_hosts,
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
WEBPAGE_ADAPTER = GeneratedAdapter(
    name="webpage recipe",
    source_label="webpage-local-generated",
    hosted_route="generated",
    allowed_hosts=frozenset(),
    allowed_suffixes=frozenset(),
    matches=_webpage_matches,
    upstream_url=_webpage_upstream_url,
    render=_render_webpage,
    legacy_source_labels=frozenset({"radio-local-generated"}),
    source_allowed_hosts=_webpage_allowed_hosts,
)
# Compatibility for callers written against v0.1.0. Keep the old adapter name
# and static fetch-policy fields bounded to HydeFM; new code should use
# WEBPAGE_ADAPTER.allowed_hosts_for(source).
HYDEFM_ADAPTER = GeneratedAdapter(
    name="HydeFM",
    source_label="radio-local-generated",
    hosted_route="generated",
    allowed_hosts=frozenset({"hydefm.com", "www.hydefm.com"}),
    allowed_suffixes=frozenset(),
    matches=_hydefm_compat_matches,
    upstream_url=_webpage_upstream_url,
    render=_render_webpage,
    legacy_source_labels=frozenset({"webpage-local-generated"}),
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

GENERATED_ADAPTERS = (BANDCAMP_ADAPTER, NTS_ADAPTER, WEBPAGE_ADAPTER, MIXCLOUD_ADAPTER)
GENERATED_SOURCE_LABELS = frozenset(
    source_label
    for adapter in GENERATED_ADAPTERS
    for source_label in adapter.source_labels
)


def adapter_for_source(source: Source) -> Optional[GeneratedAdapter]:
    for adapter in GENERATED_ADAPTERS:
        if adapter.matches(source):
            return adapter
    return None


def hosted_route_for_source(source: Source) -> Optional[str]:
    adapter = adapter_for_source(source)
    return adapter.hosted_route if adapter else None
