import unittest
from unittest.mock import patch

from netnewswire_feed_booster.bandcamp import (
    fetch_bandcamp_collection_items,
    parse_bandcamp_artist_music_html,
    parse_bandcamp_collection_html,
    render_bandcamp_collection_rss,
)
from netnewswire_feed_booster.http_client import fetch_text
from netnewswire_feed_booster.podcasts import title_from_url
from netnewswire_feed_booster.soundcloud import (
    extract_soundcloud_api_client_id,
    extract_soundcloud_app_version,
    extract_soundcloud_user_id,
    soundcloud_user_source,
)
from netnewswire_feed_booster.substack import (
    parse_substack_library_html,
    parse_substack_profile_html,
)
from netnewswire_feed_booster.youtube import (
    parse_youtube_channel_html,
    parse_youtube_subscriptions_html,
    parse_youtube_subscriptions_csv,
    parse_youtube_subscription_lines,
)


SUBSTACK_HTML = r'''
<script>window._preloads = JSON.parse("{\"profile\":{\"subscriptions\":[{\"publication\":{\"name\":\"Fixture Dispatch\",\"subdomain\":\"fixture-dispatch\",\"custom_domain\":\"fixture-dispatch.example\"}}]}}")</script>
'''

YOUTUBE_HTML = '''
<html>
  <head>
    <link rel="alternate" type="application/rss+xml" title="RSS" href="https://www.youtube.com/feeds/videos.xml?channel_id=UCfixture1234567890">
    <meta property="og:title" content="Fixture Video">
  </head>
</html>
'''

YOUTUBE_SUBSCRIPTIONS_HTML = r'''
<script>
{"channelRenderer":{"channelId":"UCfixture2345678901","title":{"simpleText":"Fixture Channel"},"navigationEndpoint":{"commandMetadata":{"webCommandMetadata":{"url":"/@fixture-channel"}},"browseEndpoint":{"browseId":"UCfixture2345678901","canonicalBaseUrl":"/@fixture-channel"}}}}
{"channelRenderer":{"channelId":"UCfixture2345678901","title":{"simpleText":"Fixture Channel"},"navigationEndpoint":{"commandMetadata":{"webCommandMetadata":{"url":"/@fixture-channel"}},"browseEndpoint":{"browseId":"UCfixture2345678901","canonicalBaseUrl":"/@fixture-channel"}}}}
</script>
'''

SUBSTACK_LIBRARY_HTML = '''
<a href="https://fixture-brief.example/" class="pencraft libraryItem-aPXCP4">
  <span>Fixture Brief</span>
</a>
<a href="https://fixture-dispatch.example/" class="pencraft libraryItem-aPXCP4">
  <span>Fixture Dispatch</span>
</a>
'''

BANDCAMP_HTML = '''
<div id="pagedata" data-blob="{&quot;item_cache&quot;:{&quot;collection&quot;:{&quot;a1&quot;:{&quot;item_title&quot;:&quot;fixture-album&quot;,&quot;band_name&quot;:&quot;fixture-artist&quot;,&quot;item_url&quot;:&quot;https://fixture-artist.bandcamp.com/album/fixture-album&quot;,&quot;item_type&quot;:&quot;album&quot;,&quot;item_art_id&quot;:537894927,&quot;featured_track_title&quot;:&quot;fixture-track&quot;,&quot;purchased&quot;:&quot;17 Jun 2026 17:28:07 GMT&quot;}}}}"></div>
'''

BANDCAMP_PAGINATED_HTML = '''
<div id="pagedata" data-blob="{&quot;fan_data&quot;:{&quot;fan_id&quot;:104653},&quot;collection_data&quot;:{&quot;item_count&quot;:3,&quot;batch_size&quot;:1,&quot;last_token&quot;:&quot;token-1&quot;},&quot;item_cache&quot;:{&quot;collection&quot;:{&quot;a1&quot;:{&quot;item_title&quot;:&quot;new one&quot;,&quot;band_name&quot;:&quot;First Artist&quot;,&quot;item_url&quot;:&quot;https://first.bandcamp.com/album/new-one&quot;,&quot;item_type&quot;:&quot;album&quot;,&quot;item_art_id&quot;:111,&quot;purchased&quot;:&quot;17 Jun 2026 17:28:07 GMT&quot;}}}}"></div>
'''

BANDCAMP_ARTIST_MUSIC_HTML = '''
<ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;band_id&quot;:1601512585,&quot;id&quot;:2261764695,&quot;page_url&quot;:&quot;/album/fixture-record&quot;,&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
'''

BANDCAMP_LEGACY_ARTIST_MUSIC_HTML = '''
<meta property="og:title" content="Fixture Legacy">
<ol id="music-grid">
  <li data-item-id="track-2912635660" class="music-grid-item square">
    <a href="/track/fixture-track">
      <div class="art"><img src="https://f4.bcbits.com/img/a2897198964_2.jpg" alt="" /></div>
      <p class="title">fixture track</p>
    </a>
  </li>
</ol>
'''

BANDCAMP_TRALBUM_HTML = '''
<meta property="og:title" content="Fixture Album, by Fixture Archive">
<meta property="og:type" content="album">
<meta property="og:site_name" content="Fixture Archive">
<meta property="og:image" content="https://f4.bcbits.com/img/a1373901960_5.jpg">
<meta property="og:url" content="https://fixture-archive.bandcamp.com/album/fixture-album">
<div data-tralbum="{&quot;current&quot;:{&quot;title&quot;:&quot;Fixture Album&quot;,&quot;publish_date&quot;:&quot;23 Feb 2026 00:33:16 GMT&quot;,&quot;artist&quot;:null}}"></div>
'''

SOUNDCLOUD_HTML = '''
<meta property="twitter:app:url:iphone" content="soundcloud://users:51978385">
<script>window.__sc_version="1782999645"</script>
<script>window.__sc_hydration = [{"hydratable":"apiClient","data":{"id":"client-123","isExpiring":false}}];</script>
'''


class SourceImporterTests(unittest.TestCase):
    def test_fetch_text_rejects_oversized_responses(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size: int) -> bytes:
                return b"x" * size

        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(ValueError):
                fetch_text("https://example.com/feed", max_bytes=4)

    def test_parse_substack_public_profile_subscriptions(self) -> None:
        sources = parse_substack_profile_html(SUBSTACK_HTML, profile="test-user", group="Substack")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "Fixture Dispatch")
        self.assertEqual(sources[0].feed_url, "https://fixture-dispatch.example/feed")
        self.assertEqual(sources[0].kind, "substack")

    def test_parse_youtube_channel_html_uses_rss_link(self) -> None:
        source = parse_youtube_channel_html(YOUTUBE_HTML, profile="test-user", group="YouTube", fallback_title="Example Channel")

        self.assertEqual(source.title, "Example Channel")
        self.assertEqual(source.feed_url, "https://www.youtube.com/feeds/videos.xml?channel_id=UCfixture1234567890")

    def test_parse_youtube_takeout_csv(self) -> None:
        csv_text = "Channel Id,Channel Url,Channel Title\nUC12345678901,https://www.youtube.com/channel/UC12345678901,Example Channel\n"

        sources = parse_youtube_subscriptions_csv(csv_text, profile="test-user", group="YouTube")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].id, "youtube-example-channel-uc12345678901")
        self.assertEqual(sources[0].title, "Example Channel")
        self.assertEqual(sources[0].feed_url, "https://www.youtube.com/feeds/videos.xml?channel_id=UC12345678901")

    def test_parse_youtube_takeout_csv_keeps_duplicate_titles(self) -> None:
        csv_text = (
            "Channel Id,Channel Url,Channel Title\n"
            "UC12345678901,https://www.youtube.com/channel/UC12345678901,Example Channel\n"
            "UC12345678902,https://www.youtube.com/channel/UC12345678902,Example Channel\n"
        )

        sources = parse_youtube_subscriptions_csv(csv_text, profile="test-user", group="YouTube")

        self.assertEqual(len(sources), 2)
        self.assertEqual(len({source.id for source in sources}), 2)
        self.assertEqual(len({source.feed_url for source in sources}), 2)

    def test_parse_youtube_saved_subscriptions_html(self) -> None:
        sources = parse_youtube_subscriptions_html(YOUTUBE_SUBSCRIPTIONS_HTML, profile="test-user", group="YouTube")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "Fixture Channel")
        self.assertEqual(sources[0].site_url, "https://www.youtube.com/@fixture-channel")

    def test_parse_youtube_plain_list(self) -> None:
        sources = parse_youtube_subscription_lines(
            ["UC12345678901\tExample Channel"],
            profile="test-user",
            group="YouTube",
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "Example Channel")

    def test_parse_substack_library_html(self) -> None:
        sources = parse_substack_library_html(SUBSTACK_LIBRARY_HTML, profile="test-user", group="Substack")

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].title, "Fixture Brief")
        self.assertEqual(sources[0].feed_url, "https://fixture-brief.example/feed")
        self.assertEqual(sources[1].feed_url, "https://fixture-dispatch.example/feed")

    def test_parse_bandcamp_collection_html(self) -> None:
        items = parse_bandcamp_collection_html(BANDCAMP_HTML)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "fixture-album")
        self.assertEqual(items[0].artist, "fixture-artist")
        self.assertEqual(items[0].artwork_url, "https://f4.bcbits.com/img/a537894927_10.jpg")

    def test_fetch_bandcamp_collection_items_paginates_and_dedupes(self) -> None:
        calls = []

        def post_collection_page(fan_id: int, token: str, count: int) -> dict:
            calls.append((fan_id, token, count))
            if token == "token-1":
                return {
                    "items": [
                        {
                            "item_title": "middle one",
                            "band_name": "Second Artist",
                            "item_url": "https://second.bandcamp.com/track/middle-one",
                            "item_type": "track",
                            "item_art_id": 222,
                            "purchased": "10 Jun 2026 17:28:07 GMT",
                        }
                    ],
                    "last_token": "token-2",
                    "more_available": True,
                }
            return {
                "items": [
                    {
                        "item_title": "middle one",
                        "band_name": "Second Artist",
                        "item_url": "https://second.bandcamp.com/track/middle-one",
                        "item_type": "track",
                        "item_art_id": 222,
                        "purchased": "10 Jun 2026 17:28:07 GMT",
                    },
                    {
                        "item_title": "old one",
                        "band_name": "Third Artist",
                        "item_url": "https://third.bandcamp.com/album/old-one",
                        "item_type": "album",
                        "item_art_id": 333,
                        "purchased": "01 Jun 2026 17:28:07 GMT",
                    },
                ],
                "last_token": "",
                "more_available": False,
            }

        items = fetch_bandcamp_collection_items(BANDCAMP_PAGINATED_HTML, post_collection_page=post_collection_page)

        self.assertEqual(calls, [(104653, "token-1", 1), (104653, "token-2", 1)])
        self.assertEqual([item.title for item in items], ["new one", "middle one", "old one"])
        self.assertEqual(len(items), 3)
        self.assertEqual(items[1].artwork_url, "https://f4.bcbits.com/img/a222_10.jpg")

    def test_render_bandcamp_collection_rss(self) -> None:
        items = parse_bandcamp_collection_html(BANDCAMP_HTML)
        rss = render_bandcamp_collection_rss("https://bandcamp.com/exampleuser", "Bandcamp: exampleuser", items)

        self.assertIn("<title>Bandcamp: exampleuser</title>", rss)
        self.assertIn("<title>fixture-artist - fixture-album</title>", rss)
        self.assertIn("<pubDate>Wed, 17 Jun 2026 17:28:07 +0000</pubDate>", rss)

    def test_parse_soundcloud_profile_bits(self) -> None:
        self.assertEqual(extract_soundcloud_user_id(SOUNDCLOUD_HTML), "51978385")
        self.assertEqual(extract_soundcloud_api_client_id(SOUNDCLOUD_HTML), "client-123")
        self.assertEqual(extract_soundcloud_app_version(SOUNDCLOUD_HTML), "1782999645")

    def test_soundcloud_user_source(self) -> None:
        source = soundcloud_user_source(
            {
                "id": 999999,
                "username": "Zorblax Quiver",
                "permalink_url": "https://soundcloud.com/zorblax-quiver",
            },
            profile="test-user",
            group="SoundCloud",
            source_label="soundcloud-following-import",
        )

        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.id, "soundcloud-zorblax-quiver-999999")
        self.assertEqual(source.title, "SoundCloud: Zorblax Quiver")
        self.assertEqual(source.feed_url, "https://feeds.soundcloud.com/users/soundcloud:users:999999/sounds.rss")
        self.assertEqual(source.site_url, "https://soundcloud.com/zorblax-quiver")

    def test_parse_bandcamp_artist_music_html(self) -> None:
        items = parse_bandcamp_artist_music_html(BANDCAMP_ARTIST_MUSIC_HTML, "https://fixture-artist.bandcamp.com")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Fixture Record")
        self.assertEqual(items[0].artist, "Fixture Artist")
        self.assertEqual(items[0].url, "https://fixture-artist.bandcamp.com/album/fixture-record")
        self.assertEqual(items[0].artwork_url, "https://f4.bcbits.com/img/a1463768112_10.jpg")

    def test_render_bandcamp_artist_music_rss(self) -> None:
        items = parse_bandcamp_artist_music_html(BANDCAMP_ARTIST_MUSIC_HTML, "https://fixture-artist.bandcamp.com")
        rss = render_bandcamp_collection_rss("https://fixture-artist.bandcamp.com", "Bandcamp: Fixture Artist", items)

        self.assertIn('<rss version="2.0">', rss)
        self.assertIn("<title>Bandcamp: Fixture Artist</title>", rss)
        self.assertIn("<title>Fixture Artist - Fixture Record</title>", rss)
        self.assertIn("<guid isPermaLink=\"true\">https://fixture-artist.bandcamp.com/album/fixture-record</guid>", rss)
        self.assertIn("&lt;img src=\"https://f4.bcbits.com/img/a1463768112_10.jpg\"", rss)

    def test_parse_bandcamp_legacy_artist_music_html(self) -> None:
        items = parse_bandcamp_artist_music_html(BANDCAMP_LEGACY_ARTIST_MUSIC_HTML, "https://fixture-legacy.bandcamp.com")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "fixture track")
        self.assertEqual(items[0].artist, "Fixture Legacy")
        self.assertEqual(items[0].item_type, "track")
        self.assertEqual(items[0].url, "https://fixture-legacy.bandcamp.com/track/fixture-track")

    def test_parse_bandcamp_tralbum_fallback(self) -> None:
        items = parse_bandcamp_artist_music_html(BANDCAMP_TRALBUM_HTML, "https://fixture-archive.bandcamp.com")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Fixture Album")
        self.assertEqual(items[0].artist, "Fixture Archive")
        self.assertEqual(items[0].item_type, "album")
        self.assertEqual(items[0].collected_at, "23 Feb 2026 00:33:16 GMT")

    def test_title_from_apple_podcast_url(self) -> None:
        self.assertEqual(
            title_from_url("https://podcasts.apple.com/us/podcast/fixture-podcast/id1234567890"),
            "Fixture Podcast",
        )


if __name__ == "__main__":
    unittest.main()
