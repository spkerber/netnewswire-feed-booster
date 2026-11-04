import io
import os
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.cli import main
from netnewswire_feed_booster.feed_store import EmailIntake, FeedStore, Source
from netnewswire_feed_booster.local_feeds import format_local_feed_status, local_feed_group


DAY = 24 * 60 * 60
NOW = 1_800_000_000.0


def touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<rss/>", encoding="utf-8")
    os.utime(path, (when, when))


class LocalFeedStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_dir = Path(tmp.name)

    def test_groups_count_newest_stale_and_missing_feeds(self) -> None:
        touch(self.out_dir / "bandcamp-fresh.rss", NOW - 2 * DAY)
        touch(self.out_dir / "bandcamp-failing.rss", NOW - 40 * DAY)

        group = local_feed_group("bandcamp", ["bandcamp-fresh", "bandcamp-failing", "bandcamp-new"], self.out_dir, NOW, 14)

        self.assertEqual((group.total, group.last_refresh), (3, NOW - 2 * DAY))
        self.assertEqual(group.stale, [("bandcamp-failing", NOW - 40 * DAY)])
        self.assertEqual(group.missing, ["bandcamp-new"])

    def test_one_new_feed_does_not_pass_for_a_refresh(self) -> None:
        for number in range(5):
            touch(self.out_dir / f"bandcamp-{number}.rss", NOW - 20 * DAY)
        touch(self.out_dir / "bandcamp-new.rss", NOW - DAY)
        ids = [f"bandcamp-{number}" for number in range(5)] + ["bandcamp-new"]

        group = local_feed_group("bandcamp", ids, self.out_dir, NOW, 14)
        report = format_local_feed_status([group], self.out_dir, "me", NOW, 14)

        self.assertEqual(group.last_refresh, NOW - 20 * DAY)
        self.assertIn("Bandcamp feeds have not been rebuilt in 14 days", report)

    def test_current_feeds_get_no_advice(self) -> None:
        touch(self.out_dir / "bandcamp-a.rss", NOW - DAY)
        groups = [local_feed_group("bandcamp", ["bandcamp-a"], self.out_dir, NOW, 14), local_feed_group("other", [], self.out_dir, NOW, 14)]

        report = format_local_feed_status(groups, self.out_dir, "me", NOW, 14)

        self.assertIn("bandcamp       1 feeds  last refreshed 2027-01-14, 1 day ago; 0 not rebuilt in 14 days; 0 missing", report)
        self.assertIn("other          0 feeds  none built yet", report)
        self.assertNotIn("refresh-local", report)

    def test_old_bandcamp_feeds_point_at_both_ways_to_catch_up(self) -> None:
        touch(self.out_dir / "bandcamp-a.rss", NOW - 20 * DAY)
        touch(self.out_dir / "nts-a.rss", NOW - 20 * DAY)
        groups = [
            local_feed_group("bandcamp", ["bandcamp-a"], self.out_dir, NOW, 14),
            local_feed_group("other", ["nts-a"], self.out_dir, NOW, 14),
        ]

        report = format_local_feed_status(groups, self.out_dir, "me", NOW, 14)

        self.assertIn("Bandcamp feeds have not been rebuilt in 14 days. Mac only: run ./scripts/netnewswire_workflow.sh refresh-local.", report)
        self.assertIn("Hosted bridge: run ./scripts/netnewswire_workflow.sh catch-up", report)
        self.assertIn("the bridge refreshes these itself", report)
        self.assertIn("Pass --list", report)

    def test_list_names_each_stale_and_missing_feed(self) -> None:
        touch(self.out_dir / "bandcamp-failing.rss", NOW - 40 * DAY)
        groups = [local_feed_group("bandcamp", ["bandcamp-failing", "bandcamp-new"], self.out_dir, NOW, 14)]

        report = format_local_feed_status(groups, self.out_dir, "me", NOW, 14, list_sources=True)

        self.assertIn("bandcamp: not rebuilt in 14 days (1):\n  bandcamp-failing\t2026-12-06, 40 days ago", report)
        self.assertIn("bandcamp: no local file yet (1):\n  bandcamp-new", report)
        self.assertNotIn("Pass --list", report)


class LocalFeedStatusCliTests(unittest.TestCase):
    def test_counts_active_generated_feeds_and_skips_direct_and_email_sources(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            out_dir = root / "generated"
            store = FeedStore(root / "sources.json")
            sources = [
                Source(id="bandcamp-artist", title="Artist", feed_url="file:///x.rss", site_url="https://artist.bandcamp.com/", kind="bandcamp", profiles=["me"], source="bandcamp-local-generated"),
                Source(id="nts-show", title="Show", feed_url="file:///y.rss", site_url="https://www.nts.live/shows/show", kind="other", profiles=["me"], source="nts-local-generated"),
                Source(id="blog", title="Blog", feed_url="https://blog.example.com/feed.xml", profiles=["me"]),
                Source(
                    id="email-letters", title="Letters", feed_url="file:///z.rss", kind="newsletter", profiles=["me"], source="email-inbound",
                    email=EmailIntake("inbox+letters-1@feeds.example.com", ["letters.example.net"]),
                ),
                Source(id="bandcamp-paused", title="Paused", feed_url="file:///p.rss", site_url="https://paused.bandcamp.com/", kind="bandcamp", profiles=["me"], status="paused", source="bandcamp-local-generated"),
            ]
            for source in sources:
                store.add_or_update(source)
            store.save()
            touch(out_dir / "bandcamp-artist.rss", time.time() - 30 * DAY)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "--data", str(root / "sources.json"), "--private-data", str(root / "private.json"), "--history", str(root / "history.json"),
                        "local-feed-status", "--out-dir", str(out_dir), "--list",
                    ]
                )

        report = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertRegex(report, r"bandcamp\s+1 feeds  last refreshed \S+, 30 days ago; 1 not rebuilt in 14 days; 0 missing")
        self.assertRegex(report, r"other\s+1 feeds  none built yet; 0 not rebuilt in 14 days; 1 missing")
        self.assertIn("  nts-show", report)
        for absent in ("blog", "email-letters", "bandcamp-paused"):
            self.assertNotIn(absent, report)


if __name__ == "__main__":
    unittest.main()
