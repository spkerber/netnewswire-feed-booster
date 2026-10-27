import unittest

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.bridge_policy import FeedWaitingForRefresh, due_sources, refresh_route_plan, require_cached_feed


HOUR = 60 * 60

class BridgePolicyTests(unittest.TestCase):
    def test_reader_request_never_falls_back_to_upstream_refresh(self) -> None:
        with self.assertRaisesRegex(FeedWaitingForRefresh, "scheduled refresh"):
            require_cached_feed(None, "Bandcamp")

        self.assertEqual(require_cached_feed("<rss/>", "Bandcamp"), "<rss/>")

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
