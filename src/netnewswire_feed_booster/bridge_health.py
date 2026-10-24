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

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, List, Mapping, Optional, Set, Tuple


MAX_ERROR_CHARS = 160
DEFAULT_STALE_AFTER_INTERVALS = 3
DEFAULT_LIST_LIMIT = 40
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
) -> BridgeHealth:
    """Sort active generated sources by what a reader is actually getting.

    `cached` maps source ID to the cache file's modification time, which stands
    in for a last success in state written before success tracking existed.
    A 304 leaves that file untouched, so it is a lower bound, never too recent.
    """
    ids = sorted(set(source_ids))
    stale_after = interval_seconds * stale_after_intervals
    health = BridgeHealth(checked_at=now, interval_seconds=interval_seconds, stale_after_seconds=stale_after, total=len(ids))
    for source_id in ids:
        state = states.get(source_id, RefreshState())
        if source_id not in cached and source_id not in seeded:
            health.nothing_to_serve.append(source_id)
        elif source_id not in cached:
            health.seed_only.append(source_id)
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
    return health


def format_bridge_health(health: BridgeHealth, list_limit: int = DEFAULT_LIST_LIMIT) -> str:
    def when(timestamp: Optional[float]) -> str:
        if timestamp is None:
            return "never recorded"
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

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
    return "\n".join(lines) + "\n"


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
