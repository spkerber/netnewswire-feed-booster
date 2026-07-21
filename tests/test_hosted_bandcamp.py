import unittest

from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.hosted_bandcamp import (
    hosted_bandcamp_feed_url,
    hosted_generated_feed_url,
    render_bandcamp_source_rss,
    sources_with_hosted_bandcamp_feeds,
)


BANDCAMP_ARTIST_MUSIC_HTML = '''
<ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;band_id&quot;:1601512585,&quot;id&quot;:2261764695,&quot;page_url&quot;:&quot;/album/fixture-record&quot;,&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
'''


class HostedBandcampTests(unittest.TestCase):
    def test_hosted_bandcamp_feed_url(self) -> None:
        self.assertEqual(
            hosted_bandcamp_feed_url("https://example.modal.run/", "bandcamp-fixture-artist", token="secret-token"),
            "https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-fixture-artist.rss",
        )

    def test_hosted_bandcamp_feed_url_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a token"):
            hosted_bandcamp_feed_url("https://example.modal.run/", "bandcamp-fixture-artist")

    def test_hosted_generated_feed_url(self) -> None:
        self.assertEqual(
            hosted_generated_feed_url("https://example.modal.run/", "nts-fixture-signal", token="secret-token"),
            "https://example.modal.run/feeds/secret-token/generated/nts-fixture-signal.rss",
        )

    def test_hosted_generated_feed_url_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a token"):
            hosted_generated_feed_url("https://example.modal.run/", "nts-fixture-signal")

    def test_sources_with_hosted_bandcamp_feeds_rewrites_only_bandcamp(self) -> None:
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

        rewritten = sources_with_hosted_bandcamp_feeds(sources, "https://example.modal.run", token="secret-token")

        self.assertEqual(
            rewritten[0].feed_url,
            "https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-fixture-artist.rss",
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


if __name__ == "__main__":
    unittest.main()
