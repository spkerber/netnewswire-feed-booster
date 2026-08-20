from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import os
from typing import Any, Dict, Iterable, List, Optional


VALID_STATUSES = {"active", "candidate", "paused", "unsubscribed"}
VALID_KINDS = {"website", "substack", "youtube", "bandcamp", "newsletter", "podcast", "other"}
ACCOUNT_IDENTITY_KINDS = {"bandcamp", "substack", "youtube"}
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_profile_id(profile: str) -> str:
    profile = profile.strip()
    if not PROFILE_ID_PATTERN.fullmatch(profile):
        raise ValueError(
            "Profile IDs must start with a letter or number and contain only "
            "letters, numbers, hyphens, or underscores (64 characters maximum)."
        )
    return profile


def default_sources_path(profile: str = "", *, prefer_configured: bool = False) -> Path:
    configured = os.environ.get("RSS_SOURCES_FILE")
    if configured and (prefer_configured or not profile):
        return Path(configured)
    if profile:
        profile = validate_profile_id(profile)
        return repo_root() / "data" / f"sources.{profile}.json"
    profile = os.environ.get("RSS_PROFILE", "")
    if profile:
        profile = validate_profile_id(profile)
        return repo_root() / "data" / f"sources.{profile}.json"
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


def source_id_from_title(title: str, stable_value: str) -> str:
    """Return a readable ID without exposing a URL when a title is non-ASCII-only."""
    source_id = slugify(title)
    if source_id != "source":
        return source_id
    digest = hashlib.sha256(stable_value.encode("utf-8")).hexdigest()[:12]
    return f"source-{digest}"


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
            "groups": normalized_groups,
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
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        # Atomic write (temp file + os.replace): a crash or kill mid-write must
        # never leave a truncated registry. This matters more now that long batch
        # commands checkpoint via save() dozens of times per run rather than once
        # — each call is a fresh window where a bad-timed interruption could
        # otherwise destroy the whole file, not just this run's progress.
        tmp_path = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, self.path)
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

    def _existing_match(self, sources: List[Source], source: Source) -> Optional[Source]:
        from .feed_identity import canonical_url

        canonical_site = canonical_url(source.site_url)
        return next(
            (
                item
                for item in sources
                if item.id == source.id
                or item.feed_url == source.feed_url
                or (
                    source.kind in ACCOUNT_IDENTITY_KINDS
                    and item.kind == source.kind
                    and canonical_site
                    and canonical_url(item.site_url) == canonical_site
                )
            ),
            None,
        )

    def resolve_source_id(self, source: Source) -> str:
        """Return the id `add_or_update` would settle on, without changing anything.

        Generated feeds are written to `<source_id>.rss` before the source is
        merged, and the hosted bridge seeds and serves strictly by id. A caller
        that writes the file first needs the final id up front, or the file
        lands under a name nothing ever reads.
        """
        sources = self.sources()
        existing = self._existing_match(sources, source)
        if existing:
            if existing.id == "source" and source.id != "source":
                return source.id
            return existing.id
        return unique_id(source.id, {item.id for item in sources})

    def add_or_update(self, source: Source) -> str:
        source.groups = normalize_groups_for_source(source)
        self._validate(source)
        sources = self.sources()
        existing = self._existing_match(sources, source)
        if existing:
            if existing.id == "source" and source.id != "source":
                existing.id = source.id
            existing.title = source.title or existing.title
            existing.feed_url = source.feed_url or existing.feed_url
            existing.site_url = source.site_url or existing.site_url
            existing.kind = source.kind or existing.kind
            existing.profiles = sorted(set(existing.profiles + source.profiles))
            existing.groups = source.groups or existing.groups
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
        from .feed_identity import canonical_url

        sources = self.sources()
        by_id = {source.id: source for source in sources}
        by_feed_url = {source.feed_url: source for source in sources}
        by_account_site = {
            (source.kind, canonical_url(source.site_url)): source
            for source in sources
            if source.kind in ACCOUNT_IDENTITY_KINDS and canonical_url(source.site_url)
        }
        added_or_updated = 0

        for source in incoming_sources:
            source.groups = normalize_groups_for_source(source)
            self._validate(source)
            account_key = (source.kind, canonical_url(source.site_url))
            existing = (
                by_id.get(source.id)
                or by_feed_url.get(source.feed_url)
                or (by_account_site.get(account_key) if source.kind in ACCOUNT_IDENTITY_KINDS else None)
            )
            if existing:
                existing.title = source.title or existing.title
                existing.feed_url = source.feed_url or existing.feed_url
                existing.site_url = source.site_url or existing.site_url
                existing.kind = source.kind or existing.kind
                existing.profiles = sorted(set(existing.profiles + source.profiles))
                existing.groups = source.groups or existing.groups
                existing.tags = sorted(set(existing.tags + source.tags))
                existing.notes = source.notes or existing.notes
                existing.source = existing.source if existing.source != "manual" else source.source
                by_feed_url[existing.feed_url] = existing
                if existing.kind in ACCOUNT_IDENTITY_KINDS:
                    updated_account_key = (existing.kind, canonical_url(existing.site_url))
                    if updated_account_key[1]:
                        by_account_site[updated_account_key] = existing
            else:
                source.id = unique_id(source.id, by_id)
                sources.append(source)
                by_id[source.id] = source
                by_feed_url[source.feed_url] = source
                if source.kind in ACCOUNT_IDENTITY_KINDS and account_key[1]:
                    by_account_site[account_key] = source
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

    def set_folder_path(self, source_id: str, folder_path: List[str]) -> Source:
        sources = self.sources()
        for source in sources:
            if source.id == source_id:
                source.groups = normalize_groups_for_source(source, folder_path=folder_path)
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


def normalize_groups_for_source(source: Source, folder_path: Optional[List[str]] = None) -> List[str]:
    """Normalize one ordered OPML folder path without imposing a taxonomy."""
    normalized: List[str] = []
    for group in folder_path if folder_path is not None else source.groups:
        cleaned = group.strip()
        if not cleaned:
            continue
        normalized.append(cleaned)
    return normalized
