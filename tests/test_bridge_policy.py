import unittest

from netnewswire_feed_booster.bridge_policy import FeedWaitingForRefresh, due_sources, refresh_route_plan, require_cached_feed


class BridgePolicyTests(unittest.TestCase):
    def test_reader_request_never_falls_back_to_upstream_refresh(self) -> None:
        with self.assertRaisesRegex(FeedWaitingForRefresh, "scheduled refresh"):
            require_cached_feed(None, "Bandcamp")

        self.assertEqual(require_cached_feed("<rss/>", "Bandcamp"), "<rss/>")

    def test_due_source_selection_has_a_hard_per_run_limit(self) -> None:
        sources = ["one", "two", "three"]

        self.assertEqual(due_sources(sources, lambda _source: True, limit=2), ["one", "two"])
        with self.assertRaises(ValueError):
            due_sources(sources, lambda _source: True, limit=0)

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
