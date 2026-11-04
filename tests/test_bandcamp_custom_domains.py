import io
import socket
import unittest
import urllib.request
from http.client import HTTPMessage
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.response import addinfourl

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.bandcamp import (
    BandcampCollectionItem,
    bandcamp_discography_items,
    bandcamp_release_urls_by_artwork,
    render_bandcamp_collection_rss,
)
from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.generated_adapters import BANDCAMP_ADAPTER
from netnewswire_feed_booster.hosted_bandcamp import (
    BandcampBotCheck,
    bandcamp_discography_items_for_source,
    bandcamp_fetch_url,
    fetch_bandcamp_discography,
    fetch_bandcamp_release_url,
    uses_bandcamp_discography,
)
from netnewswire_feed_booster.http_client import fetch_text


CUSTOM_DOMAIN = "music.example.com"
SUBDOMAIN = "fixture-artist.bandcamp.com"
BOT_CHECK_PAGE = "<html><head><title>Client Challenge</title></head><body></body></html>"

DISCOGRAPHY = {
    "id": 4242,
    "name": "Fixture Artist",
    "bandcamp_url": f"https://{CUSTOM_DOMAIN}",
    "discography": [
        {"item_type": "album", "item_id": 11, "art_id": 101, "title": "Older Record", "band_name": "Fixture Artist", "release_date": "02 Mar 2024 00:00:00 GMT"},
        {"item_type": "track", "item_id": 22, "art_id": 202, "title": "New Single", "artist_name": "Guest Artist", "band_name": "Fixture Artist", "release_date": "20 Oct 2026 00:00:00 GMT"},
        {"item_type": "album", "item_id": 33, "art_id": 303, "title": "Middle Record", "band_name": "Fixture Artist", "release_date": "05 Jun 2025 00:00:00 GMT"},
        {"item_type": "album", "item_id": 44, "art_id": 404, "title": "Undated Record", "band_name": "Fixture Artist", "release_date": None},
        {"item_type": "merch", "item_id": 55, "art_id": 505, "title": "Not A Release"},
    ],
}


def custom_domain_source(band_id=4242, site_url=f"https://{CUSTOM_DOMAIN}", source_id="bandcamp-fixture-artist") -> Source:
    return Source(
        id=source_id,
        title="Bandcamp: Fixture Artist",
        feed_url=f"file:///generated/{source_id}.rss",
        site_url=site_url,
        kind="bandcamp",
        profiles=["test-user"],
        groups=["Bandcamp"],
        bandcamp_band_id=band_id,
    )


def previous_feed(*links_and_art_ids) -> str:
    """A feed as the /music-page refresh wrote it: links on the custom domain, art in the description."""
    items = [
        BandcampCollectionItem(
            title=f"Old title {index}",
            artist="",
            url=link,
            item_type="album",
            artwork_url=f"https://f4.bcbits.com/img/a{art_id}_10.jpg",
        )
        for index, (link, art_id) in enumerate(links_and_art_ids)
    ]
    return render_bandcamp_collection_rss(profile_url=f"https://{CUSTOM_DOMAIN}", title="Bandcamp: Fixture Artist", items=items)


def public_address(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]


class CustomDomainRedirectLoopTests(unittest.TestCase):
    def test_a_looping_storefront_fails_as_a_redirect_loop_not_as_an_unsafe_host(self) -> None:
        """What a custom-domain storefront does to this tool's requests.

        The custom domain answers 303 to the artist's bandcamp.com address, and
        that address answers 301 straight back. Both hosts pass the source's
        allowlist, so the request isn't refused as unsafe: urllib follows the
        loop until it gives up.
        """
        requested = []

        def ping_pong(_handler, request):
            requested.append(request.full_url)
            path = urlparse(request.full_url).path
            host = urlparse(request.full_url).hostname
            target = f"https://{SUBDOMAIN}{path}" if host == CUSTOM_DOMAIN else f"https://{CUSTOM_DOMAIN}{path}"
            headers = HTTPMessage()
            headers["Location"] = target
            code = 303 if host == CUSTOM_DOMAIN else 301
            response = addinfourl(io.BytesIO(b""), headers, request.full_url, code)
            response.msg = "See Other" if code == 303 else "Moved Permanently"
            return response

        source = custom_domain_source(band_id=None)
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", side_effect=public_address):
            with patch.object(urllib.request.HTTPSHandler, "https_open", ping_pong):
                with self.assertRaises(HTTPError) as caught:
                    fetch_text(
                        bandcamp_fetch_url(source),
                        allowed_hosts=BANDCAMP_ADAPTER.allowed_hosts_for(source),
                        allowed_suffixes=BANDCAMP_ADAPTER.allowed_suffixes_for(source),
                    )
        caught.exception.close()

        self.assertIn("infinite loop", str(caught.exception))
        self.assertEqual({urlparse(url).hostname for url in requested}, {CUSTOM_DOMAIN, SUBDOMAIN})
        self.assertGreater(len(requested), 4)

    def test_the_redirect_allowlist_still_refuses_any_other_host(self) -> None:
        source = custom_domain_source()
        handler_hosts = BANDCAMP_ADAPTER.allowed_hosts_for(source)
        self.assertEqual(handler_hosts, frozenset({"bandcamp.com", CUSTOM_DOMAIN}))
        with patch("urllib.request.OpenerDirector.open") as urlopen:
            with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
                fetch_text(
                    "https://other.example.net/music",
                    allowed_hosts=handler_hosts,
                    allowed_suffixes=BANDCAMP_ADAPTER.allowed_suffixes_for(source),
                )
        urlopen.assert_not_called()


class UsesBandcampDiscographyTests(unittest.TestCase):
    def test_a_custom_domain_artist_with_a_band_id_reads_its_discography(self) -> None:
        self.assertTrue(uses_bandcamp_discography(custom_domain_source()))

    def test_a_custom_domain_artist_without_a_band_id_keeps_its_page(self) -> None:
        self.assertFalse(uses_bandcamp_discography(custom_domain_source(band_id=None)))

    def test_a_bandcamp_subdomain_keeps_its_page_even_with_a_band_id(self) -> None:
        self.assertFalse(uses_bandcamp_discography(custom_domain_source(site_url=f"https://{SUBDOMAIN}")))

    def test_a_fan_page_never_reads_a_discography(self) -> None:
        fan = custom_domain_source(site_url="https://bandcamp.com/fixture-fan", source_id="bandcamp-fan-fixture")
        self.assertFalse(uses_bandcamp_discography(fan))


class DiscographyItemsTests(unittest.TestCase):
    def test_releases_come_newest_first_with_dates_artists_and_artwork(self) -> None:
        lookups = []

        def lookup(release_key: str) -> str:
            lookups.append(release_key)
            kind, _, item_id = release_key.partition("-")
            return f"https://{CUSTOM_DOMAIN}/{kind}/release-{item_id}"

        items = bandcamp_discography_items(DISCOGRAPHY, f"https://{CUSTOM_DOMAIN}", known_urls={}, lookup_release_url=lookup)

        self.assertEqual([item.title for item in items], ["New Single", "Middle Record", "Older Record", "Undated Record"])
        self.assertEqual(lookups, ["track-22", "album-33", "album-11", "album-44"])
        self.assertEqual(items[0].artist, "Guest Artist")
        self.assertEqual(items[1].artist, "Fixture Artist")
        self.assertEqual(items[0].item_type, "track")
        self.assertEqual(items[0].url, f"https://{CUSTOM_DOMAIN}/track/release-22")
        self.assertEqual(items[0].artwork_url, "https://f4.bcbits.com/img/a202_10.jpg")
        self.assertEqual(items[0].collected_at, "20 Oct 2026 00:00:00 GMT")
        self.assertEqual(items[3].collected_at, "")

    def test_releases_the_previous_feed_linked_keep_their_links_without_a_lookup(self) -> None:
        known = bandcamp_release_urls_by_artwork(
            previous_feed(
                (f"https://{CUSTOM_DOMAIN}/album/older-record", 101),
                (f"https://{CUSTOM_DOMAIN}/album/middle-record", 303),
            )
        )
        lookups = []

        def lookup(release_key: str) -> str:
            lookups.append(release_key)
            return f"https://{SUBDOMAIN}/track/new-single"

        items = bandcamp_discography_items(
            DISCOGRAPHY, f"https://{CUSTOM_DOMAIN}", known_urls=known, lookup_release_url=lookup, max_lookups=1
        )

        self.assertEqual(lookups, ["track-22"])
        self.assertEqual(
            [item.url for item in items],
            [
                f"https://{SUBDOMAIN}/track/new-single",
                f"https://{CUSTOM_DOMAIN}/album/middle-record",
                f"https://{CUSTOM_DOMAIN}/album/older-record",
            ],
        )

    def test_a_rebuilt_feed_keeps_every_guid_the_page_built_feed_had(self) -> None:
        old_links = [(f"https://{CUSTOM_DOMAIN}/album/older-record", 101), (f"https://{CUSTOM_DOMAIN}/album/middle-record", 303)]
        source = custom_domain_source()
        discography = {**DISCOGRAPHY, "discography": [DISCOGRAPHY["discography"][0], DISCOGRAPHY["discography"][2]]}

        items = bandcamp_discography_items_for_source(
            source,
            previous_feed=previous_feed(*old_links),
            fetch_discography=lambda band_id: discography,
            fetch_release_url=lambda band_id, release_key: self.fail(f"looked up {release_key}"),
        )
        rss = render_bandcamp_collection_rss(profile_url=source.site_url, title=source.title, items=items)

        for link, _art_id in old_links:
            self.assertIn(f'<guid isPermaLink="true">{link}</guid>', rss)
        self.assertIn("<title>Fixture Artist - Middle Record</title>", rss)

    def test_lookups_stop_at_the_budget_and_unlinked_releases_wait_for_a_later_refresh(self) -> None:
        lookups = []

        def lookup(release_key: str) -> str:
            lookups.append(release_key)
            return f"https://{CUSTOM_DOMAIN}/album/{release_key}"

        items = bandcamp_discography_items(
            DISCOGRAPHY, f"https://{CUSTOM_DOMAIN}", known_urls={}, lookup_release_url=lookup, max_lookups=2
        )

        self.assertEqual(lookups, ["track-22", "album-33"])
        self.assertEqual([item.title for item in items], ["New Single", "Middle Record"])

    def test_the_item_cap_keeps_the_newest_releases(self) -> None:
        items = bandcamp_discography_items(
            DISCOGRAPHY,
            f"https://{CUSTOM_DOMAIN}",
            known_urls={},
            lookup_release_url=lambda key: f"https://{CUSTOM_DOMAIN}/album/{key}",
            max_items=2,
        )

        self.assertEqual([item.title for item in items], ["New Single", "Middle Record"])

    def test_release_urls_off_the_storefront_or_not_release_pages_are_dropped(self) -> None:
        answers = {
            "track-22": "https://other.example.net/track/new-single",
            "album-33": f"https://{CUSTOM_DOMAIN}/music",
            "album-11": "javascript:alert(1)",
            "album-44": f"http://{CUSTOM_DOMAIN}/album/undated-record",
        }
        known = {303: "https://other.example.net/album/middle-record"}

        items = bandcamp_discography_items(
            DISCOGRAPHY, f"https://{CUSTOM_DOMAIN}", known_urls=known, lookup_release_url=answers.__getitem__
        )

        self.assertEqual(items, [])

    def test_two_releases_sharing_a_cover_do_not_share_a_link(self) -> None:
        discography = {
            "name": "Fixture Artist",
            "discography": [
                {"item_type": "album", "item_id": 1, "art_id": 900, "title": "The Album", "release_date": "01 Feb 2026 00:00:00 GMT"},
                {"item_type": "track", "item_id": 2, "art_id": 900, "title": "The Single", "release_date": "01 Jan 2026 00:00:00 GMT"},
            ],
        }
        known = {900: f"https://{CUSTOM_DOMAIN}/album/the-album"}

        items = bandcamp_discography_items(
            discography,
            f"https://{CUSTOM_DOMAIN}",
            known_urls=known,
            lookup_release_url=lambda key: f"https://{CUSTOM_DOMAIN}/track/the-single",
        )

        self.assertEqual([item.url for item in items], [f"https://{CUSTOM_DOMAIN}/album/the-album", f"https://{CUSTOM_DOMAIN}/track/the-single"])

    def test_a_response_without_a_discography_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "no discography"):
            bandcamp_discography_items({"error": True}, f"https://{CUSTOM_DOMAIN}", known_urls={}, lookup_release_url=str)


class ReleaseUrlsByArtworkTests(unittest.TestCase):
    def test_an_artwork_id_two_items_share_with_different_links_is_left_out(self) -> None:
        known = bandcamp_release_urls_by_artwork(
            previous_feed(
                (f"https://{CUSTOM_DOMAIN}/album/one", 7),
                (f"https://{CUSTOM_DOMAIN}/track/two", 7),
                (f"https://{CUSTOM_DOMAIN}/album/three", 8),
            )
        )

        self.assertEqual(known, {8: f"https://{CUSTOM_DOMAIN}/album/three"})

    def test_an_unreadable_feed_gives_no_links(self) -> None:
        self.assertEqual(bandcamp_release_urls_by_artwork("not a feed"), {})


class BandcampApiFetchTests(unittest.TestCase):
    def test_the_discography_request_goes_to_bandcamp_com_only(self) -> None:
        with patch("netnewswire_feed_booster.hosted_bandcamp.fetch_text_post", return_value='{"discography": []}') as post:
            self.assertEqual(fetch_bandcamp_discography(4242), {"discography": []})

        url, payload = post.call_args.args
        self.assertEqual(urlparse(url).hostname, "bandcamp.com")
        self.assertEqual(payload, {"band_id": 4242})
        self.assertEqual(post.call_args.kwargs["allowed_hosts"], frozenset({"bandcamp.com"}))

    def test_a_release_lookup_asks_bandcamp_com_for_that_release(self) -> None:
        with patch(
            "netnewswire_feed_booster.hosted_bandcamp.fetch_text",
            return_value=f'{{"bandcamp_url": "https://{CUSTOM_DOMAIN}/track/new-single"}}',
        ) as get:
            self.assertEqual(fetch_bandcamp_release_url(4242, "track-22"), f"https://{CUSTOM_DOMAIN}/track/new-single")

        url = get.call_args.args[0]
        self.assertEqual(urlparse(url).hostname, "bandcamp.com")
        self.assertEqual(parse_qs(urlparse(url).query), {"band_id": ["4242"], "tralbum_id": ["22"], "tralbum_type": ["t"]})
        self.assertEqual(get.call_args.kwargs["allowed_hosts"], frozenset({"bandcamp.com"}))

    def test_a_bot_check_from_either_api_is_named_rather_than_read_as_bad_json(self) -> None:
        with patch("netnewswire_feed_booster.hosted_bandcamp.fetch_text_post", return_value=BOT_CHECK_PAGE):
            with self.assertRaises(BandcampBotCheck):
                fetch_bandcamp_discography(4242)
        with patch("netnewswire_feed_booster.hosted_bandcamp.fetch_text", return_value=BOT_CHECK_PAGE):
            with self.assertRaises(BandcampBotCheck):
                fetch_bandcamp_release_url(4242, "album-11")


if __name__ == "__main__":
    unittest.main()
