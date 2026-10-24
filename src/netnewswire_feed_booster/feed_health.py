"""Read-only health check for direct feeds: reachability, blocks, moves, and activity.

`audit-sources` answers "does this URL serve a feed?" one source at a time. This
module answers the questions a large registry runs into: which feeds are gone,
which are blocked by a bot challenge, which Substacks went invite-only, which
moved to a new address, and which simply have not posted in a long time. It
never changes the registry; it reports and suggests.

An empty feed is reported but never treated as a problem. A SoundCloud artist
who has not opted into RSS, or a newsletter with no posts yet, is not dead.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError
from urllib.parse import urlparse

from .feed_store import Source
from .feed_validation import detect_feed_type
from .http_client import USER_AGENT, _restricted_open_request, _validate_fetch_url
from .rss_safety import parse_internet_date


HEALTH_USER_AGENT = f"{USER_AGENT} (feed health check)"
MAX_HEALTH_FETCH_BYTES = 8 * 1024 * 1024
ERROR_BODY_BYTES = 4096
DEFAULT_WORKERS = 16
DEFAULT_PAUSE_SECONDS = 0.3
DEFAULT_HOST_LIMIT = 4
# Hosts that carry hundreds of a typical registry's feeds get fewer parallel
# requests, so a full check never becomes a burst against one provider.
DEFAULT_HOST_LIMITS: Mapping[str, int] = {"youtube.com": 3, "substack.com": 2}
# Problems are retried after a pause, one request at a time per host. YouTube's
# feed endpoint in particular returns 404s and 500s for live channels when it is
# busy, and an immediate parallel retry tends to meet the same wall.
DEFAULT_RETRY_DELAY_SECONDS = 30.0
# Text YouTube puts on a channel page when the channel really is gone.
YOUTUBE_GONE_MARKERS = (
    b"This account has been terminated",
    b"This channel was removed",
    b"This channel does not exist",
)

OK = "ok"
QUIET_1Y = "quiet-1y"
QUIET_2Y = "quiet-2y"
UNDATED = "undated"
EMPTY = "empty"
GONE = "gone"
BOT_CHALLENGE = "bot-challenge"
INVITE_ONLY = "invite-only"
REFUSED = "refused"
SERVER_ERROR = "server-error"
NOT_A_FEED = "not-a-feed"
NETWORK_ERROR = "network-error"
HTTP_ONLY = "http-only"
# The feed failed, but the source demonstrably still exists. Usually temporary;
# not a problem to act on, just something to check again later.
UNAVAILABLE = "unavailable"

WORKING_STATUSES = (OK, QUIET_1Y, QUIET_2Y, UNDATED, EMPTY)
PROBLEM_STATUSES = frozenset({GONE, BOT_CHALLENGE, INVITE_ONLY, REFUSED, SERVER_ERROR, NOT_A_FEED, NETWORK_ERROR, HTTP_ONLY})
PLATFORMS = ("YouTube", "SoundCloud", "Substack", "Podcasts", "Websites")
DAYS_QUIET_1Y = 365
DAYS_QUIET_2Y = 730


@dataclass(frozen=True)
class FetchResult:
    """One fetch as the health check sees it. `status` is 0 when no HTTP response arrived."""

    status: int
    final_url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str = ""


Fetcher = Callable[[str], FetchResult]
UNSAFE_URL_PREFIX = "Unsafe fetch URL: "
REDIRECT_NOT_FOLLOWED = "redirect not followed"


@dataclass
class FeedHealth:
    source_id: str
    title: str
    kind: str
    platform: str
    url: str
    status: str
    http_status: int = 0
    item_count: int = 0
    newest: Optional[datetime] = None
    final_url: str = ""
    suggested_url: str = ""
    detail: str = ""

    @property
    def is_problem(self) -> bool:
        return self.status in PROBLEM_STATUSES


def fetch_for_health(url: str) -> FetchResult:
    """Fetch with the same URL safety as fetch_text, keeping status, headers, and final URL.

    https only, no credentials, no private or loopback addresses, and every
    redirect hop revalidated. Error responses keep a short body, because that is
    where Substack says a publication went invite-only.
    """
    try:
        _validate_fetch_url(url, allowed_hosts=None, allowed_suffixes=None)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": HEALTH_USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/feed+json, application/xml;q=0.9, */*;q=0.5",
            },
        )
        with _restricted_open_request(None, None)(request, timeout=30) as response:
            body = response.read(MAX_HEALTH_FETCH_BYTES)
            return FetchResult(
                status=getattr(response, "status", 200),
                final_url=response.geturl(),
                headers=_lowercase_headers(getattr(response, "headers", {})),
                body=body,
            )
    except HTTPError as error:
        try:
            body = error.read(ERROR_BODY_BYTES)
        except Exception:
            body = b""
        return FetchResult(
            status=error.code,
            final_url=error.geturl() or url,
            headers=_lowercase_headers(error.headers or {}),
            body=body or b"",
        )
    except Exception as error:
        message = str(error)
        if isinstance(error, ValueError) and message.startswith(UNSAFE_URL_PREFIX):
            target = message[len(UNSAFE_URL_PREFIX):].split(" ", 1)[0]
            if target != url:
                # The redirect handler refused a hop (http://, credentials, or a
                # private address), so the feed now points somewhere not followed.
                return FetchResult(status=0, final_url=target, error=REDIRECT_NOT_FOLLOWED)
        reason = getattr(error, "reason", error)
        return FetchResult(status=0, final_url=url, error=f"{type(error).__name__}: {reason}"[:200])


def platform_for(source: Source) -> str:
    host = _hostname(source.feed_url)
    if host == "youtube.com" or host.endswith(".youtube.com"):
        return "YouTube"
    if host == "soundcloud.com" or host.endswith(".soundcloud.com"):
        return "SoundCloud"
    if source.kind == "substack" or host.endswith(".substack.com"):
        return "Substack"
    if source.kind == "podcast":
        return "Podcasts"
    return "Websites"


def is_direct_feed(source: Source) -> bool:
    return urlparse(source.feed_url).scheme in {"http", "https"}


def inspect_feed(body: bytes) -> Tuple[str, int, Optional[datetime]]:
    """Return (feed type, item count, newest item date) for a fetched body."""
    text = body.decode("utf-8", errors="replace")
    feed_type = detect_feed_type(text)
    if feed_type == "json":
        try:
            items = json.loads(text.lstrip("﻿ \t\r\n")).get("items") or []
        except (ValueError, AttributeError):
            return "malformed-json", 0, None
        dates = [_parse_date(item.get("date_published") or item.get("date_modified")) for item in items if isinstance(item, dict)]
        return "json", len(items), max((date for date in dates if date), default=None)
    if feed_type not in {"rss", "atom", "xml"}:
        return feed_type, 0, None
    try:
        root = ET.fromstring(text.lstrip("﻿ \t\r\n"))
    except ET.ParseError:
        return "malformed-xml", 0, None
    root_name = _local_name(root.tag)
    if root_name not in {"rss", "feed", "rdf"}:
        return feed_type, 0, None
    entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    newest: Optional[datetime] = None
    for entry in entries:
        for child in entry:
            if _local_name(child.tag) in {"pubdate", "published", "updated", "date"}:
                parsed = _parse_date(child.text)
                if parsed:
                    newest = parsed if newest is None or parsed > newest else newest
                    break
    return {"feed": "atom", "rdf": "rss"}.get(root_name, root_name), len(entries), newest


def classify(source: Source, result: FetchResult, now: datetime, requested_url: str = "") -> FeedHealth:
    requested_url = requested_url or source.feed_url
    health = FeedHealth(
        source_id=source.id,
        title=source.title,
        kind=source.kind,
        platform=platform_for(source),
        url=source.feed_url,
        status=OK,
        http_status=result.status,
        final_url=result.final_url or requested_url,
    )
    body = result.body or b""
    if result.status == 0 and result.error == REDIRECT_NOT_FOLLOWED:
        target = urlparse(result.final_url)
        health.status = NOT_A_FEED
        health.detail = f"redirects to {target.scheme}://{target.hostname or ''}, which is not followed"
        return health
    if result.status == 0:
        health.status, health.detail = NETWORK_ERROR, result.error or "no response"
        return health
    if result.status >= 400:
        lowered = body[:ERROR_BODY_BYTES].lower()
        if (result.headers.get("cf-mitigated", "").lower() == "challenge") or b"<title>just a moment" in lowered:
            health.status, health.detail = BOT_CHALLENGE, f"HTTP {result.status}: the site asks for a browser check"
        elif result.status in {404, 410}:
            health.status, health.detail = GONE, f"HTTP {result.status}"
        elif result.status == 400 and b"invite-only" in lowered:
            health.status, health.detail = INVITE_ONLY, "Substack says this publication is invite-only"
        elif result.status >= 500:
            health.status, health.detail = SERVER_ERROR, f"HTTP {result.status}"
        else:
            health.status, health.detail = REFUSED, f"HTTP {result.status}"
        return health

    feed_type, item_count, newest = inspect_feed(body)
    health.item_count, health.newest = item_count, newest
    final_host = _hostname(health.final_url)
    if feed_type not in {"rss", "atom", "json"}:
        moved = final_host and final_host != _hostname(requested_url)
        health.status = NOT_A_FEED
        health.detail = f"{feed_type} page at {final_host}" if moved else f"serves {feed_type}, not a feed"
        return health
    if _comparable(health.final_url) != _comparable(source.feed_url):
        health.suggested_url = health.final_url
    if item_count == 0:
        health.status = EMPTY
    elif newest is None:
        health.status = UNDATED
    else:
        age_days = (now - newest).days
        health.status = QUIET_2Y if age_days > DAYS_QUIET_2Y else QUIET_1Y if age_days > DAYS_QUIET_1Y else OK
    return health


def check_feed(source: Source, fetch: Fetcher, now: datetime) -> FeedHealth:
    url = source.feed_url
    if urlparse(url).scheme == "http":
        # The fetcher is https-only by design, so an http:// feed is checked at
        # its https:// address and the upgrade is suggested when that works.
        https_url = "https://" + url[len("http://"):]
        health = classify(source, fetch(https_url), now, requested_url=https_url)
        if health.is_problem:
            health.status = HTTP_ONLY
            health.detail = f"listed as http://; the https:// address failed ({health.detail})"
        elif not health.suggested_url:
            health.suggested_url = health.final_url or https_url
        return health
    return classify(source, fetch(url), now)


def check_feeds(
    sources: Iterable[Source],
    fetch: Fetcher = fetch_for_health,
    now: Optional[datetime] = None,
    workers: int = DEFAULT_WORKERS,
    host_limits: Optional[Mapping[str, int]] = None,
    default_host_limit: int = DEFAULT_HOST_LIMIT,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    retry: bool = True,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[FeedHealth]:
    """Check direct feeds concurrently, capped per host, retrying problems once.

    The retry waits `retry_delay_seconds` and then runs one request at a time
    per host. A YouTube feed that still fails is only called gone when its
    channel page says so; otherwise it is reported as unavailable.

    Results come back in the order the sources were given. Generated (file://)
    feeds are skipped; the hosted bridge refreshes those.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    now = now or datetime.now(timezone.utc)
    limits = {**DEFAULT_HOST_LIMITS, **(host_limits or {})}
    direct = [source for source in sources if is_direct_feed(source)]
    def paced(host_limit: Optional[int]) -> Fetcher:
        semaphores: Dict[str, threading.Semaphore] = {}
        semaphores_lock = threading.Lock()

        def paced_fetch(url: str) -> FetchResult:
            key = _host_key(url, limits)
            with semaphores_lock:
                semaphore = semaphores.setdefault(
                    key, threading.Semaphore(host_limit or limits.get(key, default_host_limit))
                )
            with semaphore:
                try:
                    return fetch(url)
                finally:
                    if pause_seconds > 0:
                        time.sleep(pause_seconds)

        return paced_fetch

    def run(indexes: Sequence[int], host_limit: Optional[int] = None, confirm: bool = False) -> Dict[int, FeedHealth]:
        # Interleave hosts so a long run of one provider's feeds cannot hold
        # every worker while it waits on that provider's cap.
        ordered = _interleave_by_host(indexes, [direct[index].feed_url for index in indexes], limits)
        paced_fetch = paced(host_limit)
        done: Dict[int, FeedHealth] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            check = _check_and_confirm if confirm else check_feed
            futures = {pool.submit(check, direct[index], paced_fetch, now): index for index in ordered}
            for future in as_completed(futures):
                done[futures[future]] = future.result()
                if progress:
                    progress(len(done), len(ordered))
        return done

    results = run(range(len(direct)))
    if retry:
        again = [index for index, health in results.items() if health.is_problem]
        if again:
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
            results.update(run(sorted(again), host_limit=1, confirm=True))
    return [results[index] for index in range(len(direct))]


def _check_and_confirm(source: Source, fetch: Fetcher, now: datetime) -> FeedHealth:
    health = check_feed(source, fetch, now)
    channel_id = _youtube_channel_id(source.feed_url)
    if channel_id and health.status in {GONE, SERVER_ERROR}:
        return confirm_youtube_failure(health, channel_id, fetch)
    return health


def confirm_youtube_failure(health: FeedHealth, channel_id: str, fetch: Fetcher) -> FeedHealth:
    """Call a YouTube channel gone only when its channel page says so.

    YouTube's feed endpoint returns 404 and 500 for live channels when it is
    busy. The channel page states plainly when a channel was terminated,
    removed, or deleted.
    """
    page = fetch(f"https://www.youtube.com/channel/{channel_id}")
    if page.status == 200:
        for marker in YOUTUBE_GONE_MARKERS:
            start = page.body.find(marker)
            if start >= 0:
                end = page.body.find(b'"', start)
                notice = page.body[start : end if end > start else start + 200].decode("utf-8", errors="replace")
                health.status, health.detail = GONE, f"YouTube: {notice[:160]}"
                return health
        health.status = UNAVAILABLE
        health.detail = f"the feed returned {health.detail or 'an error'}, but the channel page loads; usually temporary"
        return health
    health.status = UNAVAILABLE
    health.detail = f"the feed returned {health.detail or 'an error'} and the channel page could not be checked"
    return health


def summarize(results: Iterable[FeedHealth]) -> Dict[str, Counter]:
    table: Dict[str, Counter] = {}
    for health in results:
        table.setdefault(health.platform, Counter())[health.status] += 1
    return table


def format_summary(results: Sequence[FeedHealth], skipped_generated: int = 0, show_sensitive: bool = False) -> str:
    lines = [f"Checked {len(results)} direct feeds." + (f" Skipped {skipped_generated} generated feeds; the hosted bridge refreshes those." if skipped_generated else "")]
    lines.append("")
    lines.append(
        f"{'platform':12s} {'total':>6s} {'ok':>6s} {'quiet1y':>8s} {'quiet2y':>8s} {'undated':>8s} {'empty':>6s} {'unavail':>8s} {'problems':>9s}"
    )
    table = summarize(results)
    for platform in [name for name in PLATFORMS if name in table]:
        counts = table[platform]
        problems = sum(count for status, count in counts.items() if status in PROBLEM_STATUSES)
        lines.append(
            f"{platform:12s} {sum(counts.values()):6d} {counts[OK]:6d} {counts[QUIET_1Y]:8d} {counts[QUIET_2Y]:8d} "
            f"{counts[UNDATED]:8d} {counts[EMPTY]:6d} {counts[UNAVAILABLE]:8d} {problems:9d}"
        )
    problems = [health for health in results if health.is_problem]
    lines.append("")
    lines.append(f"Problems ({len(problems)}):")
    for health in sorted(problems, key=lambda item: (item.status, item.platform, item.title.lower())):
        lines.append("\t".join([health.status, health.platform, health.source_id, health.title, health.detail, display_url(health.url, show_sensitive)]))
    unavailable = [health for health in results if health.status == UNAVAILABLE]
    if unavailable:
        lines.append("")
        lines.append(f"Temporarily unavailable; the source still exists, check again later ({len(unavailable)}):")
        for health in sorted(unavailable, key=lambda item: (item.platform, item.title.lower())):
            lines.append("\t".join([health.platform, health.source_id, health.title, health.detail]))
    suggestions = [health for health in results if health.suggested_url]
    lines.append("")
    lines.append(f"Suggested address updates ({len(suggestions)}):")
    for health in sorted(suggestions, key=lambda item: item.title.lower()):
        lines.append("\t".join([health.source_id, health.title, describe_move(health.url, health.suggested_url, show_sensitive)]))
    lines.append("")
    lines.append("Empty, quiet, and unavailable feeds are not problems; an empty feed is not proof it is dead.")
    lines.append("Nothing was changed.")
    return "\n".join(lines)


def write_report(results: Sequence[FeedHealth], path: Path, skipped_generated: int = 0, show_sensitive: bool = False, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    lines = [
        f"# Feed health ({now.date().isoformat()})",
        "",
        f"Read-only check of {len(results)} active direct feeds"
        + (f"; {skipped_generated} generated feeds skipped." if skipped_generated else ".")
        + " Problems were retried once. Nothing was changed. Empty and quiet feeds are not problems: an empty feed is not proof it is dead.",
        "",
        "| Platform | Total | OK | Quiet 1–2y | Quiet 2y+ | Undated | Empty | Unavailable | Problems |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    table = summarize(results)
    for platform in [name for name in PLATFORMS if name in table]:
        counts = table[platform]
        problems = sum(count for status, count in counts.items() if status in PROBLEM_STATUSES)
        lines.append(
            f"| {platform} | {sum(counts.values())} | {counts[OK]} | {counts[QUIET_1Y]} | {counts[QUIET_2Y]} | "
            f"{counts[UNDATED]} | {counts[EMPTY]} | {counts[UNAVAILABLE]} | {problems} |"
        )
    lines.append("")

    def section(title: str, items: List[FeedHealth], describe: Callable[[FeedHealth], str]) -> None:
        lines.extend([f"## {title} ({len(items)})", ""])
        lines.extend(f"- {health.title} — `{health.source_id}` ({health.platform}; {describe(health)})" for health in sorted(items, key=lambda item: item.title.lower()))
        lines.append("")

    for status, title in (
        (GONE, "Gone"),
        (BOT_CHALLENGE, "Blocked by a bot challenge"),
        (INVITE_ONLY, "Invite-only (closed) Substacks"),
        (NOT_A_FEED, "Not a feed anymore"),
        (HTTP_ONLY, "http:// only"),
        (REFUSED, "Refused"),
        (SERVER_ERROR, "Server errors"),
        (NETWORK_ERROR, "Network, DNS, or certificate errors"),
    ):
        items = [health for health in results if health.status == status]
        if items:
            section(title, items, lambda health: f"{health.detail}; {display_url(health.url, show_sensitive)}")
    section(
        "Temporarily unavailable (the source still exists)",
        [health for health in results if health.status == UNAVAILABLE],
        lambda health: health.detail,
    )
    section(
        "Suggested address updates",
        [health for health in results if health.suggested_url],
        lambda health: describe_move(health.url, health.suggested_url, show_sensitive),
    )
    section("Empty", [health for health in results if health.status == EMPTY], lambda health: display_url(health.url, show_sensitive))
    for status, title in ((QUIET_2Y, "No new item in 2+ years"), (QUIET_1Y, "No new item in 1–2 years")):
        section(title, [health for health in results if health.status == status], lambda health: f"last {health.newest.date().isoformat() if health.newest else 'unknown'}")
    section("Undated items", [health for health in results if health.status == UNDATED], lambda health: display_url(health.url, show_sensitive))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def describe_move(old: str, new: str, show_sensitive: bool = False) -> str:
    if show_sensitive:
        return f"{old} -> {new}"
    before, after = urlparse(old), urlparse(new)
    if _hostname(old) == _hostname(new):
        if before.scheme != after.scheme:
            return f"{before.scheme}://{_hostname(old)} -> {after.scheme}://{_hostname(new)}"
        return f"{_hostname(old)} (new path)"
    return f"{_hostname(old)} -> {_hostname(new)}"


def display_url(url: str, show_sensitive: bool = False) -> str:
    """Feed URLs can carry private keys in a path or query, so show only the host unless asked."""
    if show_sensitive:
        return url
    return _hostname(url) or "[redacted]"


def _hostname(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower().rstrip(".")


def _host_key(url: str, limits: Mapping[str, int]) -> str:
    host = _hostname(url)
    for suffix in limits:
        if host == suffix or host.endswith(f".{suffix}"):
            return suffix
    return host


def _interleave_by_host(indexes: Sequence[int], urls: Sequence[str], limits: Mapping[str, int]) -> List[int]:
    queues: Dict[str, List[int]] = {}
    for index, url in zip(indexes, urls):
        queues.setdefault(_host_key(url, limits), []).append(index)
    ordered: List[int] = []
    while queues:
        for key in list(queues):
            ordered.append(queues[key].pop(0))
            if not queues[key]:
                del queues[key]
    return ordered


def _youtube_channel_id(url: str) -> str:
    parsed = urlparse(url)
    if _hostname(url) not in {"youtube.com", "www.youtube.com"} or parsed.path != "/feeds/videos.xml":
        return ""
    for part in parsed.query.split("&"):
        key, _sep, value = part.partition("=")
        if key == "channel_id" and value.startswith("UC") and value.replace("-", "").replace("_", "").isalnum():
            return value
    return ""


def _comparable(url: str) -> str:
    return (url or "").rstrip("/")


def _lowercase_headers(headers) -> Dict[str, str]:
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(key).lower(): str(value) for key, value in items}


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _parse_date(value) -> Optional[datetime]:
    try:
        parsed = parse_internet_date(str(value or ""))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
