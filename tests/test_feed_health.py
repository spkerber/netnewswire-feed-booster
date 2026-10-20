import io
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from netnewswire_feed_booster.feed_health import (
    BOT_CHALLENGE,
    EMPTY,
    GONE,
    HTTP_ONLY,
    INVITE_ONLY,
    NETWORK_ERROR,
    NOT_A_FEED,
    OK,
    QUIET_1Y,
    QUIET_2Y,
    REFUSED,
    SERVER_ERROR,
    UNDATED,
    FetchResult,
    check_feeds,
    fetch_for_health,
    format_summary,
    write_report,
)
from netnewswire_feed_booster.feed_store import Source


NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def rss(*dates: str) -> bytes:
    items = "".join(f"<item><title>Post</title><pubDate>{date}</pubDate></item>" for date in dates)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>{items}</channel></rss>'.encode()


ATOM = (
    b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title>'
    b"<entry><title>Video</title><published>2026-09-16T03:00:10+00:00</published></entry></feed>"
)
RDF = (
    b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    b'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    b"<channel><title>Old school</title></channel><item><title>Post</title><dc:date>2026-08-01T00:00:00Z</dc:date></item></rdf:RDF>"
)
JSON_FEED = b'{"version": "https://jsonfeed.org/version/1.1", "title": "J", "items": [{"id": "1", "date_published": "2026-10-01T00:00:00Z"}]}'
HTML = b"<!doctype html><html><head><title>Artist</title></head><body>Hi</body></html>"


def source(source_id: str, url: str, kind: str = "website") -> Source:
    return Source(id=source_id, title=source_id.replace("-", " ").title(), feed_url=url, kind=kind, profiles=["test"])


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, url: str) -> FetchResult:
        with self.lock:
            self.calls.append(url)
        response = self.responses[url]
        return response(len([call for call in self.calls if call == url])) if callable(response) else response


class ClassificationTests(unittest.TestCase):
    def check(self, sources, responses):
        fetch = FakeFetcher(responses)
        results = check_feeds(sources, fetch=fetch, now=NOW, pause_seconds=0)
        return {health.source_id: health for health in results}, fetch

    def test_each_outcome_gets_its_own_status(self) -> None:
        sources = [
            source("fresh", "https://a.example/feed"),
            source("quiet-year", "https://b.example/feed"),
            source("quiet-years", "https://c.example/feed"),
            source("no-dates", "https://d.example/feed"),
            source("empty-artist", "https://feeds.soundcloud.com/users/1/sounds.rss"),
            source("terminated", "https://www.youtube.com/feeds/videos.xml?channel_id=UC1", kind="youtube"),
            source("challenged", "https://e.example/feed"),
            source("closed", "https://closed.substack.com/feed", kind="substack"),
            source("rate-limited", "https://f.example/feed"),
            source("broken-server", "https://g.example/feed"),
            source("redirected-away", "https://feeds.soundcloud.com/users/2/sounds.rss"),
            source("dead-domain", "https://h.example/feed"),
            source("atom-channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UC2", kind="youtube"),
            source("rdf-feed", "https://i.example/index.rdf"),
            source("json-feed", "https://j.example/feed.json"),
        ]
        responses = {
            "https://a.example/feed": FetchResult(200, "https://a.example/feed", body=rss("Wed, 14 Oct 2026 10:00:00 +0000")),
            "https://b.example/feed": FetchResult(200, "https://b.example/feed", body=rss("Mon, 01 Jul 2025 10:00:00 +0000")),
            "https://c.example/feed": FetchResult(200, "https://c.example/feed", body=rss("Fri, 01 Jan 2021 10:00:00 +0000")),
            "https://d.example/feed": FetchResult(200, "https://d.example/feed", body=b'<rss version="2.0"><channel><title>x</title><item><title>a</title></item></channel></rss>'),
            "https://feeds.soundcloud.com/users/1/sounds.rss": FetchResult(200, "https://feeds.soundcloud.com/users/1/sounds.rss", body=rss()),
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC1": FetchResult(404, body=b"Not Found"),
            "https://e.example/feed": FetchResult(403, headers={"cf-mitigated": "challenge"}, body=b"<html><head><title>Just a moment...</title>"),
            "https://closed.substack.com/feed": FetchResult(400, body=b"This publication is invite-only"),
            "https://f.example/feed": FetchResult(429),
            "https://g.example/feed": FetchResult(502),
            "https://feeds.soundcloud.com/users/2/sounds.rss": FetchResult(200, "https://www.artist.example/", body=HTML),
            "https://h.example/feed": FetchResult(0, error="URLError: [Errno 8] nodename nor servname provided"),
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC2": FetchResult(200, "https://www.youtube.com/feeds/videos.xml?channel_id=UC2", body=ATOM),
            "https://i.example/index.rdf": FetchResult(200, "https://i.example/index.rdf", body=RDF),
            "https://j.example/feed.json": FetchResult(200, "https://j.example/feed.json", body=JSON_FEED),
        }

        results, _fetch = self.check(sources, responses)

        expected = {
            "fresh": OK,
            "quiet-year": QUIET_1Y,
            "quiet-years": QUIET_2Y,
            "no-dates": UNDATED,
            "empty-artist": EMPTY,
            "terminated": GONE,
            "challenged": BOT_CHALLENGE,
            "closed": INVITE_ONLY,
            "rate-limited": REFUSED,
            "broken-server": SERVER_ERROR,
            "redirected-away": NOT_A_FEED,
            "dead-domain": NETWORK_ERROR,
            "atom-channel": OK,
            "rdf-feed": OK,
            "json-feed": OK,
        }
        self.assertEqual({source_id: health.status for source_id, health in results.items()}, expected)
        self.assertIn("www.artist.example", results["redirected-away"].detail)
        self.assertEqual(results["atom-channel"].platform, "YouTube")
        self.assertEqual(results["closed"].platform, "Substack")
        self.assertEqual(results["empty-artist"].platform, "SoundCloud")
        self.assertEqual(results["fresh"].newest, datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc))

    def test_an_empty_feed_is_not_a_problem(self) -> None:
        results, _fetch = self.check(
            [source("empty-artist", "https://feeds.soundcloud.com/users/1/sounds.rss")],
            {"https://feeds.soundcloud.com/users/1/sounds.rss": FetchResult(200, "https://feeds.soundcloud.com/users/1/sounds.rss", body=rss())},
        )

        self.assertEqual(results["empty-artist"].status, EMPTY)
        self.assertFalse(results["empty-artist"].is_problem)

    def test_a_working_feed_reached_through_a_redirect_gets_a_suggested_address(self) -> None:
        results, _fetch = self.check(
            [
                source("moved", "https://old.example/feed"),
                source("trailing-slash", "https://same.example/feed"),
            ],
            {
                "https://old.example/feed": FetchResult(200, "https://new.example/feed", body=rss("Wed, 14 Oct 2026 10:00:00 +0000")),
                "https://same.example/feed": FetchResult(200, "https://same.example/feed/", body=rss("Wed, 14 Oct 2026 10:00:00 +0000")),
            },
        )

        self.assertEqual(results["moved"].status, OK)
        self.assertEqual(results["moved"].suggested_url, "https://new.example/feed")
        # A trailing slash is the same address, not a move.
        self.assertEqual(results["trailing-slash"].suggested_url, "")

    def test_an_http_feed_is_checked_over_https_and_the_upgrade_suggested(self) -> None:
        results, fetch = self.check(
            [source("upgradable", "http://up.example/feed"), source("http-stuck", "http://stuck.example/feed")],
            {
                "https://up.example/feed": FetchResult(200, "https://up.example/feed", body=rss("Wed, 14 Oct 2026 10:00:00 +0000")),
                "https://stuck.example/feed": FetchResult(0, error="SSLError: certificate verify failed"),
            },
        )

        self.assertNotIn("http://up.example/feed", fetch.calls)
        self.assertEqual(results["upgradable"].status, OK)
        self.assertEqual(results["upgradable"].suggested_url, "https://up.example/feed")
        self.assertEqual(results["http-stuck"].status, HTTP_ONLY)
        self.assertIn("certificate", results["http-stuck"].detail)

    def test_problems_are_retried_once_and_the_retry_wins(self) -> None:
        def flaky(attempt: int) -> FetchResult:
            if attempt == 1:
                return FetchResult(502)
            return FetchResult(200, "https://flaky.example/feed", body=rss("Wed, 14 Oct 2026 10:00:00 +0000"))

        results, fetch = self.check(
            [source("flaky", "https://flaky.example/feed"), source("steady", "https://steady.example/feed")],
            {
                "https://flaky.example/feed": flaky,
                "https://steady.example/feed": FetchResult(200, "https://steady.example/feed", body=rss("Wed, 14 Oct 2026 10:00:00 +0000")),
            },
        )

        self.assertEqual(results["flaky"].status, OK)
        self.assertEqual(fetch.calls.count("https://flaky.example/feed"), 2)
        self.assertEqual(fetch.calls.count("https://steady.example/feed"), 1)

    def test_generated_file_feeds_are_skipped(self) -> None:
        results, fetch = self.check(
            [source("bandcamp-artist", "file:///tmp/exports/generated/bandcamp-artist.rss")],
            {},
        )

        self.assertEqual(results, {})
        self.assertEqual(fetch.calls, [])

    def test_results_keep_the_order_sources_were_given(self) -> None:
        urls = [f"https://www.youtube.com/feeds/videos.xml?channel_id=UC{index}" for index in range(5)] + ["https://a.example/feed"]
        sources = [source(f"feed-{index}", url) for index, url in enumerate(urls)]
        responses = {url: FetchResult(200, url, body=rss("Wed, 14 Oct 2026 10:00:00 +0000")) for url in urls}

        results = check_feeds(sources, fetch=FakeFetcher(responses), now=NOW, pause_seconds=0)

        self.assertEqual([health.source_id for health in results], [item.id for item in sources])


class PacingTests(unittest.TestCase):
    def test_per_host_caps_bound_concurrent_requests(self) -> None:
        active = {}
        peak = {}
        lock = threading.Lock()

        def fetch(url: str) -> FetchResult:
            host = "youtube" if "youtube" in url else "other"
            with lock:
                active[host] = active.get(host, 0) + 1
                peak[host] = max(peak.get(host, 0), active[host])
            time.sleep(0.02)
            with lock:
                active[host] -= 1
            return FetchResult(200, url, body=rss("Wed, 14 Oct 2026 10:00:00 +0000"))

        sources = [source(f"yt-{index}", f"https://www.youtube.com/feeds/videos.xml?channel_id=UC{index}") for index in range(12)]
        sources += [source(f"site-{index}", f"https://site{index}.example/feed") for index in range(6)]

        check_feeds(sources, fetch=fetch, now=NOW, workers=10, host_limits={"youtube.com": 2}, pause_seconds=0)

        self.assertLessEqual(peak["youtube"], 2)
        self.assertGreater(peak["other"], 1)


class OutputTests(unittest.TestCase):
    def setUp(self) -> None:
        responses = {
            "https://404.feed.example/private?key=s3cret-token": FetchResult(404),
            "https://moved.example/feed": FetchResult(200, "https://new-home.example/feed?key=another-secret", body=rss("Wed, 14 Oct 2026 10:00:00 +0000")),
            "https://feeds.soundcloud.com/users/1/sounds.rss": FetchResult(200, "https://feeds.soundcloud.com/users/1/sounds.rss", body=rss()),
            "https://old.example/feed": FetchResult(200, "https://old.example/feed", body=rss("Fri, 01 Jan 2021 10:00:00 +0000")),
        }
        sources = [
            source("private-feed", "https://404.feed.example/private?key=s3cret-token"),
            source("moved-feed", "https://moved.example/feed"),
            source("empty-artist", "https://feeds.soundcloud.com/users/1/sounds.rss"),
            source("old-blog", "https://old.example/feed"),
        ]
        self.results = check_feeds(sources, fetch=FakeFetcher(responses), now=NOW, pause_seconds=0, retry=False)

    def test_summary_redacts_private_urls_unless_asked(self) -> None:
        redacted = format_summary(self.results, skipped_generated=3)
        shown = format_summary(self.results, show_sensitive=True)

        self.assertNotIn("s3cret-token", redacted)
        self.assertNotIn("another-secret", redacted)
        self.assertIn("404.feed.example", redacted)
        self.assertIn("moved.example -> new-home.example", redacted)
        self.assertIn("Skipped 3 generated feeds", redacted)
        self.assertIn("s3cret-token", shown)
        self.assertIn("Nothing was changed.", redacted)

    def test_report_lists_every_category_without_secrets(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "nested" / "feed-health.md"
            write_report(self.results, path, skipped_generated=3, now=NOW)
            report = path.read_text(encoding="utf-8")

        self.assertIn("# Feed health (2026-10-19)", report)
        self.assertIn("## Gone (1)", report)
        self.assertIn("## Suggested address updates (1)", report)
        self.assertIn("## Empty (1)", report)
        self.assertIn("## No new item in 2+ years (1)", report)
        self.assertIn("last 2021-01-01", report)
        self.assertIn("not proof it is dead", report)
        self.assertNotIn("s3cret-token", report)


class FetchForHealthTests(unittest.TestCase):
    def test_keeps_status_headers_and_body_of_an_error_response(self) -> None:
        error = HTTPError(
            "https://closed.substack.com/feed",
            400,
            "Bad Request",
            {"Content-Type": "text/html", "CF-Ray": "abc"},
            io.BytesIO(b"This publication is invite-only"),
        )
        with patch("netnewswire_feed_booster.feed_health._validate_fetch_url"):
            with patch("netnewswire_feed_booster.feed_health._restricted_open_request") as opener:
                opener.return_value.side_effect = error
                result = fetch_for_health("https://closed.substack.com/feed")
        error.close()

        self.assertEqual(result.status, 400)
        self.assertEqual(result.headers["cf-ray"], "abc")
        self.assertEqual(result.body, b"This publication is invite-only")

    def test_reports_an_unsafe_or_unreachable_url_as_no_response(self) -> None:
        result = fetch_for_health("http://insecure.example/feed")

        self.assertEqual(result.status, 0)
        self.assertIn("Unsafe fetch URL", result.error)


if __name__ == "__main__":
    unittest.main()
