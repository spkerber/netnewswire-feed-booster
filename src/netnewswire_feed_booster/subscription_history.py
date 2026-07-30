from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .feed_store import Source, repo_root, slugify, today_iso, validate_profile_id


VALID_HISTORY_STATUSES = {
    "rss_unsubscribed",
    "external_unfollow_needed",
    "external_unfollow_confirmed",
    "ignored",
}


def default_subscription_history_path(profile: str = "", *, prefer_configured: bool = False) -> Path:
    configured = os.environ.get("RSS_HISTORY_FILE")
    if configured and (prefer_configured or not profile):
        return Path(configured)
    if profile:
        profile = validate_profile_id(profile)
        return repo_root() / "data" / f"subscription-history.{profile}.json"
    profile = os.environ.get("RSS_PROFILE", "")
    if profile:
        profile = validate_profile_id(profile)
        return repo_root() / "data" / f"subscription-history.{profile}.json"
    return repo_root() / "data" / "subscription-history.json"


@dataclass
class SubscriptionHistoryEntry:
    id: str
    source_id: str
    source_title: str
    feed_url: str
    source_kind: str
    profile: str
    action: str
    status: str
    reason: str = ""
    decided_at: str = field(default_factory=today_iso)
    updated_at: str = field(default_factory=today_iso)
    external_url: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "feed_url": self.feed_url,
            "source_kind": self.source_kind,
            "profile": self.profile,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "decided_at": self.decided_at,
            "updated_at": self.updated_at,
            "external_url": self.external_url,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubscriptionHistoryEntry":
        return cls(
            id=data["id"],
            source_id=data["source_id"],
            source_title=data["source_title"],
            feed_url=data["feed_url"],
            source_kind=data.get("source_kind", "other"),
            profile=data.get("profile", os.environ.get("RSS_PROFILE", "me")),
            action=data.get("action", "rss_unsubscribe"),
            status=data.get("status", "rss_unsubscribed"),
            reason=data.get("reason", ""),
            decided_at=data.get("decided_at", today_iso()),
            updated_at=data.get("updated_at", today_iso()),
            external_url=data.get("external_url", ""),
            notes=data.get("notes", ""),
        )


class SubscriptionHistoryStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or default_subscription_history_path()
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "entries": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.data.get("schema_version", 1),
            "entries": [entry.to_dict() for entry in self.entries()],
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.data = payload

    def entries(self) -> List[SubscriptionHistoryEntry]:
        return [SubscriptionHistoryEntry.from_dict(item) for item in self.data.get("entries", [])]

    def filtered(
        self,
        status: Optional[str] = None,
        profile: Optional[str] = None,
        source_kind: Optional[str] = None,
    ) -> List[SubscriptionHistoryEntry]:
        entries = self.entries()
        if status:
            entries = [entry for entry in entries if entry.status == status]
        if profile:
            entries = [entry for entry in entries if entry.profile == profile]
        if source_kind:
            entries = [entry for entry in entries if entry.source_kind == source_kind]
        return sorted(entries, key=lambda entry: (entry.status, entry.source_kind, entry.source_title.lower()))

    def external_unfollow_candidates(self, profile: Optional[str] = None) -> List[SubscriptionHistoryEntry]:
        statuses = {"rss_unsubscribed", "external_unfollow_needed"}
        kinds = {"substack", "youtube", "newsletter"}
        return [
            entry
            for entry in self.filtered(profile=profile)
            if entry.status in statuses and entry.source_kind in kinds
        ]

    def record_rss_unsubscribe(
        self,
        source: Source,
        profile: str,
        reason: str = "",
        status: str = "rss_unsubscribed",
    ) -> SubscriptionHistoryEntry:
        if status not in VALID_HISTORY_STATUSES:
            raise ValueError(f"Invalid subscription-history status: {status}")

        entries = self.entries()
        existing = self._open_entry_for_source(entries, source.id, profile)
        if existing:
            existing.source_title = source.title
            existing.feed_url = source.feed_url
            existing.source_kind = source.kind
            existing.status = status
            existing.reason = reason or existing.reason
            existing.updated_at = today_iso()
            existing.external_url = source.site_url or existing.external_url
            entry = existing
        else:
            entry = SubscriptionHistoryEntry(
                id=unique_history_entry_id(f"{source.id}-rss-unsubscribed", [item.id for item in entries]),
                source_id=source.id,
                source_title=source.title,
                feed_url=source.feed_url,
                source_kind=source.kind,
                profile=profile,
                action="rss_unsubscribe",
                status=status,
                reason=reason,
                external_url=source.site_url,
            )
            entries.append(entry)

        self.data["entries"] = [item.to_dict() for item in entries]
        return entry

    def set_status(self, entry_id: str, status: str) -> SubscriptionHistoryEntry:
        if status not in VALID_HISTORY_STATUSES:
            raise ValueError(f"Invalid subscription-history status: {status}")
        entries = self.entries()
        for entry in entries:
            if entry.id == entry_id:
                entry.status = status
                entry.updated_at = today_iso()
                self.data["entries"] = [item.to_dict() for item in entries]
                return entry
        raise KeyError(f"No subscription-history entry found with id '{entry_id}'")

    def _open_entry_for_source(
        self,
        entries: Iterable[SubscriptionHistoryEntry],
        source_id: str,
        profile: str,
    ) -> Optional[SubscriptionHistoryEntry]:
        open_statuses = {"rss_unsubscribed", "external_unfollow_needed"}
        return next(
            (
                entry
                for entry in entries
                if entry.source_id == source_id
                and entry.profile == profile
                and entry.action == "rss_unsubscribe"
                and entry.status in open_statuses
            ),
            None,
        )


def unique_history_entry_id(base_id: str, existing_ids: Iterable[str]) -> str:
    existing = set(existing_ids)
    base_id = slugify(base_id)
    if base_id not in existing:
        return base_id
    counter = 2
    while f"{base_id}-{counter}" in existing:
        counter += 1
    return f"{base_id}-{counter}"
