import unittest

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.hosted_bandcamp import (
    BandcampBotCheck,
    bandcamp_items_for_source,
    hosted_generated_feed_url,
    hosted_generated_source_id,
    hosted_status_feed_url,
    is_hosted_status_feed_url,
    render_bandcamp_source_rss,
    sources_with_hosted_generated_feeds,
    without_feed_token,
)


BANDCAMP_ARTIST_MUSIC_HTML = '''
<ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;band_id&quot;:1601512585,&quot;id&quot;:2261764695,&quot;page_url&quot;:&quot;/album/fixture-record&quot;,&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
'''
# The shape of the page Bandcamp sends a blocked network, as HTTP 200.
BANDCAMP_BOT_CHECK_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Client Challenge</title></head>
<body><noscript>JavaScript is disabled in your browser.</noscript></body></html>
"""


class HostedBandcampTests(unittest.TestCase):
    def test_hosted_generated_feed_url(self) -> None:
        self.assertEqual(
            hosted_generated_feed_url("https://example.modal.run/", "nts-fixture-signal", token="secret-token"),
            "https://example.modal.run/feeds/secret-token/generated/nts-fixture-signal.rss",
        )

    def test_hosted_generated_feed_url_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a token"):
            hosted_generated_feed_url("https://example.modal.run/", "nts-fixture-signal")

    def test_hosted_generated_source_id_reads_any_token_and_base_path(self) -> None:
        for base in ("https://bridge.example.com", "https://example.com/bridge"):
            for token in ("fixture-token", "another-token"):
                url = hosted_generated_feed_url(base, "nts-fixture-signal", token=token)
                self.assertEqual(hosted_generated_source_id(url), "nts-fixture-signal")

    def test_hosted_generated_source_id_rejects_other_shapes(self) -> None:
        for url in (
            "https://bridge.example.com/feeds/generated/nts-fixture-signal.rss",
            "https://bridge.example.com/feeds/fixture-token/status.rss",
            "https://bridge.example.com/feeds/fixture-token/generated/nested/nts-fixture-signal.rss",
            "file:///tmp/feeds/fixture-token/generated/nts-fixture-signal.rss",
            "https://feeds.example.com/fixture.rss",
        ):
            self.assertIsNone(hosted_generated_source_id(url), url)

    def test_is_hosted_status_feed_url(self) -> None:
        self.assertTrue(is_hosted_status_feed_url(hosted_status_feed_url("https://bridge.example.com", "fixture-token")))
        self.assertFalse(is_hosted_status_feed_url("https://bridge.example.com/feeds/status.rss"))
        self.assertFalse(
            is_hosted_status_feed_url(hosted_generated_feed_url("https://bridge.example.com", "status", token="fixture-token"))
        )

    def test_without_feed_token_masks_only_hosted_urls(self) -> None:
        self.assertEqual(
            without_feed_token(hosted_generated_feed_url("https://bridge.example.com", "nts-fixture-signal", token="fixture-token")),
            "https://bridge.example.com/feeds/[token]/generated/nts-fixture-signal.rss",
        )
        self.assertEqual(
            without_feed_token(hosted_status_feed_url("https://example.com/bridge/", "fixture-token")),
            "https://example.com/bridge/feeds/[token]/status.rss",
        )
        self.assertEqual(without_feed_token("https://feeds.example.com/fixture.rss"), "https://feeds.example.com/fixture.rss")

    def test_sources_with_hosted_generated_feeds_rewrites_only_recognized_sources(self) -> None:
        sources = [
            Source(
                id="bandcamp-fixture-artist",
                title="Bandcamp: Fixture Artist",
                feed_url="file:///tmp/bandcamp-fixture-artist.rss",
                site_url="https://fixture-artist.bandcamp.com/",
                kind="bandcamp",
            ),
            Source(
                id="example",
                title="Example",
                feed_url="https://example.com/feed",
                kind="website",
            ),
            Source(
                id="nts-fixture-signal",
                title="NTS: Fixture Signal",
                feed_url="file:///tmp/nts-fixture-signal.rss",
                site_url="https://www.nts.live/shows/fixture-signal",
                kind="other",
                source="nts-local-generated",
            ),
        ]

        rewritten = sources_with_hosted_generated_feeds(sources, "https://example.modal.run", token="secret-token")

        # Bandcamp and NTS both land on the same shared /generated/ route now.
        self.assertEqual(
            rewritten[0].feed_url,
            "https://example.modal.run/feeds/secret-token/generated/bandcamp-fixture-artist.rss",
        )
        self.assertEqual(rewritten[0].site_url, "https://fixture-artist.bandcamp.com/")
        self.assertEqual(rewritten[1].feed_url, "https://example.com/feed")
        self.assertEqual(
            rewritten[2].feed_url,
            "https://example.modal.run/feeds/secret-token/generated/nts-fixture-signal.rss",
        )

    def test_render_bandcamp_source_rss_uses_artist_music_url(self) -> None:
        fetched_urls = []
        source = Source(
            id="bandcamp-fixture-artist",
            title="Bandcamp: Fixture Artist",
            feed_url="file:///tmp/bandcamp-fixture-artist.rss",
            site_url="https://fixture-artist.bandcamp.com/",
            kind="bandcamp",
            groups=["Bandcamp Artists"],
        )

        def fetcher(url: str) -> str:
            fetched_urls.append(url)
            return BANDCAMP_ARTIST_MUSIC_HTML

        rss = render_bandcamp_source_rss(source, fetcher=fetcher)

        self.assertEqual(fetched_urls, ["https://fixture-artist.bandcamp.com/music"])
        self.assertIn("<title>Bandcamp: Fixture Artist</title>", rss)
        self.assertIn("<title>Fixture Artist - Fixture Record</title>", rss)

    def test_a_bot_check_page_is_named_rather_than_read_as_an_empty_page(self) -> None:
        artist = Source(
            id="bandcamp-fixture-artist",
            title="Bandcamp: Fixture Artist",
            feed_url="file:///tmp/bandcamp-fixture-artist.rss",
            site_url="https://fixture-artist.bandcamp.com/",
            kind="bandcamp",
        )
        fan = Source(
            id="bandcamp-fan-fixture",
            title="Bandcamp Fan: Fixture",
            feed_url="file:///tmp/bandcamp-fan-fixture.rss",
            site_url="https://bandcamp.com/fixture",
            kind="bandcamp",
        )
        dated = []

        for source in (artist, fan):
            with self.subTest(source=source.id):
                with self.assertRaisesRegex(BandcampBotCheck, f"bot check instead of {source.id}"):
                    render_bandcamp_source_rss(source, fetcher=lambda _url: BANDCAMP_BOT_CHECK_HTML)
        # It is caught before any follow-up request, such as the discography lookup.
        with self.assertRaises(BandcampBotCheck):
            bandcamp_items_for_source(artist, BANDCAMP_BOT_CHECK_HTML, post_band_details=lambda *args: dated.append(args))
        self.assertEqual(dated, [])
        # It is still a ValueError, so callers that skip a failed source keep doing so.
        self.assertTrue(issubclass(BandcampBotCheck, ValueError))
        self.assertTrue(bandcamp_items_for_source(artist, BANDCAMP_ARTIST_MUSIC_HTML))


if __name__ == "__main__":
    unittest.main()
