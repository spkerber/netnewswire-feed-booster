from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .feed_store import FeedStore, Source


GENERATED_SOURCE_LABELS = {
    "bandcamp-local-generated",
    "nts-local-generated",
    "radio-local-generated",
}


@dataclass
class GeneratedSourceMigration:
    replacements: List[Source]
    additions: List[Source]
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
    target_by_id = {source.id: source for source in target_store.sources()}
    replacements: List[Source] = []
    additions: List[Source] = []
    conflicts: List[str] = []

    for source in reference_sources:
        rebuilt = rebuilt_generated_source(
            source,
            profile=profile,
            bandcamp_out_dir=bandcamp_out_dir,
            generated_out_dir=generated_out_dir,
        )
        existing = target_by_id.get(rebuilt.id)
        if not existing:
            additions.append(rebuilt)
        elif is_legacy_generated_feed(existing):
            replacements.append(rebuilt)
        else:
            conflicts.append(
                f"{rebuilt.id}: target has a non-generated feed URL; refusing to overwrite {existing.feed_url}"
            )

    return GeneratedSourceMigration(
        replacements=replacements,
        additions=additions,
        conflicts=conflicts,
        source_counts=dict(sorted(Counter(source.source for source in reference_sources).items())),
    )


def apply_generated_source_migration(target_store: FeedStore, migration: GeneratedSourceMigration) -> None:
    if migration.conflicts:
        raise ValueError("Cannot apply generated-source migration with conflicts")

    replacements = {source.id: source for source in migration.replacements}
    sources = [replacements.get(source.id, source) for source in target_store.sources()]
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
