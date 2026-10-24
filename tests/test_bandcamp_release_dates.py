import io
import unittest
from contextlib import redirect_stdout

from netnewswire_feed_booster.bandcamp import (
    add_bandcamp_release_dates,
    extract_bandcamp_band_id,
    parse_bandcamp_artist_music_html,
)
from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.hosted_bandcamp import bandcamp_items_for_source, render_bandcamp_source_rss


# Synthetic pages in the two /music layouts Bandcamp serves. The label page's
# releases carry their own artists' band ids; only data-band names the label.
LABEL_CLIENT_ITEMS_PAGE = (
    '<div id="pagedata" data-band="{&quot;id&quot;:24623638,&quot;name&quot;:&quot;Fixture Label&quot;}"></div>'
    '<ol id="music-grid" data-client-items="['
    "{&quot;art_id&quot;:1,&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;band_id&quot;:1111,&quot;id&quot;:2261764695,"
    "&quot;page_url&quot;:&quot;/album/fixture-record&quot;,&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;},"
    "{&quot;art_id&quot;:2,&quot;artist&quot;:&quot;Other Artist&quot;,&quot;band_id&quot;:2222,&quot;id&quot;:555,"
    "&quot;page_url&quot;:&quot;/track/fixture-single&quot;,&quot;title&quot;:&quot;Fixture Single&quot;,&quot;type&quot;:&quot;track&quot;},"
    "{&quot;art_id&quot;:3,&quot;artist&quot;:&quot;Fixture Artist&quot;,&quot;band_id&quot;:1111,&quot;id&quot;:777,"
    "&quot;page_url&quot;:&quot;/album/unlisted&quot;,&quot;title&quot;:&quot;Not In Discography&quot;,&quot;type&quot;:&quot;album&quot;}"
    ']"></ol>'
)

ARTIST_LEGACY_GRID_PAGE = """
<meta property="og:title" content="Fixture Artist">
<ol id="music-grid">
<li data-item-id="album-846808957"
    data-band-id="900165621"
    class="music-grid-item square first-four">
<a href="/album/twister">
    <div class="art"><img src="https://f4.bcbits.com/img/a111_2.jpg" alt=""></div>
    <p class="title">Twister</p>
</a>
</li>
<li data-item-id="track-123456"
    data-band-id="900165621"
    class="music-grid-item square">
<a href="/track/dat455">
    <div class="art"><img src="https://f4.bcbits.com/img/a222_2.jpg" alt=""></div>
    <p class="title">DAT455</p>
</a>
</li>
</ol>
"""

LABEL_DISCOGRAPHY = {
    "id": 24623638,
    "discography": [
        {"item_type": "album", "item_id": 2261764695, "release_date": "18 Sep 2026 00:00:00 GMT", "title": "Fixture Record"},
        {"item_type": "track", "item_id": 555, "release_date": "30 Jan 2026 09:18:53 GMT", "title": "Fixture Single"},
        {"item_type": "album", "item_id": 999, "release_date": "not a date", "title": "Broken Date"},
    ],
}

ARTIST_DISCOGRAPHY = {
    "discography": [
        {"item_type": "album", "item_id": 846808957, "release_date": "18 Sep 2026 00:00:00 GMT"},
        {"item_type": "track", "item_id": 123456, "release_date": "29 May 2025 07:59:20 GMT"},
    ],
}


class RecordingPoster:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.band_ids = []

    def __call__(self, band_id: int) -> dict:
        self.band_ids.append(band_id)
        if self.error:
            raise self.error
        return self.payload


def artist_source(site_url: str = "https://fixture.bandcamp.com") -> Source:
    return Source(
        id="bandcamp-fixture",
        title="Bandcamp: Fixture",
        feed_url="file:///tmp/bandcamp-fixture.rss",
        site_url=site_url,
        kind="bandcamp",
        profiles=["test"],
        source="bandcamp-local-generated",
    )


class ReleaseKeyTests(unittest.TestCase):
    def test_both_page_layouts_carry_release_keys(self) -> None:
        client = parse_bandcamp_artist_music_html(LABEL_CLIENT_ITEMS_PAGE, "https://label.bandcamp.com")
        legacy = parse_bandcamp_artist_music_html(ARTIST_LEGACY_GRID_PAGE, "https://fixture.bandcamp.com")

        self.assertEqual([item.release_key for item in client], ["album-2261764695", "track-555", "album-777"])
        self.assertEqual([item.release_key for item in legacy], ["album-846808957", "track-123456"])

    def test_band_id_prefers_the_page_owner_over_per_release_artists(self) -> None:
        self.assertEqual(extract_bandcamp_band_id(LABEL_CLIENT_ITEMS_PAGE), 24623638)
        self.assertEqual(extract_bandcamp_band_id(ARTIST_LEGACY_GRID_PAGE), 900165621)
        self.assertIsNone(extract_bandcamp_band_id("<html><body>No band here</body></html>"))


class ReleaseDateTests(unittest.TestCase):
    def test_dates_come_from_one_discography_request_per_artist(self) -> None:
        items = parse_bandcamp_artist_music_html(LABEL_CLIENT_ITEMS_PAGE, "https://label.bandcamp.com")
        poster = RecordingPoster(LABEL_DISCOGRAPHY)

        dated = add_bandcamp_release_dates(items, LABEL_CLIENT_ITEMS_PAGE, poster)

        self.assertEqual(poster.band_ids, [24623638])
        self.assertEqual(
            [item.collected_at for item in dated],
            ["18 Sep 2026 00:00:00 GMT", "30 Jan 2026 09:18:53 GMT", ""],
        )

        # No request at all when nothing could be dated: no release keys, or no band id.
        unused = RecordingPoster(ARTIST_DISCOGRAPHY)
        keyless = parse_bandcamp_artist_music_html(ARTIST_LEGACY_GRID_PAGE, "https://fixture.bandcamp.com")
        for item in keyless:
            item.release_key = ""
        add_bandcamp_release_dates(keyless, ARTIST_LEGACY_GRID_PAGE, unused)
        add_bandcamp_release_dates(
            parse_bandcamp_artist_music_html(ARTIST_LEGACY_GRID_PAGE, "https://fixture.bandcamp.com"),
            "<html>no band id</html>",
            unused,
        )
        self.assertEqual(unused.band_ids, [])

    def test_the_item_cap_keeps_the_newest_releases_once_dates_are_known(self) -> None:
        label = artist_source("https://label.bandcamp.com")
        newer_single = {
            "discography": [
                {"item_type": "album", "item_id": 2261764695, "release_date": "18 Sep 2026 00:00:00 GMT"},
                {"item_type": "track", "item_id": 555, "release_date": "01 Oct 2026 00:00:00 GMT"},
            ]
        }

        dated = bandcamp_items_for_source(label, LABEL_CLIENT_ITEMS_PAGE, max_items=2, post_band_details=RecordingPoster(newer_single))
        page_order = bandcamp_items_for_source(label, LABEL_CLIENT_ITEMS_PAGE, max_items=2)

        # The single is listed second on the page but released last.
        self.assertEqual([item.title for item in dated], ["Fixture Single", "Fixture Record"])
        self.assertEqual([item.title for item in page_order], ["Fixture Record", "Fixture Single"])

    def test_undated_releases_follow_dated_ones_and_a_failed_lookup_changes_nothing(self) -> None:
        label = artist_source("https://label.bandcamp.com")
        output = io.StringIO()

        items = bandcamp_items_for_source(label, LABEL_CLIENT_ITEMS_PAGE, max_items=None, post_band_details=RecordingPoster(LABEL_DISCOGRAPHY))
        with redirect_stdout(output):
            failed = bandcamp_items_for_source(
                label,
                LABEL_CLIENT_ITEMS_PAGE,
                max_items=None,
                post_band_details=RecordingPoster(error=ConnectionError("down")),
            )

        self.assertEqual([item.title for item in items], ["Fixture Record", "Fixture Single", "Not In Discography"])
        # A failed lookup leaves the feed working: page order, no dates, and a warning.
        self.assertEqual([item.title for item in failed], ["Fixture Record", "Fixture Single", "Not In Discography"])
        self.assertEqual([item.collected_at for item in failed], ["", "", ""])
        self.assertIn("release dates unavailable for band 24623638", output.getvalue())

    def test_rendered_feed_gets_pub_dates_only_when_a_lookup_is_passed(self) -> None:
        poster = RecordingPoster(ARTIST_DISCOGRAPHY)

        dated_rss = render_bandcamp_source_rss(artist_source(), fetcher=lambda _url: ARTIST_LEGACY_GRID_PAGE, post_band_details=poster)
        plain_rss = render_bandcamp_source_rss(artist_source(), fetcher=lambda _url: ARTIST_LEGACY_GRID_PAGE)

        self.assertIn("<pubDate>Fri, 18 Sep 2026 00:00:00 +0000</pubDate>", dated_rss)
        self.assertIn("<pubDate>Thu, 29 May 2025 07:59:20 +0000</pubDate>", dated_rss)
        self.assertNotIn("<pubDate>", plain_rss)
        self.assertEqual(poster.band_ids, [900165621])


if __name__ == "__main__":
    unittest.main()
