import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from netnewswire_feed_booster.feed_store import FeedStore, Source
from netnewswire_feed_booster.generated_migration import (
    apply_generated_source_migration,
    plan_generated_source_migration,
)


class GeneratedSourceMigrationTests(unittest.TestCase):
    def test_replaces_existing_generated_source_when_opml_import_overwrote_its_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = FeedStore(root / "sources.old.json")
            reference.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url="file:///old/bandcamp-example.rss",
                    site_url="https://example.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["old"],
                    source="bandcamp-local-generated",
                )
            )
            reference.save()

            target = FeedStore(root / "sources.trial.json")
            target.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url="https://old-host.example/feeds/old-token/bandcamp/bandcamp-example.rss",
                    site_url="https://example.bandcamp.com/",
                    kind="website",
                    profiles=["trial"],
                    source="bandcamp-local-generated",
                )
            )
            target.save()

            migration = plan_generated_source_migration(
                reference,
                target,
                profile="trial",
                bandcamp_out_dir=root / "exports/bandcamp",
                generated_out_dir=root / "exports/generated",
            )

        self.assertIn("bandcamp-example", migration.replacements)
        rebuilt = migration.replacements["bandcamp-example"]
        self.assertEqual(rebuilt.kind, "bandcamp")
        self.assertTrue(rebuilt.feed_url.endswith("/exports/bandcamp/bandcamp-example.rss"))

    def test_keeps_canonical_generated_source_when_only_review_dates_differ(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = FeedStore(root / "sources.old.json")
            reference.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url="file:///old/bandcamp-example.rss",
                    site_url="https://example.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["old"],
                    source="bandcamp-local-generated",
                )
            )
            reference.save()

            target = FeedStore(root / "sources.trial.json")
            target.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url=(root / "exports/bandcamp/bandcamp-example.rss").resolve().as_uri(),
                    site_url="https://example.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["trial"],
                    source="bandcamp-local-generated",
                    added_at="2020-01-01",
                    last_reviewed_at="2025-01-01",
                )
            )
            target.save()

            migration = plan_generated_source_migration(
                reference,
                target,
                profile="trial",
                bandcamp_out_dir=root / "exports/bandcamp",
                generated_out_dir=root / "exports/generated",
            )

        self.assertEqual(migration.replacements, {})

    def test_replaces_legacy_hosted_feed_with_fresh_local_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference_path = root / "sources.old.json"
            target_path = root / "sources.trial.json"
            reference = FeedStore(reference_path)
            reference.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url="file:///old/bandcamp-example.rss",
                    site_url="https://example.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["old"],
                    groups=["Bandcamp"],
                    source="bandcamp-local-generated",
                )
            )
            reference.save()

            target = FeedStore(target_path)
            target.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url="https://old-host.example/feeds/old-token/bandcamp/bandcamp-example.rss",
                    profiles=["trial"],
                    groups=["Bandcamp"],
                    source="netnewswire-import",
                )
            )
            target.save()

            migration = plan_generated_source_migration(
                reference,
                target,
                profile="trial",
                bandcamp_out_dir=root / "exports/bandcamp",
                generated_out_dir=root / "exports/generated",
            )
            self.assertEqual(len(migration.replacements), 1)
            self.assertEqual(migration.conflicts, [])

            apply_generated_source_migration(target, migration)
            rebuilt = FeedStore(target_path).source_by_id("bandcamp-example")

        self.assertEqual(rebuilt.profiles, ["trial"])
        self.assertEqual(rebuilt.kind, "bandcamp")
        self.assertEqual(rebuilt.site_url, "https://example.bandcamp.com/")
        self.assertTrue(rebuilt.feed_url.endswith("/exports/bandcamp/bandcamp-example.rss"))

    def test_reports_non_generated_id_collision_without_overwriting(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = FeedStore(root / "sources.old.json")
            reference.add_or_update(
                Source(
                    id="shared-id",
                    title="Bandcamp: Example",
                    feed_url="file:///old/shared-id.rss",
                    site_url="https://example.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["old"],
                    groups=["Bandcamp"],
                    source="bandcamp-local-generated",
                )
            )
            reference.save()

            target = FeedStore(root / "sources.trial.json")
            target.add_or_update(
                Source(
                    id="shared-id",
                    title="Different direct feed",
                    feed_url="https://example.com/feed.xml",
                    profiles=["trial"],
                )
            )
            target.save()

            migration = plan_generated_source_migration(
                reference,
                target,
                profile="trial",
                bandcamp_out_dir=root / "exports/bandcamp",
                generated_out_dir=root / "exports/generated",
            )

        self.assertEqual(len(migration.replacements), 0)
        self.assertEqual(len(migration.conflicts), 1)

    def test_replaces_legacy_feed_by_site_url_when_opml_id_differs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = FeedStore(root / "sources.old.json")
            reference.add_or_update(
                Source(
                    id="radio-hydefm-archives",
                    title="HydeFM Archives",
                    feed_url="file:///old/radio-hydefm-archives.rss",
                    site_url="https://hydefm.com/archives/",
                    kind="other",
                    profiles=["old"],
                    groups=["HydeFM"],
                    source="radio-local-generated",
                )
            )
            reference.save()

            target = FeedStore(root / "sources.trial.json")
            target.add_or_update(
                Source(
                    id="hydefm-archives",
                    title="HydeFM Archives",
                    feed_url="https://old-host.example/feeds/old-token/generated/hydefm-archives.rss",
                    site_url="https://hydefm.com/archives/",
                    profiles=["trial"],
                    groups=["HydeFM"],
                    source="netnewswire-import",
                )
            )
            target.save()

            migration = plan_generated_source_migration(
                reference,
                target,
                profile="trial",
                bandcamp_out_dir=root / "exports/bandcamp",
                generated_out_dir=root / "exports/generated",
            )
            apply_generated_source_migration(target, migration)
            rebuilt = FeedStore(root / "sources.trial.json")

        self.assertEqual(list(migration.replacements), ["hydefm-archives"])
        self.assertIsNone(rebuilt.source_by_id("hydefm-archives"))
        self.assertIsNotNone(rebuilt.source_by_id("radio-hydefm-archives"))
