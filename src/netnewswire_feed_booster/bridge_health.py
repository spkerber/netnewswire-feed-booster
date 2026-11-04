"""Per-source refresh state for the hosted bridge, and the health summary built from it.

The bridge answers every reader request from its cache, or from the copy seeded
at deploy time, and never fetches upstream on a request. That keeps readers fast
but hides trouble: a source whose refreshes keep failing, or that the scheduler
never reaches, goes on serving an old copy and nothing in the reader says so.
This module keeps just enough state per source to notice, and summarizes it.
State holds timestamps, a failure count, and a short error with URLs removed;
never feed URLs, tokens, or content.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Iterable, List, Mapping, Optional, Set, Tuple


MAX_ERROR_CHARS = 160
DEFAULT_STALE_AFTER_INTERVALS = 3
# Newsletters and release emails come when they come, so a quiet email source
# is information, not a failure. Past this, the report calls it quiet.
DEFAULT_EMAIL_QUIET_DAYS = 30
# With a route's refresh off, its feeds move only when the owner catches them up
# from a network the upstream still answers. Past this, the report says so.
DEFAULT_CATCH_UP_DUE_DAYS = 14
# A refresh-off copy built this long before the newest one was missed by the last catch-up.
CATCH_UP_MISSED_AFTER_SECONDS = 24 * 60 * 60
DEFAULT_LIST_LIMIT = 40
STATUS_FEED_TITLE = "Feed Booster status"
# A failing feed needs its owner once it has outlasted a passing hiccup: at the
# default 12-hour interval, three failures in a row is a day and a half.
ATTENTION_AFTER_FAILURES = 3
WORKFLOW = "./scripts/netnewswire_workflow.sh"
_URL_PATTERN = re.compile(r"\bhttps?://\S+")


@dataclass(frozen=True)
class RefreshState:
    last_attempt: Optional[float] = None
    last_success: Optional[float] = None
    last_error: str = ""
    consecutive_failures: int = 0


def parse_refresh_state(text: str) -> RefreshState:
    """Read one refresh-state file. Files written before success tracking hold only last_attempt."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return RefreshState()
    if not isinstance(data, dict):
        return RefreshState()
    return RefreshState(
        last_attempt=_number(data.get("last_attempt")),
        last_success=_number(data.get("last_success")),
        last_error=str(data.get("last_error") or "")[:MAX_ERROR_CHARS],
        consecutive_failures=_count(data.get("consecutive_failures")),
    )


def dump_refresh_state(state: RefreshState) -> str:
    payload = {
        "last_attempt": state.last_attempt,
        "last_success": state.last_success,
        "last_error": state.last_error,
        "consecutive_failures": state.consecutive_failures,
    }
    return json.dumps({key: value for key, value in payload.items() if value not in (None, "")})


def record_refresh(previous: RefreshState, now: float, error: Optional[BaseException] = None) -> RefreshState:
    """The state after one refresh attempt. A 304 that returned the cached feed counts as a success."""
    if error is None:
        return RefreshState(last_attempt=now, last_success=now)
    return RefreshState(
        last_attempt=now,
        last_success=previous.last_success,
        last_error=short_error(error),
        consecutive_failures=previous.consecutive_failures + 1,
    )


def short_error(error: BaseException) -> str:
    text = " ".join(f"{type(error).__name__}: {error}".split())
    return _URL_PATTERN.sub("[url]", text)[:MAX_ERROR_CHARS]


@dataclass
class BridgeHealth:
    checked_at: float
    interval_seconds: float
    stale_after_seconds: float
    total: int
    healthy: List[str] = field(default_factory=list)
    never_attempted: List[str] = field(default_factory=list)
    failing: List[Tuple[str, int, str]] = field(default_factory=list)
    stale: List[Tuple[str, Optional[float]]] = field(default_factory=list)
    seed_only: List[str] = field(default_factory=list)
    nothing_to_serve: List[str] = field(default_factory=list)
    refresh_off: List[str] = field(default_factory=list)
    # Email sources: (source ID, time of the last accepted mail), and which are quiet.
    email: List[Tuple[str, Optional[float]]] = field(default_factory=list)
    email_quiet: List[str] = field(default_factory=list)
    email_quiet_after_seconds: float = DEFAULT_EMAIL_QUIET_DAYS * 24 * 60 * 60
    # Refresh-off sources: when the newest copy was built (the last catch-up),
    # and which sources that catch-up did not rebuild.
    last_catch_up: Optional[float] = None
    missed_catch_up: List[Tuple[str, Optional[float]]] = field(default_factory=list)
    catch_up_due_after_seconds: float = DEFAULT_CATCH_UP_DUE_DAYS * 24 * 60 * 60

    @property
    def catch_up_due(self) -> bool:
        return self.last_catch_up is not None and self.checked_at - self.last_catch_up > self.catch_up_due_after_seconds

    @property
    def has_problems(self) -> bool:
        return bool(self.never_attempted or self.failing or self.stale or self.nothing_to_serve)


def summarize_bridge_health(
    source_ids: Iterable[str],
    states: Mapping[str, RefreshState],
    cached: Mapping[str, float],
    seeded: Set[str],
    now: float,
    interval_seconds: float,
    stale_after_intervals: int = DEFAULT_STALE_AFTER_INTERVALS,
    refresh_off: Iterable[str] = (),
    email: Iterable[str] = (),
    email_quiet_days: float = DEFAULT_EMAIL_QUIET_DAYS,
    built: Optional[Mapping[str, float]] = None,
    catch_up_due_days: float = DEFAULT_CATCH_UP_DUE_DAYS,
) -> BridgeHealth:
    """Sort active generated sources by what a reader is actually getting.

    `cached` maps source ID to the cache file's modification time, which stands
    in for a last success in state written before success tracking existed.
    A 304 leaves that file untouched, so it is a lower bound, never too recent.

    Sources in `refresh_off` belong to a route whose refresh is switched off.
    They still count toward nothing to serve and seed copy only, but not toward
    failing or stale: their last refresh outcome is history, not a live problem.

    `built` maps refresh-off sources to when the copy they serve was built.
    The last catch-up is the build time most of them share, the median: a
    catch-up rebuilds nearly all of them within the hour, while one source
    added since must not pass for a catch-up. A source built well before it
    was missed by that catch-up, usually because it failed.

    Sources in `email` are never refreshed; each accepted message records a
    success instead. They are listed with their last mail, and called quiet
    after `email_quiet_days` without one, but never failing or stale.
    """
    ids = sorted(set(source_ids))
    not_refreshed = set(refresh_off)
    email_ids = set(email)
    stale_after = interval_seconds * stale_after_intervals
    health = BridgeHealth(
        checked_at=now,
        interval_seconds=interval_seconds,
        stale_after_seconds=stale_after,
        total=len(ids),
        email_quiet_after_seconds=email_quiet_days * 24 * 60 * 60,
        catch_up_due_after_seconds=catch_up_due_days * 24 * 60 * 60,
    )
    for source_id in ids:
        state = states.get(source_id, RefreshState())
        if source_id not in cached and source_id not in seeded:
            health.nothing_to_serve.append(source_id)
        elif source_id not in cached:
            health.seed_only.append(source_id)
        if source_id in email_ids:
            last_mail = state.last_success
            health.email.append((source_id, last_mail))
            if last_mail is None or now - last_mail > health.email_quiet_after_seconds:
                health.email_quiet.append(source_id)
            continue
        if source_id in not_refreshed:
            health.refresh_off.append(source_id)
            continue
        if state.last_attempt is None:
            health.never_attempted.append(source_id)
            continue
        last_success = state.last_success if state.last_success is not None else cached.get(source_id)
        if state.consecutive_failures:
            health.failing.append((source_id, state.consecutive_failures, state.last_error))
        if last_success is None or now - last_success > stale_after:
            health.stale.append((source_id, last_success))
        elif not state.consecutive_failures:
            health.healthy.append(source_id)
    built = built or {}
    build_times = [built[source_id] for source_id in health.refresh_off if source_id in built]
    if build_times:
        health.last_catch_up = sorted(build_times)[len(build_times) // 2]
        cutoff = health.last_catch_up - CATCH_UP_MISSED_AFTER_SECONDS
        health.missed_catch_up = [
            (source_id, built.get(source_id))
            for source_id in health.refresh_off
            if source_id not in built or built[source_id] < cutoff
        ]
    return health


def format_bridge_health(health: BridgeHealth, list_limit: int = DEFAULT_LIST_LIMIT) -> str:
    def when(timestamp: Optional[float]) -> str:
        if timestamp is None:
            return "never recorded"
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    def days_ago(timestamp: float) -> str:
        days = max(int((health.checked_at - timestamp) // 86400), 0)
        return "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"

    lines = [
        f"Hosted bridge health, {datetime.fromtimestamp(health.checked_at, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"{health.total} active generated sources. Target refresh every {health.interval_seconds / 3600:g}h; "
        f"stale after {health.stale_after_seconds / 3600:g}h without a success.",
        "",
        f"  healthy           {len(health.healthy):6d}",
        f"  never attempted   {len(health.never_attempted):6d}",
        f"  failing           {len(health.failing):6d}",
        f"  stale             {len(health.stale):6d}",
        f"  seed copy only    {len(health.seed_only):6d}",
        f"  nothing to serve  {len(health.nothing_to_serve):6d}",
    ]
    if health.refresh_off and health.last_catch_up is not None:
        lines.append(
            f"  refresh off       {len(health.refresh_off):6d}  (serving the last cached or deploy-time copy; "
            f"last catch-up {when(health.last_catch_up)}, {days_ago(health.last_catch_up)})"
        )
    elif health.refresh_off:
        lines.append(f"  refresh off       {len(health.refresh_off):6d}  (serving the last cached or deploy-time copy)")
    quiet_days = health.email_quiet_after_seconds / 86400
    if health.email:
        lines.append(f"  email             {len(health.email):6d}  ({len(health.email_quiet)} with no mail in {quiet_days:g} days)")

    if health.catch_up_due:
        lines.extend(
            [
                "",
                f"Catch-up due: refresh-off feeds were last rebuilt {days_ago(health.last_catch_up)}. "
                "Run ./scripts/netnewswire_workflow.sh catch-up from a network the upstream still answers.",
            ]
        )

    def section(title: str, rows: List[str]) -> None:
        if not rows:
            return
        lines.extend(["", f"{title} ({len(rows)}):"])
        lines.extend(f"  {row}" for row in rows[:list_limit])
        if len(rows) > list_limit:
            lines.append(f"  ... and {len(rows) - list_limit} more")

    section("Nothing to serve; readers get 'waiting for its scheduled refresh'", health.nothing_to_serve)
    section(
        "Failing",
        [f"{source_id}\t{count} in a row\t{error}" for source_id, count, error in sorted(health.failing, key=lambda row: (-row[1], row[0]))],
    )
    section(
        "Stale; last success",
        [f"{source_id}\t{when(last_success)}" for source_id, last_success in sorted(health.stale, key=lambda row: (row[1] or 0, row[0]))],
    )
    section("Never attempted; serving the deploy-time copy if there is one", health.never_attempted)
    section(
        "Refresh off and missed by the last catch-up; serving an older copy",
        [
            f"{source_id}\t{when(built_at) if built_at is not None else 'no build date'}"
            for source_id, built_at in sorted(health.missed_catch_up, key=lambda row: (row[1] or 0, row[0]))
        ],
    )
    quiet = set(health.email_quiet)
    section(
        f"Email, last mail received; quiet means none in {quiet_days:g} days, which is not a failure",
        [
            f"{source_id}\t{when(last_mail) if last_mail is not None else 'no mail yet'}" + ("\tquiet" if source_id in quiet else "")
            for source_id, last_mail in sorted(health.email, key=lambda row: (row[1] or 0, row[0]))
        ],
    )
    return "\n".join(lines) + "\n"


def render_status_feed(health: BridgeHealth, list_limit: int = DEFAULT_LIST_LIMIT) -> str:
    """An RSS feed of what the bridge needs from its owner, read in the reader itself.

    Items have stable GUIDs, so each one shows up once:
    - one per catch-up, naming the feeds it missed;
    - one when the next catch-up comes due;
    - one each time the set of feeds needing attention changes.
    A feed needs attention when it has failed several refreshes in a row, or
    when it has nothing to serve at all. Quiet email sources and passing
    failures are left to bridge-health.
    """

    def day(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    def listed(rows: List[str]) -> str:
        shown = "".join(f"<li>{row}</li>" for row in rows[:list_limit])
        more = f"<li>... and {len(rows) - list_limit} more</li>" if len(rows) > list_limit else ""
        return f"<ul>{shown}{more}</ul>"

    def code(value: str) -> str:
        return f"<code>{html.escape(value)}</code>"

    items: List[Tuple[str, str, Optional[float], str]] = []
    errors = {source_id: (count, error) for source_id, count, error in health.failing}
    attention = sorted(
        {source_id for source_id, (count, _error) in errors.items() if count >= ATTENTION_AFTER_FAILURES}
        | set(health.nothing_to_serve)
    )
    if attention:
        rows = []
        for source_id in attention:
            if source_id in errors:
                count, error = errors[source_id]
                rows.append(f"{code(source_id)}: {count} failed refreshes in a row. {html.escape(error)}")
            else:
                rows.append(f"{code(source_id)}: nothing to serve, so readers get \"waiting for its scheduled refresh\".")
        digest = hashlib.sha256("\n".join(attention).encode("utf-8")).hexdigest()[:16]
        count = len(attention)
        items.append(
            (
                f"attention-{digest}",
                f"{count} generated feed{'s' if count != 1 else ''} need{'' if count != 1 else 's'} attention",
                None,
                f"<p>These feeds aren't getting new items. Run {code(f'{WORKFLOW} bridge-health')} for the full report.</p>"
                + listed(rows),
            )
        )
    if health.catch_up_due:
        due_at = health.last_catch_up + health.catch_up_due_after_seconds
        items.append(
            (
                f"bandcamp-catch-up-due-{day(health.last_catch_up)}",
                f"Time to catch up Bandcamp: last catch-up was {day(health.last_catch_up)}",
                due_at,
                f"<p>The bridge is still serving Bandcamp feeds rebuilt on {day(health.last_catch_up)}. "
                f"Run {code(f'{WORKFLOW} catch-up')} on your Mac to bring them up to date.</p>",
            )
        )
    if health.last_catch_up is not None:
        description = f"<p>The bridge is serving Bandcamp feeds rebuilt on {day(health.last_catch_up)}.</p>"
        if health.missed_catch_up:
            missed = len(health.missed_catch_up)
            weren_t, serve = ("weren't", "serve") if missed != 1 else ("wasn't", "serves")
            description += (
                f"<p>{missed} {'feeds' if missed != 1 else 'feed'} {weren_t} rebuilt and still {serve} an older copy. "
                "One that is missed catch-up after catch-up keeps failing:</p>"
                + listed(
                    [
                        f"{code(source_id)}: {day(built_at) if built_at is not None else 'no build date'}"
                        for source_id, built_at in sorted(health.missed_catch_up, key=lambda row: (row[1] or 0, row[0]))
                    ]
                )
            )
        items.append(
            (
                f"bandcamp-catch-up-{day(health.last_catch_up)}",
                f"Bandcamp feeds caught up on {day(health.last_catch_up)}",
                health.last_catch_up,
                description,
            )
        )

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = STATUS_FEED_TITLE
    ET.SubElement(channel, "description").text = "What the hosted bridge needs from you. An item appears only when something changes."
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.fromtimestamp(health.checked_at, tz=timezone.utc))
    for guid, title, published, description in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = guid
        if published is not None:
            # Undated items are dated by the reader when it first sees them.
            ET.SubElement(item, "pubDate").text = format_datetime(datetime.fromtimestamp(published, tz=timezone.utc))
        ET.SubElement(item, "description").text = description
    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def _number(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _count(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0
