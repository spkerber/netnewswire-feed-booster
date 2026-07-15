import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from netnewswire_feed_booster.feed_store import FeedStore, Source
from netnewswire_feed_booster.generated_migration import (
    apply_generated_source_migration,
    plan_generated_source_migration,
)


class GeneratedSourceMigrationTests(unittest.TestCase):
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
