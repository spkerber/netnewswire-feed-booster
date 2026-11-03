import unittest

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.bridge_policy import (
    FeedWaitingForRefresh,
    bandcamp_refresh_setting,
    due_sources,
    refresh_route_plan,
    require_cached_feed,
    served_copy,
)


HOUR = 60 * 60


def built_rss(last_build_date: str = "") -> str:
    build = f"<lastBuildDate>{last_build_date}</lastBuildDate>" if last_build_date else ""
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Fixture</title>{build}</channel></rss>\n'


class BridgePolicyTests(unittest.TestCase):
    def test_reader_request_never_falls_back_to_upstream_refresh(self) -> None:
        with self.assertRaisesRegex(FeedWaitingForRefresh, "scheduled refresh"):
            require_cached_feed(None, "Bandcamp")

        self.assertEqual(require_cached_feed("<rss/>", "Bandcamp"), "<rss/>")

    def test_the_cache_is_served_unless_the_deploy_shipped_a_newer_build(self) -> None:
        october = built_rss("Thu, 01 Oct 2026 12:00:00 +0000")
        november = built_rss("Mon, 02 Nov 2026 20:51:00 +0000")
        undated = built_rss()

        self.assertIsNone(served_copy(None, None))
        self.assertEqual(served_copy(october, None), october)
        self.assertEqual(served_copy(None, november), november)
        # A local refresh plus a redeploy reaches readers past an older cache.
        self.assertEqual(served_copy(october, november), november)
        self.assertEqual(served_copy(november, october), november)
        self.assertEqual(served_copy(november, november.replace("Fixture", "Seed")), november)
        # Without a build date on both sides, the cache wins as it always has.
        self.assertEqual(served_copy(undated, november), undated)
        self.assertEqual(served_copy(october, undated), october)
        self.assertEqual(served_copy(october, built_rss("not a date")), october)

    def test_bandcamp_refresh_setting_is_on_unless_switched_off(self) -> None:
        for value, expected in ((None, "on"), ("", "on"), (" ON ", "on"), ("off", "off"), ("Off", "off")):
            with self.subTest(value=value):
                self.assertEqual(bandcamp_refresh_setting(value), expected)
        with self.assertRaisesRegex(ValueError, "RSS_BRIDGE_BANDCAMP_REFRESH must be on or off"):
            bandcamp_refresh_setting("false")

    def test_due_source_selection_has_a_hard_per_run_limit(self) -> None:
        sources = ["one", "two", "three"]

        self.assertEqual(due_sources(sources, lambda _source: None, now=0, interval_seconds=HOUR, limit=2), ["one", "two"])
        with self.assertRaises(ValueError):
            due_sources(sources, lambda _source: None, now=0, interval_seconds=HOUR, limit=0)

    def test_due_sources_take_the_longest_waiting_first(self) -> None:
        now = 100 * HOUR
        last_attempts = {
            "refreshed-an-hour-ago": now - HOUR,
            "refreshed-a-day-ago": now - 24 * HOUR,
            "never-refreshed": None,
            "refreshed-two-days-ago": now - 48 * HOUR,
            "also-never-refreshed": None,
        }

        selected = due_sources(list(last_attempts), last_attempts.get, now=now, interval_seconds=12 * HOUR, limit=10)

        # Not yet due is left out; never-attempted keep registry order ahead of the rest.
        self.assertEqual(
            selected,
            ["never-refreshed", "also-never-refreshed", "refreshed-two-days-ago", "refreshed-a-day-ago"],
        )

    def test_an_over_capacity_route_still_reaches_every_source(self) -> None:
        # The fresh profile's shape: 1,184 Bandcamp sources, 20 per hourly run, a
        # 12-hour target. Picking due sources in registry order kept cycling the
        # first ~240 and never reached the rest.
        sources = [f"bandcamp-{index:04d}" for index in range(1184)]
        last_attempts: dict = {}
        attempts_per_source = {source: 0 for source in sources}

        for hour in range(120):
            now = hour * HOUR
            for source in due_sources(sources, last_attempts.get, now=now, interval_seconds=12 * HOUR, limit=20):
                last_attempts[source] = now
                attempts_per_source[source] += 1
            if hour == 59:
                self.assertTrue(all(attempts_per_source.values()), "every source refreshed in the first 60 runs")

        self.assertEqual(min(attempts_per_source.values()), 2)
        self.assertEqual(max(attempts_per_source.values()), 3)

    def test_refresh_plan_exposes_source_volume_capacity(self) -> None:
        plan = refresh_route_plan("bandcamp", 106, batch_size=20, schedule_hours=1, refresh_interval_hours=12)

        self.assertEqual(plan.batches_needed, 6)
        self.assertEqual(plan.first_pass_hours, 6)
        self.assertEqual(plan.capacity_per_interval, 240)
        self.assertTrue(plan.meets_target)

        overloaded = refresh_route_plan("bandcamp", 241, batch_size=20, schedule_hours=1, refresh_interval_hours=12)
        self.assertFalse(overloaded.meets_target)


if __name__ == "__main__":
    unittest.main()
