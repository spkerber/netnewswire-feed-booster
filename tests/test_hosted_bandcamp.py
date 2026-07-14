import unittest

from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.hosted_bandcamp import (
    hosted_bandcamp_feed_url,
    hosted_generated_feed_url,
    render_bandcamp_source_rss,
    sources_with_hosted_bandcamp_feeds,
)


BANDCAMP_ARTIST_MUSIC_HTML = '''
<ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Ghost Dubs&quot;,&quot;band_id&quot;:1601512585,&quot;id&quot;:2261764695,&quot;page_url&quot;:&quot;/album/damaged&quot;,&quot;title&quot;:&quot;Damaged&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
'''


class HostedBandcampTests(unittest.TestCase):
    def test_hosted_bandcamp_feed_url(self) -> None:
        self.assertEqual(
            hosted_bandcamp_feed_url("https://example.modal.run/", "bandcamp-ghost-dubs", token="secret-token"),
            "https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-ghost-dubs.rss",
        )

    def test_hosted_bandcamp_feed_url_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a token"):
            hosted_bandcamp_feed_url("https://example.modal.run/", "bandcamp-ghost-dubs")

    def test_hosted_generated_feed_url(self) -> None:
        self.assertEqual(
            hosted_generated_feed_url("https://example.modal.run/", "nts-nkisi", token="secret-token"),
            "https://example.modal.run/feeds/secret-token/generated/nts-nkisi.rss",
        )

    def test_hosted_generated_feed_url_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a token"):
            hosted_generated_feed_url("https://example.modal.run/", "nts-nkisi")

    def test_sources_with_hosted_bandcamp_feeds_rewrites_only_bandcamp(self) -> None:
        sources = [
            Source(
                id="bandcamp-ghost-dubs",
                title="Bandcamp: Ghost Dubs",
                feed_url="file:///tmp/bandcamp-ghost-dubs.rss",
                site_url="https://ghostdubs.bandcamp.com/",
                kind="bandcamp",
            ),
            Source(
                id="example",
                title="Example",
                feed_url="https://example.com/feed",
                kind="website",
            ),
            Source(
                id="nts-nkisi",
                title="NTS: NKISI",
                feed_url="file:///tmp/nts-nkisi.rss",
                site_url="https://www.nts.live/shows/nkisi",
                kind="other",
                source="nts-local-generated",
            ),
        ]

        rewritten = sources_with_hosted_bandcamp_feeds(sources, "https://example.modal.run", token="secret-token")

        self.assertEqual(
            rewritten[0].feed_url,
            "https://example.modal.run/feeds/secret-token/bandcamp/bandcamp-ghost-dubs.rss",
        )
        self.assertEqual(rewritten[0].site_url, "https://ghostdubs.bandcamp.com/")
        self.assertEqual(rewritten[1].feed_url, "https://example.com/feed")
        self.assertEqual(
            rewritten[2].feed_url,
            "https://example.modal.run/feeds/secret-token/generated/nts-nkisi.rss",
        )

    def test_render_bandcamp_source_rss_uses_artist_music_url(self) -> None:
        fetched_urls = []
        source = Source(
            id="bandcamp-ghost-dubs",
            title="Bandcamp: Ghost Dubs",
            feed_url="file:///tmp/bandcamp-ghost-dubs.rss",
            site_url="https://ghostdubs.bandcamp.com/",
            kind="bandcamp",
            groups=["Bandcamp Artists"],
        )

        def fetcher(url: str) -> str:
            fetched_urls.append(url)
            return BANDCAMP_ARTIST_MUSIC_HTML

        rss = render_bandcamp_source_rss(source, fetcher=fetcher)

        self.assertEqual(fetched_urls, ["https://ghostdubs.bandcamp.com/music"])
        self.assertIn("<title>Bandcamp: Ghost Dubs</title>", rss)
        self.assertIn("<title>Ghost Dubs - Damaged</title>", rss)


if __name__ == "__main__":
    unittest.main()
