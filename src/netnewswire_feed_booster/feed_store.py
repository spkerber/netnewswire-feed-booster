from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import os
from typing import Any, Dict, Iterable, List, Optional


VALID_STATUSES = {"active", "candidate", "paused", "unsubscribed"}
VALID_KINDS = {"website", "substack", "youtube", "bandcamp", "newsletter", "podcast", "other"}
BANDCAMP_GROUP = "Bandcamp"
BANDCAMP_GROUP_ALIASES = {"bandcamp", "bandcamp artists", "bandcamp fans"}
SOURCE_GROUP_OVERRIDES = {
    "nyt-movies": ["culture"],
    "split-infinitives": ["blogs"],
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_sources_path(profile: str = "") -> Path:
    configured = os.environ.get("RSS_SOURCES_FILE")
    if configured:
        return Path(configured)
    profile = profile or os.environ.get("RSS_PROFILE", "")
    if profile:
        profile_path = repo_root() / "data" / f"sources.{profile}.json"
        if profile_path.exists():
            return profile_path
    return repo_root() / "data" / "sources.json"


def default_private_sources_path() -> Path:
    return repo_root() / "data" / "private-sources.json"


def today_iso() -> str:
    return date.today().isoformat()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "source"


def normalize_url(value: str) -> str:
    value = value.strip()
    if value and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        return f"https://{value}"
    return value


@dataclass
class Source:
    id: str
    title: str
    feed_url: str
    site_url: str = ""
    kind: str = "website"
    profiles: List[str] = field(default_factory=lambda: [os.environ.get("RSS_PROFILE", "me")])
    groups: List[str] = field(default_factory=list)
    status: str = "active"
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    added_at: str = field(default_factory=today_iso)
    last_reviewed_at: Optional[str] = None
    source: str = "manual"

    def to_dict(self) -> Dict[str, Any]:
        normalized_groups = normalize_groups_for_source(self)
        return {
            "id": self.id,
            "title": self.title,
            "feed_url": self.feed_url,
            "site_url": self.site_url,
            "kind": self.kind,
            "profiles": sorted(set(self.profiles)),
            "groups": sorted(set(normalized_groups)),
            "status": self.status,
            "tags": sorted(set(self.tags)),
            "notes": self.notes,
            "added_at": self.added_at,
            "last_reviewed_at": self.last_reviewed_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Source":
        return cls(
            id=data["id"],
            title=data["title"],
            feed_url=data["feed_url"],
            site_url=data.get("site_url", ""),
            kind=data.get("kind", "website"),
            profiles=list(data.get("profiles", [os.environ.get("RSS_PROFILE", "me")])),
            groups=list(data.get("groups", [])),
            status=data.get("status", "active"),
            tags=list(data.get("tags", [])),
            notes=data.get("notes", ""),
            added_at=data.get("added_at", today_iso()),
            last_reviewed_at=data.get("last_reviewed_at"),
            source=data.get("source", "manual"),
        )


class FeedStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or default_sources_path()
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "sources": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.data.get("schema_version", 1),
            "sources": [source.to_dict() for source in self.sources()],
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.data = payload

    def sources(self) -> List[Source]:
        return [Source.from_dict(item) for item in self.data.get("sources", [])]

    def source_by_id(self, source_id: str) -> Optional[Source]:
        return next((source for source in self.sources() if source.id == source_id), None)

    def source_by_feed_url(self, feed_url: str) -> Optional[Source]:
        return next((source for source in self.sources() if source.feed_url == feed_url), None)

    def active_sources(self, profile: str) -> List[Source]:
        return [
            source
            for source in self.sources()
            if source.status == "active" and profile in source.profiles
        ]

    def set_sources(self, sources: List[Source]) -> None:
        for source in sources:
            source.groups = normalize_groups_for_source(source)
        self.data["sources"] = [item.to_dict() for item in sorted(sources, key=lambda item: item.title.lower())]

    def add_or_update(self, source: Source) -> str:
        source.groups = normalize_groups_for_source(source)
        self._validate(source)
        sources = self.sources()
        existing = next((item for item in sources if item.id == source.id or item.feed_url == source.feed_url), None)
        if existing:
            existing.title = source.title or existing.title
            existing.feed_url = source.feed_url or existing.feed_url
            existing.site_url = source.site_url or existing.site_url
            existing.kind = source.kind or existing.kind
            existing.profiles = sorted(set(existing.profiles + source.profiles))
            existing.groups = normalize_groups_for_source(existing, extra_groups=source.groups)
            existing.tags = sorted(set(existing.tags + source.tags))
            existing.notes = source.notes or existing.notes
            existing.source = existing.source if existing.source != "manual" else source.source
            changed_id = existing.id
        else:
            existing_ids = {item.id for item in sources}
            source.id = unique_id(source.id, existing_ids)
            sources.append(source)
            changed_id = source.id

        self.data["sources"] = [item.to_dict() for item in sorted(sources, key=lambda item: item.title.lower())]
        return changed_id

    def add_or_update_many(self, incoming_sources: Iterable[Source]) -> int:
        """Merge a collection of sources and sort once, for large OPML imports."""
        sources = self.sources()
        by_id = {source.id: source for source in sources}
        by_feed_url = {source.feed_url: source for source in sources}
        added_or_updated = 0

        for source in incoming_sources:
            source.groups = normalize_groups_for_source(source)
            self._validate(source)
            existing = by_id.get(source.id) or by_feed_url.get(source.feed_url)
            if existing:
                existing.title = source.title or existing.title
                existing.feed_url = source.feed_url or existing.feed_url
                existing.site_url = source.site_url or existing.site_url
                existing.kind = source.kind or existing.kind
                existing.profiles = sorted(set(existing.profiles + source.profiles))
                existing.groups = normalize_groups_for_source(existing, extra_groups=source.groups)
                existing.tags = sorted(set(existing.tags + source.tags))
                existing.notes = source.notes or existing.notes
                existing.source = existing.source if existing.source != "manual" else source.source
                by_feed_url[existing.feed_url] = existing
            else:
                source.id = unique_id(source.id, by_id)
                sources.append(source)
                by_id[source.id] = source
                by_feed_url[source.feed_url] = source
            added_or_updated += 1

        self.set_sources(sources)
        return added_or_updated

    def set_status(self, source_id: str, status: str) -> Source:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        sources = self.sources()
        for source in sources:
            if source.id == source_id:
                source.status = status
                source.last_reviewed_at = today_iso()
                self.data["sources"] = [item.to_dict() for item in sources]
                return source
        raise KeyError(f"No source found with id '{source_id}'")

    def filtered(
        self,
        profile: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Source]:
        sources = self.sources()
        if profile:
            sources = [source for source in sources if profile in source.profiles]
        if status:
            sources = [source for source in sources if source.status == status]
        if kind:
            sources = [source for source in sources if source.kind == kind]
        return sorted(sources, key=lambda source: (source.status, source.kind, source.title.lower()))

    def _validate(self, source: Source) -> None:
        if source.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {source.status}")
        if source.kind not in VALID_KINDS:
            raise ValueError(f"Invalid kind: {source.kind}")
        if not source.title:
            raise ValueError("Source title is required")
        if not source.feed_url:
            raise ValueError("Source feed_url is required")


def unique_id(base_id: str, existing_ids: Iterable[str]) -> str:
    existing = set(existing_ids)
    if base_id not in existing:
        return base_id
    counter = 2
    while f"{base_id}-{counter}" in existing:
        counter += 1
    return f"{base_id}-{counter}"


def normalize_groups_for_source(source: Source, extra_groups: Optional[List[str]] = None) -> List[str]:
    if source.id in SOURCE_GROUP_OVERRIDES:
        return SOURCE_GROUP_OVERRIDES[source.id]
    if source.kind == "bandcamp":
        return [BANDCAMP_GROUP]

    groups = list(source.groups)
    if extra_groups:
        groups.extend(extra_groups)

    normalized: List[str] = []
    seen: set[str] = set()
    for group in groups:
        cleaned = group.strip()
        if not cleaned:
            continue
        if cleaned.lower() in BANDCAMP_GROUP_ALIASES:
            cleaned = BANDCAMP_GROUP
        key = cleaned.lower()
        if key not in seen:
            normalized.append(cleaned)
            seen.add(key)
    return normalized
