from pathlib import Path
from tempfile import TemporaryDirectory
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from netnewswire_feed_booster.cli import build_parser, main, normalize_legacy_webpage_command
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
WEBPAGE_RECIPE_HTML = """
<div data-elementor-type="loop-item">
  <h2>July 2, 2026</h2>
  <h2><a href="https://hydefm.com/archive/fixture-show/">Fixture show</a></h2>
</div>
"""


class CliTests(unittest.TestCase):
    def test_non_ascii_titles_do_not_collide_in_manual_or_youtube_adds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "add",
                        "--title",
                        "\u03a9\u03bc\u03ad\u03b3\u03b1 \u03a3\u03ae\u03bc\u03b1",
                        "--feed-url",
                        "https://feeds.example.com/one.rss",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "add",
                        "--title",
                        "\ud14c\uc2a4\ud2b8 \uc2e0\ud638",
                        "--feed-url",
                        "https://feeds.example.com/two.rss",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "subscribe-youtube",
                        "UCfirst",
                        "--title",
                        "\u0625\u0634\u0627\u0631\u0629 \u062a\u062c\u0631\u064a\u0628\u064a\u0629",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "subscribe-youtube",
                        "UCsecond",
                        "--title",
                        "\ud14c\uc2a4\ud2b8 \uc2e0\ud638",
                    ]
                )

            sources = FeedStore(data_path).sources()

        self.assertEqual(len(sources), 4)
        self.assertEqual(len({source.id for source in sources}), 4)
        self.assertTrue(all(source.id.startswith("source-") for source in sources))
        self.assertEqual(
            {source.feed_url for source in sources},
            {
                "https://feeds.example.com/one.rss",
                "https://feeds.example.com/two.rss",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfirst",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCsecond",
            },
        )

    def test_non_ascii_title_ids_do_not_expose_feed_url_tokens(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "add",
                        "--title",
                        "\u0e2a\u0e31\u0e0d\u0e0d\u0e32\u0e13",
                        "--feed-url",
                        "https://example.modal.run/feeds/private-token/generated/source.rss",
                    ]
                )

            source = FeedStore(data_path).sources()[0]

        self.assertTrue(source.id.startswith("source-"))
        self.assertNotIn("private-token", source.id)

    def test_source_specific_commands_only_use_an_explicit_folder(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "subscribe-youtube",
                        "UCroot",
                        "--title",
                        "Root Feed",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "subscribe-youtube",
                        "UCfolder",
                        "--title",
                        "Folder Feed",
                        "--group",
                        "My Video Folder",
                    ]
                )

            sources = {source.id: source for source in FeedStore(data_path).sources()}

        self.assertEqual(sources["root-feed"].groups, [])
        self.assertEqual(sources["folder-feed"].groups, ["My Video Folder"])

    def test_readding_legacy_source_id_migrates_to_a_stable_id(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(Source(id="source", title="Old", feed_url="https://feeds.example.com/source.rss"))
            store.add_or_update(Source(id="source-123", title="New", feed_url="https://feeds.example.com/source.rss"))

            source = store.source_by_id("source-123")

        self.assertIsNotNone(source)
        self.assertEqual(source.title, "New")

    def test_set_folder_sets_a_nested_path_and_can_clear_it(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="example",
                    title="Example",
                    feed_url="https://example.com/feed.xml",
                    profiles=["test-user"],
                    groups=["Old Folder"],
                )
            )
            store.save()

            with redirect_stdout(io.StringIO()):
                main(["--data", str(data_path), "set-folder", "example", "News", "Example Publisher", "--profile", "test-user"])
                main(["--data", str(data_path), "set-folder", "example", "--profile", "test-user"])

            source = FeedStore(data_path).source_by_id("example")

        self.assertIsNotNone(source)
        self.assertEqual(source.groups, [])

    def test_import_opml_uses_bulk_store_merge(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            opml_path = Path(tmp_dir) / "sources.opml"
            write_opml(
                opml_path,
                [
                    Source(id="first", title="First", feed_url="https://example.com/first.xml", profiles=["fresh"]),
                    Source(id="second", title="Second", feed_url="https://example.com/second.xml", profiles=["fresh"]),
                ],
                title="Import test",
            )

            with patch.object(FeedStore, "add_or_update", side_effect=AssertionError("per-source import is too slow")):
                with redirect_stdout(io.StringIO()):
                    main(["--data", str(data_path), "import-opml", str(opml_path), "--profile", "fresh"])

            self.assertEqual(len(FeedStore(data_path).active_sources("fresh")), 2)

    def test_profile_argument_selects_profile_specific_paths(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_path = root / "sources.fresh.json"
            history_path = root / "subscription-history.fresh.json"
            data_path.write_text('{"schema_version": 1, "sources": []}\n', encoding="utf-8")
            history_path.write_text('{"schema_version": 1, "entries": []}\n', encoding="utf-8")

            with (
                patch("netnewswire_feed_booster.cli.default_sources_path", return_value=data_path) as default_data,
                patch("netnewswire_feed_booster.cli.default_subscription_history_path", return_value=history_path) as default_history,
                redirect_stdout(io.StringIO()),
            ):
                main(
                    [
                        "subscribe-substack",
                        "fixture-letter.example",
                        "--title",
                        "Fixture Letter",
                        "--profile",
                        "fresh",
                    ]
                )

            default_data.assert_called_once_with("fresh", prefer_configured=False)
            default_history.assert_called_once_with("fresh", prefer_configured=False)
            self.assertIsNotNone(FeedStore(data_path).source_by_id("fixture-letter"))

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

    def test_source_commands_do_not_print_urls_or_local_paths_by_default(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            output = io.StringIO()
            with redirect_stdout(output):
                main(
                    [
                        "--data",
                        str(data_path),
                        "subscribe-substack",
                        "private-token.example",
                        "--title",
                        "Fixture Letter",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "subscribe-youtube",
                        "UCsecret",
                        "--title",
                        "Fixture Channel",
                    ]
                )

        rendered = output.getvalue()
        self.assertNotIn("private-token.example", rendered)
        self.assertNotIn("UCsecret", rendered)
        self.assertNotIn(tmp_dir, rendered)

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
                        "fixture-letter.example",
                        "--title",
                        "Fixture Letter",
                    ]
                )
                main(
                    [
                        "--data",
                        str(data_path),
                        "--history",
                        str(history_path),
                        "set-status",
                        "fixture-letter",
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
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="file:///tmp/bandcamp-fixture-artist.rss",
                    site_url="https://fixture-artist.bandcamp.com/",
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

        self.assertIn("https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-fixture-artist.rss", rendered)
        self.assertIn('htmlUrl="https://fixture-artist.bandcamp.com/"', rendered)

    def test_export_opml_requires_token_with_hosted_base(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            out_path = Path(tmp_dir) / "feeds.opml"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="file:///tmp/bandcamp-fixture-artist.rss",
                    site_url="https://fixture-artist.bandcamp.com/",
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
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-fixture-artist.rss",
                    site_url="https://fixture-artist.bandcamp.com/",
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
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-fixture-artist.rss",
                    site_url="https://fixture-artist.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp Artists"],
                ),
                Source(
                    id="nts-fixture-signal",
                    title="NTS: Fixture Signal",
                    feed_url="https://example.modal.run/feeds/secret-token/generated/nts-fixture-signal.rss",
                    site_url="https://www.nts.live/shows/fixture-signal",
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
        <meta property="og:site_name" content="Fixture Artist">
        <ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;page_url&quot;:&quot;/album/fixture-record&quot;,&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
        '''
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            out_dir = Path(tmp_dir) / "bandcamp"
            output = io.StringIO()

            with patch("netnewswire_feed_booster.bandcamp_sources.fetch_text", return_value=artist_html):
                with patch("netnewswire_feed_booster.cli.render_bandcamp_source_rss", return_value="<rss></rss>"):
                    with redirect_stdout(output):
                        main(
                            [
                                "--data",
                                str(data_path),
                                "subscribe-bandcamp-source",
                                "https://fixture-artist.bandcamp.com/",
                                "--group",
                                "Music",
                                "--out-dir",
                                str(out_dir),
                            ]
                        )

            source = FeedStore(data_path).source_by_id("bandcamp-fixture-artist")
            rss_path = out_dir / "bandcamp-fixture-artist.rss"
            rss_exists = rss_path.exists()
            rendered_output = output.getvalue()

        self.assertIsNotNone(source)
        self.assertEqual(source.groups, ["Music"])
        self.assertTrue(rss_exists)
        self.assertNotIn(tmp_dir, rendered_output)
        self.assertNotIn("file://", rendered_output)

    def test_set_folder_updates_private_overlay_only_when_requested(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            private_data_path = Path(tmp_dir) / "private-sources.json"
            private_store = FeedStore(private_data_path)
            private_store.add_or_update(
                Source(
                    id="private-source",
                    title="Private Source",
                    feed_url="https://example.com/private.xml",
                    profiles=["test-user"],
                )
            )
            private_store.save()

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--data",
                        str(data_path),
                        "--private-data",
                        str(private_data_path),
                        "set-folder",
                        "private-source",
                        "Private Folder",
                        "--profile",
                        "test-user",
                        "--private",
                    ]
                )

            public_source = FeedStore(data_path).source_by_id("private-source")
            private_source = FeedStore(private_data_path).source_by_id("private-source")

        self.assertIsNone(public_source)
        self.assertIsNotNone(private_source)
        self.assertEqual(private_source.groups, ["Private Folder"])

    def test_discover_feed_prints_alternate_feed_url(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            private_data_path = Path(tmp_dir) / "private-sources.json"
            with patch("netnewswire_feed_booster.cli.discover_feed_url", return_value="https://example.com/feed.xml"):
                with redirect_stdout(output):
                    result = main(
                        [
                            "--data",
                            str(data_path),
                            "--private-data",
                            str(private_data_path),
                            "discover-feed",
                            "https://example.com",
                        ]
                    )

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

    def test_subscribe_webpage_feed_uses_a_registered_recipe(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_path = root / "sources.json"
            out_dir = root / "generated"

            with patch(
                "netnewswire_feed_booster.cli.fetch_text",
                return_value=WEBPAGE_RECIPE_HTML,
            ):
                with redirect_stdout(io.StringIO()) as output:
                    result = main(
                        [
                            "--data",
                            str(data_path),
                            "subscribe-webpage-feed",
                            "https://hydefm.com/archives/",
                            "--profile",
                            "trial",
                            "--group",
                            "Radio archives",
                            "--out-dir",
                            str(out_dir),
                        ]
                    )

            source = FeedStore(data_path).source_by_id("radio-hydefm-archives")
            rss = (out_dir / "radio-hydefm-archives.rss").read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertIsNotNone(source)
        self.assertEqual(source.source, "webpage-local-generated")
        self.assertEqual(source.groups, ["Radio archives"])
        self.assertIn("<title>Fixture show</title>", rss)
        self.assertIn("recipe hydefm-archives", output.getvalue())

    def test_legacy_hydefm_shortcut_routes_through_the_webpage_recipe(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_path = root / "sources.json"
            out_dir = root / "generated"

            with patch(
                "netnewswire_feed_booster.cli.fetch_text",
                return_value=WEBPAGE_RECIPE_HTML,
            ):
                with redirect_stdout(io.StringIO()):
                    result = main(
                        [
                            "--data",
                            str(data_path),
                            "subscribe-hydefm-archive",
                            "--profile",
                            "trial",
                            "--out-dir",
                            str(out_dir),
                        ]
                    )

            source = FeedStore(data_path).source_by_id("radio-hydefm-archives")

        self.assertEqual(result, 0)
        self.assertIsNotNone(source)
        self.assertEqual(source.source, "webpage-local-generated")

    def test_legacy_hydefm_shortcut_accepts_argparse_url_forms(self) -> None:
        expected_url = "https://www.hydefm.com/archives/"
        for url_arguments in (
            ["--url", expected_url],
            [f"--url={expected_url}"],
        ):
            with self.subTest(url_arguments=url_arguments):
                normalized = normalize_legacy_webpage_command(
                    ["subscribe-hydefm-archive", *url_arguments]
                )
                args = build_parser().parse_args(normalized)

                self.assertEqual(args.command, "subscribe-webpage-feed")
                self.assertEqual(args.url, expected_url)

    def test_refresh_generated_local_feeds_regenerates_nts_and_webpage_recipes(self) -> None:
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
                    id="radio-fixture-archives",
                    title="Fixture Radio Archives",
                    feed_url="file:///old/radio-fixture-archives.rss",
                    site_url="https://radio.example/archives/",
                    kind="other",
                    profiles=["trial"],
                    groups=["Webpage feeds"],
                    source="webpage-local-generated",
                )
            )
            store.save()

            class FakeAdapter:
                hosted_route = "generated"
                allowed_hosts = set()
                allowed_suffixes = set()

                def validate(self, _source):
                    return None

                def upstream_url(self, source):
                    return source.site_url

                def render(self, source, _content):
                    return "<rss>nts</rss>" if source.source == "nts-local-generated" else "<rss>webpage</rss>"

                def allowed_hosts_for(self, _source):
                    return self.allowed_hosts

                def allowed_suffixes_for(self, _source):
                    return self.allowed_suffixes

            with patch("netnewswire_feed_booster.cli.adapter_for_source", return_value=FakeAdapter()):
                with patch("netnewswire_feed_booster.cli.fetch_text", return_value="source content"):
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
            webpage_rss = (out_dir / "radio-fixture-archives.rss").read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertEqual(nts_rss, "<rss>nts</rss>")
        self.assertEqual(webpage_rss, "<rss>webpage</rss>")
        self.assertTrue(refreshed.source_by_id("nts-example").feed_url.endswith("/generated/nts-example.rss"))


if __name__ == "__main__":
    unittest.main()
