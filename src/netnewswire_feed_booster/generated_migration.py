from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlsplit, urlunsplit

from .feed_store import FeedStore, Source


GENERATED_SOURCE_LABELS = {
    "bandcamp-local-generated",
    "nts-local-generated",
    "radio-local-generated",
}


@dataclass
class GeneratedSourceMigration:
    replacements: Dict[str, Source]
    additions: List[Source]
    removals: List[str]
    conflicts: List[str]
    source_counts: Dict[str, int]

    @property
    def total(self) -> int:
        return len(self.replacements) + len(self.additions)


def plan_generated_source_migration(
    reference_store: FeedStore,
    target_store: FeedStore,
    profile: str,
    bandcamp_out_dir: Path,
    generated_out_dir: Path,
) -> GeneratedSourceMigration:
    reference_sources = [
        source
        for source in reference_store.sources()
        if source.status == "active" and source.source in GENERATED_SOURCE_LABELS
    ]
    target_sources = target_store.sources()
    target_by_id = {source.id: source for source in target_sources}
    replacements: Dict[str, Source] = {}
    additions: List[Source] = []
    removals: List[str] = []
    conflicts: List[str] = []

    for source in reference_sources:
        rebuilt = rebuilt_generated_source(
            source,
            profile=profile,
            bandcamp_out_dir=bandcamp_out_dir,
            generated_out_dir=generated_out_dir,
        )
        existing = target_by_id.get(rebuilt.id)
        legacy_site_matches = [
            target
            for target in target_sources
            if is_legacy_generated_feed(target) and same_site_url(target.site_url, rebuilt.site_url)
        ]
        if existing and existing.source == rebuilt.source:
            if not has_canonical_generated_metadata(existing, rebuilt):
                replacements[existing.id] = rebuilt
            removals.extend(source.id for source in legacy_site_matches if source.id != existing.id)
        elif existing and is_legacy_generated_feed(existing):
            replacements[existing.id] = rebuilt
        elif existing:
            conflicts.append(
                f"{rebuilt.id}: target has a non-generated feed URL; refusing to overwrite {existing.feed_url}"
            )
        elif len(legacy_site_matches) == 1:
            replacements[legacy_site_matches[0].id] = rebuilt
        elif len(legacy_site_matches) > 1:
            conflicts.append(f"{rebuilt.id}: multiple legacy generated feeds share {rebuilt.site_url}")
        else:
            additions.append(rebuilt)

    return GeneratedSourceMigration(
        replacements=replacements,
        additions=additions,
        removals=sorted(set(removals)),
        conflicts=conflicts,
        source_counts=dict(sorted(Counter(source.source for source in reference_sources).items())),
    )


def apply_generated_source_migration(target_store: FeedStore, migration: GeneratedSourceMigration) -> None:
    if migration.conflicts:
        raise ValueError("Cannot apply generated-source migration with conflicts")

    removed_ids = set(migration.replacements) | set(migration.removals)
    sources = [source for source in target_store.sources() if source.id not in removed_ids]
    sources.extend(migration.replacements.values())
    sources.extend(migration.additions)
    target_store.set_sources(sources)
    target_store.save()


def rebuilt_generated_source(
    source: Source,
    profile: str,
    bandcamp_out_dir: Path,
    generated_out_dir: Path,
) -> Source:
    out_dir = bandcamp_out_dir if source.source == "bandcamp-local-generated" else generated_out_dir
    return Source(
        id=source.id,
        title=source.title,
        feed_url=(out_dir / f"{source.id}.rss").resolve().as_uri(),
        site_url=source.site_url,
        kind=source.kind,
        profiles=[profile],
        groups=list(source.groups),
        status="active",
        tags=list(source.tags),
        notes=source.notes,
        source=source.source,
    )


def is_legacy_generated_feed(source: Source) -> bool:
    feed_url = source.feed_url
    return feed_url.startswith("file://") or "/feeds/" in feed_url and (
        "/bandcamp/" in feed_url or "/generated/" in feed_url
    )


def same_site_url(left: str, right: str) -> bool:
    return normalized_site_url(left) == normalized_site_url(right)


def has_canonical_generated_metadata(existing: Source, rebuilt: Source) -> bool:
    """Compare generated-feed identity without treating review timestamps as drift."""
    existing_metadata = existing.to_dict()
    rebuilt_metadata = rebuilt.to_dict()
    for field in ("added_at", "last_reviewed_at"):
        existing_metadata.pop(field, None)
        rebuilt_metadata.pop(field, None)
    return existing_metadata == rebuilt_metadata


def normalized_site_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
