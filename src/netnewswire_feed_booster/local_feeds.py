"""How current the generated feeds on this Mac are.

Generated feeds are files under exports/generated, rewritten only when a
refresh of that source succeeds. A file's modification time is therefore when
its source was last rebuilt: an old one means no refresh has run since, or the
source has failed in every run since. Nothing here touches the network.

On a Mac-only setup these files are what NetNewsWire reads. With the hosted
bridge they are the seeds each deploy ships, and for Bandcamp on a host that
Bandcamp blocks, the only way its feeds move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


DEFAULT_STALE_DAYS = 14
WORKFLOW = "./scripts/netnewswire_workflow.sh"


@dataclass
class LocalFeedGroup:
    name: str
    total: int = 0
    # When most of the group was last rebuilt (the median), so one source
    # added since does not pass for a full refresh.
    last_refresh: Optional[float] = None
    stale: List[Tuple[str, float]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


def local_feed_group(name: str, source_ids: Iterable[str], out_dir: Path, now: float, stale_days: float) -> LocalFeedGroup:
    group = LocalFeedGroup(name)
    cutoff = now - stale_days * 24 * 60 * 60
    build_times = []
    for source_id in sorted(source_ids):
        group.total += 1
        try:
            built = (out_dir / f"{source_id}.rss").stat().st_mtime
        except FileNotFoundError:
            group.missing.append(source_id)
            continue
        build_times.append(built)
        if built < cutoff:
            group.stale.append((source_id, built))
    if build_times:
        group.last_refresh = sorted(build_times)[len(build_times) // 2]
    return group


def format_local_feed_status(
    groups: List[LocalFeedGroup],
    out_dir: Path,
    profile: str,
    now: float,
    stale_days: float,
    list_sources: bool = False,
) -> str:
    def when(timestamp: float) -> str:
        days = max(int((now - timestamp) // 86400), 0)
        ago = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"
        return f"{datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%d')}, {ago}"

    lines = [f"Generated feeds on this Mac for profile {profile} ({out_dir})"]
    for group in groups:
        refreshed = f"last refreshed {when(group.last_refresh)}" if group.last_refresh is not None else "none built yet"
        lines.append(
            f"  {group.name:<10} {group.total:5d} feeds  {refreshed}; "
            f"{len(group.stale)} not rebuilt in {stale_days:g} days; {len(group.missing)} missing"
        )

    advice = []
    for group in groups:
        if not group.total:
            continue
        behind = group.last_refresh is None or now - group.last_refresh > stale_days * 24 * 60 * 60
        if not behind:
            continue
        if group.name == "bandcamp":
            advice.append(
                f"Bandcamp feeds have not been rebuilt in {stale_days:g} days. Mac only: run {WORKFLOW} refresh-local. "
                f"Hosted bridge: run {WORKFLOW} catch-up, which rebuilds them here and deploys them."
            )
        else:
            advice.append(
                f"Other generated feeds have not been rebuilt here in {stale_days:g} days. Mac only: run {WORKFLOW} refresh-local. "
                f"Hosted bridge: the bridge refreshes these itself; {WORKFLOW} bridge-health shows how they are doing."
            )
    if any(group.stale or group.missing for group in groups) and not list_sources:
        advice.append(
            "Pass --list to name them. A Bandcamp feed that stays on the list after a refresh keeps failing; "
            "so does any feed on a Mac-only setup. With the hosted bridge, old local copies of other feeds are expected."
        )
    if advice:
        lines.append("")
        lines.extend(advice)

    if list_sources:
        for group in groups:
            if group.stale:
                lines.extend(["", f"{group.name}: not rebuilt in {stale_days:g} days ({len(group.stale)}):"])
                lines.extend(f"  {source_id}\t{when(built)}" for source_id, built in sorted(group.stale, key=lambda row: (row[1], row[0])))
            if group.missing:
                lines.extend(["", f"{group.name}: no local file yet ({len(group.missing)}):"])
                lines.extend(f"  {source_id}" for source_id in group.missing)
    return "\n".join(lines) + "\n"
