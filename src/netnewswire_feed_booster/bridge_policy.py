from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Callable, Iterable, Optional, TypeVar


T = TypeVar("T")

DEFAULT_REFRESH_INTERVAL_SECONDS = 12 * 60 * 60
DEFAULT_REFRESH_SCHEDULE_HOURS = 1
DEFAULT_MAX_REFRESH_SOURCES_PER_RUN = 20
MAX_SAFE_REFRESH_SOURCES_PER_RUN = 25
DEFAULT_MAX_RSS_ITEMS_PER_SOURCE = 50


class FeedWaitingForRefresh(Exception):
    """Raised when a reader request has no safe cached feed to return."""


def require_cached_feed(rss: str | None, source_name: str) -> str:
    """Reader requests are cache-only and must never trigger an upstream fetch."""
    if rss is None:
        raise FeedWaitingForRefresh(f"{source_name} feed is waiting for its scheduled refresh")
    return rss


def due_sources(
    sources: Iterable[T],
    last_attempt: Callable[[T], Optional[float]],
    now: float,
    interval_seconds: float,
    limit: int,
) -> list[T]:
    """Pick up to `limit` sources due for a refresh, longest-waiting first.

    A source is due when it has never been attempted or its last attempt is at
    least `interval_seconds` old. Taking due sources in registry order let the
    top of the alphabet claim every batch once a route outgrew its capacity, so
    sources further down were never refreshed at all.
    """
    if limit < 1:
        raise ValueError("Refresh source limit must be positive")
    due: list[tuple[Optional[float], T]] = []
    for source in sources:
        attempted = last_attempt(source)
        if attempted is None or now - attempted >= interval_seconds:
            due.append((attempted, source))
    # Never-attempted sources sort first; the sort is stable, so ties keep registry order.
    due.sort(key=lambda entry: float("-inf") if entry[0] is None else entry[0])
    return [source for _attempted, source in due[:limit]]


@dataclass(frozen=True)
class RefreshRoutePlan:
    route: str
    source_count: int
    batch_size: int
    schedule_hours: int
    refresh_interval_hours: int

    @property
    def batches_needed(self) -> int:
        return ceil(self.source_count / self.batch_size) if self.source_count else 0

    @property
    def first_pass_hours(self) -> int:
        return self.batches_needed * self.schedule_hours

    @property
    def capacity_per_interval(self) -> int:
        return (self.refresh_interval_hours // self.schedule_hours) * self.batch_size

    @property
    def meets_target(self) -> bool:
        return self.source_count <= self.capacity_per_interval


def refresh_route_plan(
    route: str,
    source_count: int,
    batch_size: int,
    schedule_hours: int,
    refresh_interval_hours: int,
) -> RefreshRoutePlan:
    if batch_size < 1 or batch_size > MAX_SAFE_REFRESH_SOURCES_PER_RUN:
        raise ValueError(f"Batch size must be between 1 and {MAX_SAFE_REFRESH_SOURCES_PER_RUN}")
    if schedule_hours < 1:
        raise ValueError("Schedule interval must be at least one hour")
    if refresh_interval_hours < schedule_hours:
        raise ValueError("Refresh interval must be at least the schedule interval")
    return RefreshRoutePlan(route, source_count, batch_size, schedule_hours, refresh_interval_hours)
