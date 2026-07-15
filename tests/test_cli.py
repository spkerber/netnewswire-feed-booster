from pathlib import Path
from tempfile import TemporaryDirectory
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from netnewswire_feed_booster.cli import main
from netnewswire_feed_booster.subscription_history import SubscriptionHistoryStore
from netnewswire_feed_booster.feed_validation import FeedValidationResult
from netnewswire_feed_booster.feed_store import FeedStore, Source
from netnewswire_feed_booster.opml import write_opml


EMPTY_NETNEWSWIRE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>NetNewsWire Export</title></head>
  <body></body>
</opml>
"""


class CliTests(unittest.TestCase):
    def test_list_redacts_feed_urls_unless_explicitly_requested(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="private-feed",
                    title="Private Feed",
                    feed_url="https://example.modal.run/feeds/secret-token/bandcamp/private-feed.rss",
                    kind="bandcamp",
                    profiles=["test-user"],
                )
            )
            store.save()

            redacted_output = io.StringIO()
            with redirect_stdout(redacted_output):
                main(["--data", str(data_path), "list", "--profile", "test-user"])

            sensitive_output = io.StringIO()
            with redirect_stdout(sensitive_output):
                main(["--data", str(data_path), "list", "--profile", "test-user", "--show-sensitive"])

        self.assertNotIn("secret-token", redacted_output.getvalue())
        self.assertIn("[redacted; use --show-sensitive]", redacted_output.getvalue())
        self.assertIn("secret-token", sensitive_output.getvalue())

    def test_set_status_unsubscribed_records_subscription_history(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            history_path = Path(tmp_dir) / "subscription-history.json"

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "--history",
                        str(history_path),
                        "subscribe-substack",
                        "oneusefulthing.substack.com",
                        "--title",
                        "One Useful Thing",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "--history",
                        str(history_path),
                        "set-status",
                        "one-useful-thing",
                        "--status",
                        "unsubscribed",
                        "--reason",
                        "Too noisy",
                    ]
                )

            entries = SubscriptionHistoryStore(history_path).entries()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "rss_unsubscribed")
        self.assertEqual(entries[0].reason, "Too noisy")

    def test_reconcile_netnewswire_apply_records_missing_active_sources(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            history_path = Path(tmp_dir) / "subscription-history.json"
            opml_path = Path(tmp_dir) / "netnewswire.opml"
            opml_path.write_text(EMPTY_NETNEWSWIRE_OPML, encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "--history",
                        str(history_path),
                        "subscribe-youtube",
                        "UC123",
                        "--title",
                        "Video Source",
                    ]
                )

            output = io.StringIO()
            with redirect_stdout(output):
                main(
                    [
                        "--data",
                        str(data_path),
                        "--history",
                        str(history_path),
                        "reconcile-netnewswire",
                        str(opml_path),
                        "--apply",
                    ]
                )

            source = FeedStore(data_path).source_by_id("video-source")
            entries = SubscriptionHistoryStore(history_path).entries()

        self.assertIsNotNone(source)
        self.assertEqual(source.status, "unsubscribed")
        self.assertEqual(len(entries), 1)
        self.assertIn("Applied 1 subscription-history entries", output.getvalue())

    def test_export_opml_can_rewrite_bandcamp_to_hosted_base(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            out_path = Path(tmp_dir) / "feeds.opml"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="bandcamp-ghost-dubs",
                    title="Bandcamp: Ghost Dubs",
                    feed_url="file:///tmp/bandcamp-ghost-dubs.rss",
                    site_url="https://ghostdubs.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp Artists"],
                )
            )
            store.save()

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "export-opml",
                        "--profile",
                        "test-user",
                        "--out",
                        str(out_path),
                        "--bandcamp-feed-base",
                        "https://example.modal.run",
                        "--bandcamp-feed-token",
                        "secret-token",
                    ]
                )

            rendered = out_path.read_text(encoding="utf-8")

        self.assertIn("https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-ghost-dubs.rss", rendered)
        self.assertIn('htmlUrl="https://ghostdubs.bandcamp.com/"', rendered)

    def test_export_opml_requires_token_with_hosted_base(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            out_path = Path(tmp_dir) / "feeds.opml"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="bandcamp-ghost-dubs",
                    title="Bandcamp: Ghost Dubs",
                    feed_url="file:///tmp/bandcamp-ghost-dubs.rss",
                    site_url="https://ghostdubs.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp Artists"],
                )
            )
            store.save()

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main(
                        [
                            "--data",
                            str(data_path),
                            "export-opml",
                            "--profile",
                            "test-user",
                            "--out",
                            str(out_path),
                            "--bandcamp-feed-base",
                            "https://example.modal.run",
                            "--bandcamp-feed-token",
                            "",
                        ]
                    )

        self.assertIn("--bandcamp-feed-token is required", stderr.getvalue())

    def test_verify_netnewswire_passes_when_current_matches_expected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            current_path = Path(tmp_dir) / "current.opml"
            expected_path = Path(tmp_dir) / "expected.opml"
            sources = [
                Source(
                    id="bandcamp-ghost-dubs",
                    title="Bandcamp: Ghost Dubs",
                    feed_url="https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-ghost-dubs.rss",
                    site_url="https://ghostdubs.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp Artists"],
                )
            ]
            write_opml(current_path, sources, title="Current")
            write_opml(expected_path, sources, title="Expected")

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "--data",
                        str(data_path),
                        "verify-netnewswire",
                        str(current_path),
                        "--expected",
                        str(expected_path),
                        "--profile",
                        "test-user",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("match the hosted OPML export", output.getvalue())

    def test_verify_netnewswire_reports_stale_and_extra_sources(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            current_path = Path(tmp_dir) / "current.opml"
            expected_path = Path(tmp_dir) / "expected.opml"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="removed-channel",
                    title="Removed Channel",
                    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCREMOVED",
                    site_url="https://www.youtube.com/channel/UCREMOVED",
                    kind="youtube",
                    status="unsubscribed",
                    profiles=["test-user"],
                )
            )
            store.save()
            expected_sources = [
                Source(
                    id="bandcamp-ghost-dubs",
                    title="Bandcamp: Ghost Dubs",
                    feed_url="https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-ghost-dubs.rss",
                    site_url="https://ghostdubs.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp Artists"],
                ),
                Source(
                    id="nts-nkisi",
                    title="NTS: NKISI",
                    feed_url="https://example.modal.run/feeds/secret-token/generated/nts-nkisi.rss",
                    site_url="https://www.nts.live/shows/nkisi",
                    kind="other",
                    profiles=["test-user"],
                    groups=["NTS"],
                ),
            ]
            current_sources = [
                expected_sources[0],
                Source(
                    id="bandcamp-old-file",
                    title="Bandcamp: Old File",
                    feed_url=f"{Path(tmp_dir, 'exports/bandcamp/bandcamp-old-file.rss').resolve().as_uri()}",
                    kind="bandcamp",
                    profiles=["test-user"],
                ),
                Source(
                    id="bandcamp-tokenless",
                    title="Bandcamp: Tokenless",
                    feed_url="https://example.modal.run/feeds/bandcamp/bandcamp-tokenless.rss",
                    kind="bandcamp",
                    profiles=["test-user"],
                ),
                Source(
                    id="removed-channel",
                    title="Removed Channel",
                    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCREMOVED",
                    kind="youtube",
                    profiles=["test-user"],
                ),
                Source(
                    id="extra-channel",
                    title="Extra Channel",
                    feed_url="https://example.com/feed.xml",
                    kind="website",
                    profiles=["test-user"],
                ),
            ]
            write_opml(current_path, current_sources, title="Current")
            write_opml(expected_path, expected_sources, title="Expected")

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "--data",
                        str(data_path),
                        "verify-netnewswire",
                        str(current_path),
                        "--expected",
                        str(expected_path),
                        "--profile",
                        "test-user",
                    ]
                )

        rendered = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("missing: 1", rendered)
        self.assertIn("unexpected: 1", rendered)
        self.assertIn("stale_file_bandcamp: 1", rendered)
        self.assertIn("tokenless_modal: 1", rendered)
        self.assertIn("unsubscribed: 1", rendered)

    def test_unsubscribe_exact_matches_multiple_sources(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            history_path = Path(tmp_dir) / "subscription-history.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="youtube-example",
                    title="Example Channel",
                    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC123",
                    site_url="https://www.youtube.com/channel/UC123",
                    kind="youtube",
                    profiles=["test-user"],
                )
            )
            store.add_or_update(
                Source(
                    id="bandcamp-example",
                    title="Bandcamp: Example",
                    feed_url="file:///tmp/bandcamp-example.rss",
                    site_url="https://example.bandcamp.com",
                    kind="bandcamp",
                    profiles=["test-user"],
                )
            )
            store.save()

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "--history",
                        str(history_path),
                        "unsubscribe",
                        "youtube-example",
                        "https://example.bandcamp.com",
                        "--profile",
                        "test-user",
                        "--reason",
                        "Too noisy",
                    ]
                )

            updated = FeedStore(data_path)
            entries = SubscriptionHistoryStore(history_path).entries()

        self.assertEqual(updated.source_by_id("youtube-example").status, "unsubscribed")
        self.assertEqual(updated.source_by_id("bandcamp-example").status, "unsubscribed")
        self.assertEqual(len(entries), 2)

    def test_subscribe_bandcamp_source_adds_artist_and_local_rss(self) -> None:
        artist_html = '''
        <meta property="og:site_name" content="Ghost Dubs">
        <ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Ghost Dubs&quot;,&quot;page_url&quot;:&quot;/album/damaged&quot;,&quot;title&quot;:&quot;Damaged&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
        '''
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            out_dir = Path(tmp_dir) / "bandcamp"

            with patch("netnewswire_feed_booster.bandcamp_sources.fetch_text", return_value=artist_html):
                with patch("netnewswire_feed_booster.cli.render_bandcamp_source_rss", return_value="<rss></rss>"):
                    with redirect_stdout(io.StringIO()):
                        main(
                            [
                                "--data",
                                str(data_path),
                                "subscribe-bandcamp-source",
                                "https://ghostdubs.bandcamp.com/",
                                "--out-dir",
                                str(out_dir),
                            ]
                        )

            source = FeedStore(data_path).source_by_id("bandcamp-ghost-dubs")
            rss_path = out_dir / "bandcamp-ghost-dubs.rss"
            rss_exists = rss_path.exists()

        self.assertIsNotNone(source)
        self.assertEqual(source.groups, ["Bandcamp"])
        self.assertTrue(rss_exists)

    def test_discover_feed_prints_alternate_feed_url(self) -> None:
        output = io.StringIO()
        with patch("netnewswire_feed_booster.cli.discover_feed_url", return_value="https://example.com/feed.xml"):
            with redirect_stdout(output):
                result = main(["discover-feed", "https://example.com"])

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), "https://example.com/feed.xml")

    def test_audit_sources_reports_failures_and_returns_nonzero(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="ok-feed",
                    title="OK Feed",
                    feed_url="https://example.com/feed.xml",
                    profiles=["test-user"],
                )
            )
            store.add_or_update(
                Source(
                    id="broken-feed",
                    title="Broken Feed",
                    feed_url="https://example.com/broken",
                    profiles=["test-user"],
                )
            )
            store.save()

            output = io.StringIO()
            with patch(
                "netnewswire_feed_booster.cli.audit_sources",
                return_value=[
                    FeedValidationResult("ok-feed", "OK Feed", "https://example.com/feed.xml", "ok", "rss", "ok"),
                    FeedValidationResult("broken-feed", "Broken Feed", "https://example.com/broken", "error", "html", "not a feed"),
                ],
            ):
                with redirect_stdout(output):
                    result = main(["--data", str(data_path), "audit-sources", "--profile", "test-user"])

        rendered = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("ok\trss\tok-feed", rendered)
        self.assertIn("error\thtml\tbroken-feed", rendered)
        self.assertIn("Audited 2 sources; failures: 1", rendered)

    def test_refresh_generated_local_feeds_regenerates_nts_and_hydefm(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            out_dir = Path(tmp_dir) / "generated"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="nts-example",
                    title="NTS: Example",
                    feed_url="file:///old/nts-example.rss",
                    site_url="https://www.nts.live/shows/example",
                    kind="other",
                    profiles=["trial"],
                    groups=["NTS"],
                    source="nts-local-generated",
                )
            )
            store.add_or_update(
                Source(
                    id="radio-hydefm-archives",
                    title="HydeFM Archives",
                    feed_url="file:///old/radio-hydefm-archives.rss",
                    site_url="https://hydefm.com/archives/",
                    kind="other",
                    profiles=["trial"],
                    groups=["HydeFM"],
                    source="radio-local-generated",
                )
            )
            store.save()

            with patch("netnewswire_feed_booster.cli.render_nts_show_rss", return_value="<rss>nts</rss>"):
                with patch("netnewswire_feed_booster.cli.render_hydefm_archive_rss", return_value="<rss>hydefm</rss>"):
                    with redirect_stdout(io.StringIO()):
                        result = main(
                            [
                                "--data",
                                str(data_path),
                                "refresh-generated-local-feeds",
                                "--profile",
                                "trial",
                                "--out-dir",
                                str(out_dir),
                            ]
                        )

            refreshed = FeedStore(data_path)
            nts_rss = (out_dir / "nts-example.rss").read_text(encoding="utf-8")
            hydefm_rss = (out_dir / "radio-hydefm-archives.rss").read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertEqual(nts_rss, "<rss>nts</rss>")
        self.assertEqual(hydefm_rss, "<rss>hydefm</rss>")
        self.assertTrue(refreshed.source_by_id("nts-example").feed_url.endswith("/generated/nts-example.rss"))


if __name__ == "__main__":
    unittest.main()
