import unittest
import xml.etree.ElementTree as ET

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.bridge_health import (
    RefreshState,
    dump_refresh_state,
    format_bridge_health,
    parse_refresh_state,
    record_refresh,
    render_status_feed,
    short_error,
    summarize_bridge_health,
)


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
        # What is written to the volume reads back unchanged.
        for state in (failed_twice, recovered):
            self.assertEqual(parse_refresh_state(dump_refresh_state(state)), state)

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
        self.assertNotIn("refresh off", report)

    def test_a_route_with_refresh_off_is_reported_apart_from_live_failures(self) -> None:
        states = {
            "bandcamp-blocked": RefreshState(last_attempt=NOW - 30 * DAY, last_success=NOW - 32 * DAY, last_error="BandcampBotCheck: Bandcamp served a bot check", consecutive_failures=3),
            "nts-healthy": RefreshState(last_attempt=NOW - HOUR, last_success=NOW - HOUR),
        }
        cached = {"bandcamp-blocked": NOW - 32 * DAY, "nts-healthy": NOW - HOUR}
        source_ids = ["bandcamp-blocked", "bandcamp-seed-only", "bandcamp-no-copy", "nts-healthy"]

        health = summarize_bridge_health(
            source_ids,
            states,
            cached,
            {"bandcamp-seed-only"},
            NOW,
            interval_seconds=12 * HOUR,
            refresh_off={"bandcamp-blocked", "bandcamp-seed-only", "bandcamp-no-copy"},
        )
        report = format_bridge_health(health)

        self.assertEqual(health.refresh_off, ["bandcamp-blocked", "bandcamp-no-copy", "bandcamp-seed-only"])
        self.assertEqual(health.healthy, ["nts-healthy"])
        self.assertEqual((health.failing, health.stale, health.never_attempted), ([], [], []))
        # What readers get still counts: one source has no copy at all.
        self.assertEqual(health.seed_only, ["bandcamp-seed-only"])
        self.assertEqual(health.nothing_to_serve, ["bandcamp-no-copy"])
        self.assertTrue(health.has_problems)
        self.assertIn("  refresh off            3  (serving the last cached or deploy-time copy)", report)

    def test_refresh_off_reports_the_last_catch_up_and_what_it_missed(self) -> None:
        ids = ["bandcamp-a", "bandcamp-b", "bandcamp-failing", "bandcamp-undated"]
        built = {"bandcamp-a": NOW - 3 * DAY, "bandcamp-b": NOW - 3 * DAY - HOUR, "bandcamp-failing": NOW - 70 * DAY}

        health = summarize_bridge_health(ids, {}, {}, set(ids), NOW, 12 * HOUR, refresh_off=set(ids), built=built)
        report = format_bridge_health(health)

        self.assertEqual(health.last_catch_up, NOW - 3 * DAY - HOUR)
        self.assertEqual(health.missed_catch_up, [("bandcamp-failing", NOW - 70 * DAY), ("bandcamp-undated", None)])
        self.assertFalse(health.catch_up_due)
        self.assertFalse(health.has_problems)
        self.assertIn("refresh off            4  (serving the last cached or deploy-time copy; last catch-up 2027-01-12, 3 days ago)", report)
        self.assertNotIn("Catch-up due", report)
        self.assertIn("Refresh off and missed by the last catch-up; serving an older copy (2):", report)
        self.assertIn("  bandcamp-undated\tno build date\n  bandcamp-failing\t2026-11-06\n", report)

    def test_a_source_added_since_does_not_pass_for_a_catch_up(self) -> None:
        ids = [f"bandcamp-{number}" for number in range(5)] + ["bandcamp-new"]
        built = {**{f"bandcamp-{number}": NOW - 20 * DAY for number in range(5)}, "bandcamp-new": NOW - HOUR}

        health = summarize_bridge_health(ids, {}, {}, set(ids), NOW, 12 * HOUR, refresh_off=set(ids), built=built)

        self.assertEqual(health.last_catch_up, NOW - 20 * DAY)
        self.assertTrue(health.catch_up_due)
        self.assertEqual(health.missed_catch_up, [])

    def test_a_catch_up_is_due_after_two_weeks(self) -> None:
        built = {"bandcamp-a": NOW - 15 * DAY}

        health = summarize_bridge_health(["bandcamp-a"], {}, {}, {"bandcamp-a"}, NOW, 12 * HOUR, refresh_off={"bandcamp-a"}, built=built)
        report = format_bridge_health(health)

        self.assertTrue(health.catch_up_due)
        self.assertIn("Catch-up due: refresh-off feeds were last rebuilt 15 days ago.", report)
        self.assertIn("./scripts/netnewswire_workflow.sh catch-up", report)
        self.assertEqual(health.missed_catch_up, [])

        sooner = summarize_bridge_health(["bandcamp-a"], {}, {}, {"bandcamp-a"}, NOW, 12 * HOUR, refresh_off={"bandcamp-a"}, built=built, catch_up_due_days=30)
        self.assertFalse(sooner.catch_up_due)

    def test_without_build_dates_the_refresh_off_line_is_unchanged(self) -> None:
        health = summarize_bridge_health(["bandcamp-a"], {}, {}, {"bandcamp-a"}, NOW, 12 * HOUR, refresh_off={"bandcamp-a"})

        self.assertIsNone(health.last_catch_up)
        self.assertFalse(health.catch_up_due)
        self.assertIn("  refresh off            1  (serving the last cached or deploy-time copy)\n", format_bridge_health(health))

    def test_email_sources_are_listed_by_last_mail_and_quiet_is_not_a_problem(self) -> None:
        health = summarize_bridge_health(
            ["email-recent", "email-quiet", "email-new", "nts-ok"],
            {
                "email-recent": RefreshState(last_attempt=NOW - 2 * DAY, last_success=NOW - 2 * DAY),
                "email-quiet": RefreshState(last_attempt=NOW - 45 * DAY, last_success=NOW - 45 * DAY),
                "nts-ok": RefreshState(last_attempt=NOW - HOUR, last_success=NOW - HOUR),
            },
            {"email-recent": NOW - 2 * DAY, "email-quiet": NOW - 45 * DAY, "nts-ok": NOW - HOUR},
            {"email-new"},
            NOW,
            12 * HOUR,
            email={"email-recent", "email-quiet", "email-new"},
        )
        report = format_bridge_health(health)

        self.assertEqual(
            health.email,
            [("email-new", None), ("email-quiet", NOW - 45 * DAY), ("email-recent", NOW - 2 * DAY)],
        )
        self.assertEqual(health.email_quiet, ["email-new", "email-quiet"])
        # Mail on its own schedule is never failing, stale, or waiting for a refresh.
        self.assertEqual((health.healthy, health.never_attempted, health.failing, health.stale), (["nts-ok"], [], [], []))
        self.assertEqual(health.seed_only, ["email-new"])
        self.assertFalse(health.has_problems)
        self.assertIn("  email                  3  (2 with no mail in 30 days)", report)
        self.assertIn("email-new\tno mail yet\tquiet", report)
        self.assertIn("email-quiet\t2026-12-01\tquiet", report)
        self.assertRegex(report, r"email-recent\t\d{4}-\d{2}-\d{2}\n")

    def test_an_email_source_with_nothing_to_serve_is_still_a_problem(self) -> None:
        health = summarize_bridge_health(["email-lost"], {}, {}, set(), NOW, 12 * HOUR, email={"email-lost"})

        self.assertEqual(health.nothing_to_serve, ["email-lost"])
        self.assertTrue(health.has_problems)

    def test_the_quiet_window_is_adjustable(self) -> None:
        state = {"email-weekly": RefreshState(last_attempt=NOW - 10 * DAY, last_success=NOW - 10 * DAY)}

        health = summarize_bridge_health(["email-weekly"], state, {"email-weekly": NOW}, set(), NOW, 12 * HOUR, email={"email-weekly"}, email_quiet_days=7)

        self.assertEqual(health.email_quiet, ["email-weekly"])
        self.assertIn("(1 with no mail in 7 days)", format_bridge_health(health))



class StatusFeedTests(unittest.TestCase):
    def items(self, health):
        channel = ET.fromstring(render_status_feed(health)).find("channel")
        self.assertEqual(channel.findtext("title"), "Feed Booster status")
        return {item.findtext("guid"): item for item in channel.findall("item")}

    def test_all_quiet_means_an_empty_feed(self) -> None:
        health = summarize_bridge_health(["nts-ok"], {"nts-ok": RefreshState(last_attempt=NOW, last_success=NOW)}, {"nts-ok": NOW}, set(), NOW, 12 * HOUR)

        self.assertEqual(self.items(health), {})

    def test_each_catch_up_gets_one_item_naming_what_it_missed(self) -> None:
        ids = ["bandcamp-a", "bandcamp-b", "bandcamp-failing"]
        built = {"bandcamp-a": NOW - 2 * DAY, "bandcamp-b": NOW - 2 * DAY, "bandcamp-failing": NOW - 60 * DAY}
        health = summarize_bridge_health(ids, {}, {}, set(ids), NOW, 12 * HOUR, refresh_off=set(ids), built=built)

        items = self.items(health)

        self.assertEqual(list(items), ["bandcamp-catch-up-2027-01-13"])
        item = items["bandcamp-catch-up-2027-01-13"]
        self.assertEqual(item.findtext("title"), "Bandcamp feeds caught up on 2027-01-13")
        self.assertEqual(item.findtext("pubDate"), "Wed, 13 Jan 2027 08:00:00 +0000")
        self.assertIn("1 feed wasn't rebuilt and still serves an older copy", item.findtext("description"))
        self.assertIn("<code>bandcamp-failing</code>: 2026-11-16", item.findtext("description"))

    def test_a_due_catch_up_adds_a_reminder_dated_when_it_came_due(self) -> None:
        built = {"bandcamp-a": NOW - 20 * DAY}
        health = summarize_bridge_health(["bandcamp-a"], {}, {}, {"bandcamp-a"}, NOW, 12 * HOUR, refresh_off={"bandcamp-a"}, built=built)

        reminder = self.items(health)["bandcamp-catch-up-due-2026-12-26"]

        self.assertEqual(reminder.findtext("title"), "Time to catch up Bandcamp: last catch-up was 2026-12-26")
        self.assertEqual(reminder.findtext("pubDate"), "Sat, 09 Jan 2027 08:00:00 +0000")
        self.assertIn("./scripts/netnewswire_workflow.sh catch-up", reminder.findtext("description"))

    def test_the_reminder_keeps_its_guid_while_the_catch_up_stays_due(self) -> None:
        built = {"bandcamp-a": NOW - 20 * DAY}
        guids = [
            set(self.items(summarize_bridge_health(["bandcamp-a"], {}, {}, {"bandcamp-a"}, now, 12 * HOUR, refresh_off={"bandcamp-a"}, built=built)))
            for now in (NOW, NOW + 3 * DAY)
        ]

        self.assertEqual(guids[0], guids[1])

    def test_feeds_failing_for_a_while_or_with_nothing_to_serve_need_attention(self) -> None:
        states = {
            "nts-broken": RefreshState(last_attempt=NOW, last_success=NOW - 5 * DAY, last_error="ValueError: <bad> page", consecutive_failures=3),
            "nts-hiccup": RefreshState(last_attempt=NOW, last_success=NOW - DAY, last_error="TimeoutError: slow", consecutive_failures=2),
        }
        health = summarize_bridge_health(["nts-broken", "nts-hiccup", "nts-empty"], states, {"nts-broken": NOW, "nts-hiccup": NOW}, set(), NOW, 12 * HOUR)

        items = self.items(health)

        self.assertEqual(len(items), 1)
        guid, item = next(iter(items.items()))
        self.assertTrue(guid.startswith("attention-"))
        self.assertEqual(item.findtext("title"), "2 generated feeds need attention")
        self.assertIsNone(item.find("pubDate"))
        description = item.findtext("description")
        self.assertIn("<code>nts-broken</code>: 3 failed refreshes in a row. ValueError: &lt;bad&gt; page", description)
        self.assertIn("<code>nts-empty</code>: nothing to serve", description)
        self.assertNotIn("nts-hiccup", description)

    def test_a_new_attention_item_appears_only_when_the_set_changes(self) -> None:
        def guid(failing_ids):
            states = {source_id: RefreshState(last_attempt=NOW, consecutive_failures=4, last_error="x") for source_id in failing_ids}
            health = summarize_bridge_health(failing_ids, states, {source_id: NOW for source_id in failing_ids}, set(), NOW, 12 * HOUR)
            return next(iter(self.items(health)))

        self.assertEqual(guid(["a", "b"]), guid(["b", "a"]))
        self.assertNotEqual(guid(["a", "b"]), guid(["a", "b", "c"]))


if __name__ == "__main__":
    unittest.main()
