import unittest

from netnewswire_feed_booster.bridge_health import (
    RefreshState,
    dump_refresh_state,
    format_bridge_health,
    parse_refresh_state,
    record_refresh,
    short_error,
    summarize_bridge_health,
)
from netnewswire_feed_booster.bridge_policy import due_sources


HOUR = 60 * 60
DAY = 24 * HOUR
NOW = 1_800_000_000.0


class RefreshStateTests(unittest.TestCase):
    def test_state_written_before_success_tracking_still_reads(self) -> None:
        state = parse_refresh_state('{"last_attempt": 1792000000.5}')

        self.assertEqual(state, RefreshState(last_attempt=1792000000.5))

    def test_unreadable_state_reads_as_never_attempted(self) -> None:
        for text in ("", "not json", "[1, 2]", '{"last_attempt": "soon"}'):
            with self.subTest(text=text):
                self.assertIsNone(parse_refresh_state(text).last_attempt)

    def test_a_success_clears_failures_and_a_failure_keeps_the_last_success(self) -> None:
        succeeded = record_refresh(RefreshState(), NOW - DAY)
        failed_once = record_refresh(succeeded, NOW - HOUR, ConnectionError("reset"))
        failed_twice = record_refresh(failed_once, NOW, ValueError("Bandcamp returned 403"))
        recovered = record_refresh(failed_twice, NOW + HOUR)

        self.assertEqual(succeeded, RefreshState(last_attempt=NOW - DAY, last_success=NOW - DAY))
        self.assertEqual(failed_twice.last_success, NOW - DAY)
        self.assertEqual(failed_twice.consecutive_failures, 2)
        self.assertEqual(failed_twice.last_error, "ValueError: Bandcamp returned 403")
        self.assertEqual(recovered, RefreshState(last_attempt=NOW + HOUR, last_success=NOW + HOUR))

    def test_state_round_trips_without_empty_fields(self) -> None:
        state = RefreshState(last_attempt=NOW, last_success=NOW - DAY, last_error="HTTPError: HTTP Error 403: Forbidden", consecutive_failures=3)

        self.assertEqual(parse_refresh_state(dump_refresh_state(state)), state)
        self.assertEqual(dump_refresh_state(RefreshState(last_attempt=NOW, last_success=NOW)), '{"last_attempt": 1800000000.0, "last_success": 1800000000.0, "consecutive_failures": 0}')

    def test_errors_are_kept_short_and_without_urls(self) -> None:
        error = ValueError("Unsafe fetch URL: https://example.bandcamp.com/music?token=abc123 " + "x" * 400)

        text = short_error(error)

        self.assertNotIn("example.bandcamp.com", text)
        self.assertNotIn("abc123", text)
        self.assertIn("[url]", text)
        self.assertLessEqual(len(text), 160)


class SummaryTests(unittest.TestCase):
    def summarize(self):
        states = {
            "healthy": RefreshState(last_attempt=NOW - HOUR, last_success=NOW - HOUR),
            "failing-recently-fine": RefreshState(last_attempt=NOW - HOUR, last_success=NOW - DAY, last_error="HTTPError: HTTP Error 503", consecutive_failures=1),
            "failing-for-weeks": RefreshState(last_attempt=NOW - HOUR, last_success=NOW - 60 * DAY, last_error="HTTPError: HTTP Error 403: Forbidden", consecutive_failures=40),
            "old-format-recent-cache": RefreshState(last_attempt=NOW - HOUR),
            "old-format-old-cache": RefreshState(last_attempt=NOW - 70 * DAY),
        }
        cached = {
            "healthy": NOW - HOUR,
            "failing-recently-fine": NOW - DAY,
            "failing-for-weeks": NOW - 60 * DAY,
            "old-format-recent-cache": NOW - 2 * HOUR,
            "old-format-old-cache": NOW - 70 * DAY,
        }
        seeded = {"never-refreshed-with-seed", "old-format-old-cache"}
        source_ids = list(states) + ["never-refreshed-with-seed", "never-refreshed-no-seed"]
        return summarize_bridge_health(source_ids, states, cached, seeded, NOW, interval_seconds=12 * HOUR)

    def test_sources_are_sorted_by_what_readers_get(self) -> None:
        health = self.summarize()

        self.assertEqual(health.total, 7)
        self.assertEqual(health.healthy, ["healthy", "old-format-recent-cache"])
        self.assertEqual(health.never_attempted, ["never-refreshed-no-seed", "never-refreshed-with-seed"])
        self.assertEqual([row[0] for row in health.failing], ["failing-for-weeks", "failing-recently-fine"])
        # Stale means no success within three refresh intervals (36 hours here).
        self.assertEqual([row[0] for row in health.stale], ["failing-for-weeks", "old-format-old-cache"])
        self.assertEqual(health.seed_only, ["never-refreshed-with-seed"])
        self.assertEqual(health.nothing_to_serve, ["never-refreshed-no-seed"])
        self.assertTrue(health.has_problems)

    def test_report_leads_with_counts_and_caps_long_lists(self) -> None:
        health = self.summarize()
        health.never_attempted = [f"bandcamp-{index:03d}" for index in range(50)]

        report = format_bridge_health(health, list_limit=10)

        self.assertIn("7 active generated sources. Target refresh every 12h; stale after 36h without a success.", report)
        self.assertIn("  failing                2", report)
        self.assertIn("failing-for-weeks\t40 in a row\tHTTPError: HTTP Error 403: Forbidden", report)
        self.assertIn("Nothing to serve; readers get 'waiting for its scheduled refresh' (1):", report)
        self.assertIn("  ... and 40 more", report)
        self.assertLess(report.index("failing-for-weeks\t40"), report.index("failing-recently-fine\t1"))

    def test_a_route_refreshed_on_schedule_reports_healthy(self) -> None:
        sources = [f"nts-{index}" for index in range(10)]
        states = {}
        for hour in range(24):
            now = NOW - (24 - hour) * HOUR
            for source_id in due_sources(sources, lambda item: states.get(item, RefreshState()).last_attempt, now, 12 * HOUR, 20):
                error = ConnectionError("down") if source_id == "nts-3" else None
                states[source_id] = record_refresh(states.get(source_id, RefreshState()), now, error)

        # Only a successful refresh writes the cache, so the failing source has none.
        cached = {source_id: NOW for source_id in sources if source_id != "nts-3"}
        health = summarize_bridge_health(sources, states, cached, set(), NOW, 12 * HOUR)

        self.assertEqual(len(health.healthy), 9)
        self.assertEqual([(row[0], row[1]) for row in health.failing], [("nts-3", 2)])
        self.assertEqual([row[0] for row in health.stale], ["nts-3"])


if __name__ == "__main__":
    unittest.main()
