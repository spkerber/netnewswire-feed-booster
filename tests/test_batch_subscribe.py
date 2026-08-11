import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from netnewswire_feed_booster.batch_subscribe import (
    BATCH_ADAPTER_COMMANDS,
    BatchLine,
    detect_batch_adapter,
    parse_batch_lines,
)
from netnewswire_feed_booster.cli import main
from netnewswire_feed_booster.feed_store import FeedStore, Source


BANDCAMP_ARTIST_HTML = (
    '<meta property="og:site_name" content="Fixture Artist">'
    '<ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,'
    "&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;page_url&quot;:&quot;/album/fixture-record&quot;,"
    '&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>'
)
YOUTUBE_CHANNEL_HTML = (
    "<html><head>"
    '<meta property="og:title" content="Fixture Channel">'
    '<link rel="alternate" type="application/rss+xml" title="RSS" '
    'href="https://www.youtube.com/feeds/videos.xml?channel_id=UCfixturechannel">'
    "</head><body></body></html>"
)
YOUTUBE_FEED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>Fixture Channel</title>'
    "<link>https://www.youtube.com/channel/UCfixturechannel</link>"
    "<item><guid>fixture-video-1</guid><title>First video</title></item>"
    "</channel></rss>"
)
BANDCAMP_RSS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>Bandcamp: Fixture Artist</title>'
    "<link>https://fixture-artist.bandcamp.com/</link>"
    "<item><guid>fixture-record</guid><title>Fixture Record</title></item>"
    "</channel></rss>"
)
BLOG_FEED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>Fixture Blog</title>'
    "<link>https://example.com</link>"
    "<item><guid>fixture-post-1</guid><title>First post</title></item>"
    "</channel></rss>"
)


def _youtube_channel_html(handle: str) -> str:
    """A distinct channel per handle: same page shape, different id and title."""
    return YOUTUBE_CHANNEL_HTML.replace("UCfixturechannel", f"UC{handle}").replace(
        "Fixture Channel", f"Fixture Channel {handle}"
    )


def _fake_fetch(url: str, **_kwargs: object) -> str:
    """Serve each fixture by host, the way the real fetch layer would."""
    if "feeds/videos.xml" in url:
        return YOUTUBE_FEED_XML
    if "youtube.com" in url:
        return _youtube_channel_html(url.rstrip("/").rsplit("/", 1)[-1].lstrip("@"))
    if "example.com" in url or "substack.com" in url:
        return BLOG_FEED_XML
    if "bandcamp.com" in url:
        return BANDCAMP_ARTIST_HTML
    raise ConnectionError(f"simulated network failure fetching {url}")


class BatchSubscribeCliTests(unittest.TestCase):
    def _run_batch(self, tmp_dir: str, batch_text: str, *extra_args: str) -> tuple[int, str, FeedStore]:
        data_path = Path(tmp_dir) / "sources.json"
        batch_path = Path(tmp_dir) / "urls.txt"
        batch_path.write_text(batch_text, encoding="utf-8")
        output = io.StringIO()

        with patch("netnewswire_feed_booster.bandcamp_sources.fetch_text", side_effect=_fake_fetch), patch(
            "netnewswire_feed_booster.cli.render_bandcamp_source_rss", return_value=BANDCAMP_RSS
        ), patch("netnewswire_feed_booster.cli.fetch_text", side_effect=_fake_fetch), patch(
            "netnewswire_feed_booster.cli.discover_feed_url", side_effect=lambda url, **_: url
        ), patch("netnewswire_feed_booster.cli.time.sleep"):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                result = main(
                    [
                        "--data",
                        str(data_path),
                        "--private-data",
                        str(Path(tmp_dir) / "private.json"),
                        "--history",
                        str(Path(tmp_dir) / "history.json"),
                        "batch-subscribe",
                        str(batch_path),
                        "--profile",
                        "test-user",
                        "--out-dir",
                        str(Path(tmp_dir) / "generated"),
                        *extra_args,
                    ]
                )
        return result, output.getvalue(), FeedStore(data_path)

    def test_mixed_type_file_subscribes_each_url_through_its_own_adapter(self) -> None:
        batch_text = (
            "# sources worth following\n"
            "\n"
            "https://fixture-artist.bandcamp.com/\n"
            "https://www.youtube.com/@fixture\n"
            "https://example.com/blog\n"
        )
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run_batch(tmp_dir, batch_text)
            sources = store.sources()

        self.assertEqual(result, 0)
        kinds_by_group = {source.kind: source.groups for source in sources}
        self.assertEqual(len(sources), 3)
        self.assertEqual(kinds_by_group["bandcamp"], ["Bandcamp"])
        self.assertEqual(kinds_by_group["youtube"], ["YouTube"])
        self.assertIn("3 processed", rendered)
        self.assertIn("3 succeeded", rendered)
        self.assertIn("0 failed", rendered)

    def test_comment_and_blank_lines_are_not_counted_as_urls(self) -> None:
        batch_text = "# just one real URL\n\n\nhttps://www.youtube.com/@fixture\n   \n"
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run_batch(tmp_dir, batch_text)
            source_count = len(store.sources())

        self.assertEqual(result, 0)
        self.assertEqual(source_count, 1)
        self.assertIn("1 processed", rendered)

    def test_an_already_registered_url_is_skipped_without_stopping_the_batch(self) -> None:
        batch_text = "https://fixture-artist.bandcamp.com/\nhttps://www.youtube.com/@fixture\n"
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            seeded = FeedStore(data_path)
            seeded.add_or_update(
                Source(
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="file:///already/bandcamp-fixture-artist.rss",
                    site_url="https://fixture-artist.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp"],
                )
            )
            seeded.save()

            result, rendered, store = self._run_batch(tmp_dir, batch_text)
            sources = store.sources()

        self.assertEqual(result, 0)
        self.assertIn("SKIPPED", rendered)
        self.assertIn("1 skipped", rendered)
        self.assertIn("1 succeeded", rendered)
        self.assertEqual(len(sources), 2)
        self.assertTrue(any(source.kind == "youtube" for source in sources))

    def test_an_unreachable_url_fails_without_stopping_the_batch(self) -> None:
        batch_text = "https://unreachable.invalid/feed\nhttps://www.youtube.com/@fixture\n"
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run_batch(tmp_dir, batch_text)
            sources = store.sources()

        self.assertEqual(result, 1)
        self.assertIn("FAILED", rendered)
        self.assertIn("1 failed", rendered)
        self.assertIn("1 succeeded", rendered)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].kind, "youtube")

    def test_a_failure_reason_is_redacted_until_show_sensitive_is_passed(self) -> None:
        batch_text = "https://unreachable.invalid/feed\n"
        with TemporaryDirectory() as tmp_dir:
            _, redacted, _ = self._run_batch(tmp_dir, batch_text)
        with TemporaryDirectory() as tmp_dir:
            _, revealed, _ = self._run_batch(tmp_dir, batch_text, "--show-sensitive")

        self.assertIn("ConnectionError", redacted)
        self.assertIn("details redacted", redacted)
        self.assertNotIn("simulated network failure", redacted)
        self.assertIn("simulated network failure", revealed)

    def test_a_substack_url_auto_detects_to_the_substack_adapter(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run_batch(tmp_dir, "https://publication.substack.com/\n")
            sources = store.sources()

        self.assertEqual(result, 0)
        self.assertIn("substack", rendered)
        self.assertEqual(sources[0].source, "manual-oneoff")
        self.assertEqual(sources[0].feed_url, "https://publication.substack.com/feed")

    def test_an_adapter_override_forces_the_adapter_over_the_detected_one(self) -> None:
        batch_text = "https://publication.substack.com/  --adapter=feed-url\n"
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run_batch(tmp_dir, batch_text)
            sources = store.sources()

        self.assertEqual(result, 0)
        self.assertIn("feed-url", rendered)
        self.assertEqual(len(sources), 1)
        # The same URL reaches subscribe-feed-url's discovery path instead of the
        # substack one it would otherwise have auto-detected to.
        self.assertEqual(sources[0].source, "public-feed-discovery")
        self.assertEqual(sources[0].title, "Fixture Blog")

    def test_repeated_url_flags_work_without_a_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            output = io.StringIO()
            with patch("netnewswire_feed_booster.cli.fetch_text", side_effect=_fake_fetch), patch(
                "netnewswire_feed_booster.cli.discover_feed_url", side_effect=lambda url, **_: url
            ), patch("netnewswire_feed_booster.cli.time.sleep"):
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    result = main(
                        [
                            "--data",
                            str(data_path),
                            "batch-subscribe",
                            "--url",
                            "https://www.youtube.com/@fixture",
                            "--url",
                            "https://example.com/blog",
                            "--profile",
                            "test-user",
                        ]
                    )
            sources = FeedStore(data_path).sources()

        self.assertEqual(result, 0)
        self.assertEqual(len(sources), 2)
        self.assertIn("2 processed", output.getvalue())

    def test_reading_the_batch_from_stdin(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            output = io.StringIO()
            with patch("netnewswire_feed_booster.cli.fetch_text", side_effect=_fake_fetch), patch(
                "netnewswire_feed_booster.cli.discover_feed_url", side_effect=lambda url, **_: url
            ), patch("netnewswire_feed_booster.cli.time.sleep"), patch(
                "sys.stdin", io.StringIO("https://www.youtube.com/@fixture\n")
            ):
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    result = main(
                        ["--data", str(data_path), "batch-subscribe", "-", "--profile", "test-user"]
                    )
            sources = FeedStore(data_path).sources()

        self.assertEqual(result, 0)
        self.assertEqual(len(sources), 1)

    def test_an_explicit_group_overrides_every_per_adapter_default(self) -> None:
        batch_text = "https://fixture-artist.bandcamp.com/\nhttps://www.youtube.com/@fixture\n"
        with TemporaryDirectory() as tmp_dir:
            _, _, store = self._run_batch(tmp_dir, batch_text, "--group", "Listening")
            groups = sorted(source.groups for source in store.sources())

        self.assertEqual(groups, [["Listening"], ["Listening"]])

    def test_every_upstream_request_is_paced_including_verification(self) -> None:
        """Two URLs, each fetched once to subscribe and once to verify."""
        batch_text = "https://www.youtube.com/@one\nhttps://www.youtube.com/@two\n"
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            batch_path = Path(tmp_dir) / "urls.txt"
            batch_path.write_text(batch_text, encoding="utf-8")
            with patch("netnewswire_feed_booster.cli.fetch_text", side_effect=_fake_fetch), patch(
                "netnewswire_feed_booster.cli.time.sleep"
            ) as sleep_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    main(
                        [
                            "--data",
                            str(data_path),
                            "batch-subscribe",
                            str(batch_path),
                            "--profile",
                            "test-user",
                        ]
                    )

        self.assertEqual(sleep_mock.call_count, 4)
        sleep_mock.assert_called_with(1.0)

    def test_a_malformed_adapter_override_fails_before_any_network_call(self) -> None:
        batch_text = "https://example.com/blog  --adapter=telepathy\n"
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            batch_path = Path(tmp_dir) / "urls.txt"
            batch_path.write_text(batch_text, encoding="utf-8")
            with patch("netnewswire_feed_booster.cli.fetch_text") as fetch_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                    with self.assertRaises(SystemExit) as raised:
                        main(
                            [
                                "--data",
                                str(data_path),
                                "batch-subscribe",
                                str(batch_path),
                                "--profile",
                                "test-user",
                            ]
                        )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("telepathy", errors.getvalue())
        fetch_mock.assert_not_called()

    def test_help_documents_the_file_format_and_overrides(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit):
                main(["batch-subscribe", "--help"])
        rendered = output.getvalue()

        for expected in ("--adapter=", "--url", "--group", "--profile", "--data", "#"):
            self.assertIn(expected, rendered)




class ParseBatchLinesTests(unittest.TestCase):
    def test_ignores_comments_and_blank_lines(self) -> None:
        text = (
            "# Music I follow\n"
            "\n"
            "https://artist.bandcamp.com/\n"
            "   \n"
            "# trailing note\n"
            "https://www.youtube.com/@example\n"
        )

        lines = parse_batch_lines(text)

        self.assertEqual(
            [line.url for line in lines],
            ["https://artist.bandcamp.com/", "https://www.youtube.com/@example"],
        )

    def test_records_the_source_line_number_for_each_url(self) -> None:
        text = "# note\n\nhttps://artist.bandcamp.com/\nhttps://www.youtube.com/@example\n"

        lines = parse_batch_lines(text)

        self.assertEqual([line.line_number for line in lines], [3, 4])

    def test_reads_an_adapter_override_from_a_line(self) -> None:
        lines = parse_batch_lines("https://example.com/feed.xml  --adapter=podcast\n")

        self.assertEqual(lines, [BatchLine(url="https://example.com/feed.xml", adapter="podcast", line_number=1)])

    def test_leaves_adapter_empty_when_a_line_does_not_force_one(self) -> None:
        lines = parse_batch_lines("https://artist.bandcamp.com/\n")

        self.assertEqual(lines[0].adapter, "")

    def test_rejects_an_unknown_adapter_override(self) -> None:
        with self.assertRaises(ValueError) as raised:
            parse_batch_lines("https://example.com  --adapter=telepathy\n")

        self.assertIn("telepathy", str(raised.exception))

    def test_rejects_a_line_with_an_unsupported_trailing_token(self) -> None:
        with self.assertRaises(ValueError) as raised:
            parse_batch_lines("https://example.com  --group=Music\n")

        self.assertIn("--group", str(raised.exception))

    def test_every_supported_adapter_names_a_real_subcommand(self) -> None:
        self.assertEqual(
            set(BATCH_ADAPTER_COMMANDS),
            {"bandcamp", "youtube", "soundcloud", "substack", "mixcloud", "nts", "webpage", "podcast", "feed-url"},
        )


class DetectBatchAdapterTests(unittest.TestCase):
    def test_maps_known_platform_domains(self) -> None:
        self.assertEqual(detect_batch_adapter("https://artist.bandcamp.com/"), "bandcamp")
        self.assertEqual(detect_batch_adapter("https://bandcamp.com/fanname"), "bandcamp")
        self.assertEqual(detect_batch_adapter("https://www.youtube.com/@example"), "youtube")
        self.assertEqual(detect_batch_adapter("https://www.youtube.com/channel/UCabc"), "youtube")
        self.assertEqual(detect_batch_adapter("https://soundcloud.com/example"), "soundcloud")
        self.assertEqual(detect_batch_adapter("https://publication.substack.com/"), "substack")
        self.assertEqual(detect_batch_adapter("https://www.mixcloud.com/example/"), "mixcloud")
        self.assertEqual(detect_batch_adapter("https://www.nts.live/shows/example"), "nts")

    def test_routes_a_registered_webpage_recipe_url_to_the_webpage_adapter(self) -> None:
        self.assertEqual(detect_batch_adapter("https://hydefm.com/archives/"), "webpage")

    def test_falls_back_to_feed_discovery_for_an_unknown_domain(self) -> None:
        self.assertEqual(detect_batch_adapter("https://example.com/blog"), "feed-url")

    def test_a_youtube_watch_url_is_not_treated_as_a_channel(self) -> None:
        self.assertEqual(detect_batch_adapter("https://www.youtube.com/watch?v=abc"), "feed-url")

    def test_an_unrecognized_nts_path_is_not_treated_as_a_show(self) -> None:
        self.assertEqual(detect_batch_adapter("https://www.nts.live/latest"), "feed-url")


class BatchGuardTests(unittest.TestCase):
    """Guards against a subscription that succeeds but points at a dead feed."""

    def _run(self, tmp_dir: str, batch_text: str, *extra: str) -> tuple[int, str, FeedStore]:
        return BatchSubscribeCliTests._run_batch(self, tmp_dir, batch_text, *extra)

    def _run_with_dead_feed(self, tmp_dir: str, *extra: str) -> tuple[int, str, list]:
        """A YouTube channel whose advertised feed URL does not serve a feed.

        import-youtube-channel-url reads the channel page and takes the alternate
        link at its word without ever fetching it, so it reports success while
        writing a feed URL that answers with HTML. Substack cannot stand in here
        any more: it now checks its own feed before saving.
        """

        def fetch(url: str, **kwargs: object) -> str:
            if "feeds/videos.xml" in url:
                return "<html><body>not a feed</body></html>"
            return _fake_fetch(url, **kwargs)

        data_path = Path(tmp_dir) / "sources.json"
        batch_path = Path(tmp_dir) / "urls.txt"
        batch_path.write_text("https://www.youtube.com/@fixture\n", encoding="utf-8")
        output = io.StringIO()
        with patch("netnewswire_feed_booster.cli.fetch_text", side_effect=fetch), patch(
            "netnewswire_feed_booster.cli.time.sleep"
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                result = main(
                    [
                        "--data",
                        str(data_path),
                        "batch-subscribe",
                        str(batch_path),
                        "--profile",
                        "test-user",
                        *extra,
                    ]
                )
        return result, output.getvalue(), FeedStore(data_path).sources()

    def test_a_substack_profile_url_is_rejected_with_a_pointer_to_the_publication(self) -> None:
        batch_text = "https://substack.com/@ghosttropicssound/posts\nhttps://www.youtube.com/@fixture\n"
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run(tmp_dir, batch_text)
            sources = store.sources()

        self.assertEqual(result, 1)
        self.assertIn("publication subdomain", rendered)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].kind, "youtube")

    def test_a_publication_subdomain_is_not_rejected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result, _, store = self._run(tmp_dir, "https://publication.substack.com/\n")
            source_count = len(store.sources())

        self.assertEqual(result, 0)
        self.assertEqual(source_count, 1)

    def test_a_row_whose_feed_does_not_validate_is_reported_and_left_out_of_exports(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result, rendered, sources = self._run_with_dead_feed(tmp_dir)

        self.assertEqual(result, 1)
        self.assertIn("did not validate", rendered)
        self.assertIn("1 failed", rendered)
        # The row is kept for the record but demoted, so exports skip it.
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].status, "candidate")

    def test_verification_can_be_turned_off(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result, _, sources = self._run_with_dead_feed(tmp_dir, "--no-verify")

        self.assertEqual(result, 0)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].status, "active")

    def test_a_previously_unsubscribed_url_says_so_rather_than_already_subscribed(self) -> None:
        """Skipping is right; calling it "already subscribed" is not."""
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            seeded = FeedStore(data_path)
            seeded.add_or_update(
                Source(
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="file:///already/bandcamp-fixture-artist.rss",
                    site_url="https://fixture-artist.bandcamp.com/",
                    kind="bandcamp",
                    profiles=["test-user"],
                    groups=["Bandcamp"],
                )
            )
            seeded.set_status("bandcamp-fixture-artist", "unsubscribed")
            seeded.save()

            result, rendered, store = self._run(tmp_dir, "https://fixture-artist.bandcamp.com/\n")
            status = store.source_by_id("bandcamp-fixture-artist").status

        self.assertEqual(result, 0)
        self.assertIn("SKIPPED", rendered)
        self.assertIn("previously unsubscribed", rendered)
        self.assertIn("set-status", rendered)
        self.assertNotIn("already subscribed", rendered)
        self.assertEqual(status, "unsubscribed")

    def test_a_failed_verification_surfaces_the_feed_the_page_advertises(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            _, redacted, _ = self._run_with_dead_feed(tmp_dir)
        with TemporaryDirectory() as tmp_dir:
            _, revealed, _ = self._run_with_dead_feed(tmp_dir, "--show-sensitive")

        self.assertIn("advertises", revealed)
        self.assertIn("feeds/videos.xml", revealed)
        # The hint is a URL, so it follows the same redaction rule as everything else.
        self.assertNotIn("feeds/videos.xml", redacted)

    def test_a_generated_file_url_row_still_verifies(self) -> None:
        """Generated sources hold file:// feed URLs; those must not false-fail."""
        with TemporaryDirectory() as tmp_dir:
            result, rendered, store = self._run(tmp_dir, "https://fixture-artist.bandcamp.com/\n")
            sources = store.sources()

        self.assertEqual(result, 0)
        self.assertIn("OK", rendered)
        self.assertEqual(sources[0].status, "active")


if __name__ == "__main__":
    unittest.main()
