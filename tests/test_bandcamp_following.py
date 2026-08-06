import json
import unittest
from pathlib import Path
from unittest.mock import patch

from netnewswire_feed_booster.bandcamp_following import (
    bandcamp_following_band_source,
    bandcamp_following_fan_source,
    fetch_bandcamp_following_bands,
    fetch_bandcamp_following_fans,
    import_bandcamp_following,
)


def following_bands_page_html(item_count: int, batch_size: int = 20, embedded_ids: list[str] | None = None) -> str:
    embedded_ids = ["1001", "1002"] if embedded_ids is None else embedded_ids
    cache = {
        band_id: {
            "band_id": int(band_id),
            "name": f"Fixture Artist {band_id}",
            "url_hints": {"subdomain": f"fixture-artist-{band_id}", "custom_domain": None},
        }
        for band_id in embedded_ids
    }
    blob = {
        "fan_data": {"fan_id": 104653},
        "following_bands_data": {
            "last_token": "1785267986:1379977449",
            "batch_size": batch_size,
            "item_count": item_count,
        },
        "item_cache": {"following_bands": cache},
    }
    payload = json.dumps(blob).replace('"', "&quot;")
    return f'<div id="pagedata" data-blob="{payload}"></div>'


def following_fans_page_html(item_count: int, embedded_ids: list[str] | None = None) -> str:
    embedded_ids = ["2001"] if embedded_ids is None else embedded_ids
    cache = {
        fan_id: {
            "fan_id": int(fan_id),
            "name": f"Fixture Fan {fan_id}",
            "trackpipe_url": f"https://bandcamp.com/fixture-fan-{fan_id}",
        }
        for fan_id in embedded_ids
    }
    blob = {
        "fan_data": {"fan_id": 104653},
        "following_fans_data": {"last_token": "1607104367:4600", "batch_size": 20, "item_count": item_count},
        "item_cache": {"following_fans": cache},
    }
    payload = json.dumps(blob).replace('"', "&quot;")
    return f'<div id="pagedata" data-blob="{payload}"></div>'


class BandcampFollowingTests(unittest.TestCase):
    def test_bandcamp_following_band_source_uses_subdomain(self) -> None:
        item = {
            "band_id": 2702912497,
            "name": "Kino Disk",
            "url_hints": {"subdomain": "kinodisk", "custom_domain": None},
        }

        source = bandcamp_following_band_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"))

        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.id, "bandcamp-kino-disk")
        self.assertEqual(source.title, "Bandcamp: Kino Disk")
        self.assertEqual(source.site_url, "https://kinodisk.bandcamp.com")
        self.assertEqual(source.kind, "bandcamp")
        self.assertEqual(source.source, "bandcamp-following-import")
        self.assertTrue(source.feed_url.endswith("/exports/generated/bandcamp-kino-disk.rss"))

    def test_bandcamp_following_band_source_uses_custom_domain(self) -> None:
        item = {"band_id": 1, "name": "Custom Label", "url_hints": {"subdomain": None, "custom_domain": "label.example"}}

        source = bandcamp_following_band_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"))

        assert source is not None
        self.assertEqual(source.site_url, "https://label.example")

    def test_bandcamp_following_band_source_returns_none_without_a_resolvable_url(self) -> None:
        item = {"band_id": 1, "name": "No URL", "url_hints": {}}

        self.assertIsNone(bandcamp_following_band_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated")))

    def test_bandcamp_following_band_source_rejects_a_subdomain_containing_a_slash(self) -> None:
        # A subdomain containing "/" would otherwise break the intended
        # {subdomain}.bandcamp.com host boundary and silently produce a different,
        # unintended real host — e.g. "evil.example.com/x" -> site_url host
        # becomes evil.example.com, not "evil.example.com/x".bandcamp.com.
        item = {
            "band_id": 999,
            "name": "Malicious Test",
            "url_hints": {"subdomain": "evil.example.com/x", "custom_domain": None},
        }

        self.assertIsNone(bandcamp_following_band_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated")))

    def test_bandcamp_following_band_source_rejects_an_ip_literal_custom_domain(self) -> None:
        item = {"band_id": 1, "name": "IP Literal", "url_hints": {"subdomain": None, "custom_domain": "192.0.2.10"}}

        self.assertIsNone(bandcamp_following_band_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated")))

    def test_bandcamp_following_fan_source_rejects_a_non_bandcamp_trackpipe_url(self) -> None:
        item = {"fan_id": 1, "name": "Fake Fan", "trackpipe_url": "https://metadata.example/fake"}

        self.assertIsNone(bandcamp_following_fan_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated")))

    def test_bandcamp_following_fan_source_uses_trackpipe_url(self) -> None:
        item = {"fan_id": 1305711, "name": "Cole Pulice", "trackpipe_url": "https://bandcamp.com/colepulice"}

        source = bandcamp_following_fan_source(item, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"))

        assert source is not None
        self.assertEqual(source.id, "bandcamp-fan-cole-pulice")
        self.assertEqual(source.title, "Bandcamp Fan: Cole Pulice")
        self.assertEqual(source.site_url, "https://bandcamp.com/colepulice")

    def test_fetch_bandcamp_following_bands_resolves_title_collisions_uniquely(self) -> None:
        # Two different real artists sharing a display name, plus one whose name is
        # entirely symbols and doesn't survive slugify — all three must stay distinct.
        html = following_bands_page_html(item_count=3, batch_size=20, embedded_ids=[])

        def post_page(api_url, fan_id, older_than_token, count):
            return {
                "followeers": [
                    {"band_id": 1, "name": "Home", "url_hints": {"subdomain": "home-artist-one"}},
                    {"band_id": 2, "name": "Home", "url_hints": {"subdomain": "home-artist-two"}},
                    {"band_id": 3, "name": "@@", "url_hints": {"subdomain": "symbols-only-artist"}},
                ],
                "more_available": False,
                "last_token": "",
            }

        sources = fetch_bandcamp_following_bands(
            html, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"), post_page=post_page
        )

        self.assertEqual(len(sources), 3)
        ids = {source.id for source in sources}
        self.assertEqual(len(ids), 3, f"expected 3 distinct ids, got collisions: {ids}")
        for source in sources:
            self.assertTrue(source.feed_url.endswith(f"/{source.id}.rss"), f"feed_url out of sync with id for {source.id}")
        site_urls = {source.site_url for source in sources}
        self.assertEqual(
            site_urls,
            {
                "https://home-artist-one.bandcamp.com",
                "https://home-artist-two.bandcamp.com",
                "https://symbols-only-artist.bandcamp.com",
            },
        )

    def test_fetch_bandcamp_following_bands_stops_at_the_page_cap_for_a_misbehaving_api(self) -> None:
        # item_count is deliberately unreachable (way higher than what any page
        # ever returns) and more_available never goes False, simulating a
        # misbehaving or malicious API response. Termination must not depend
        # entirely on the API's own self-reported counters.
        html = following_bands_page_html(item_count=1_000_000, batch_size=1, embedded_ids=[])
        call_count = 0

        def post_page(api_url, fan_id, older_than_token, count):
            nonlocal call_count
            call_count += 1
            return {
                "followeers": [{"band_id": call_count, "name": f"Artist {call_count}", "url_hints": {"subdomain": f"artist-{call_count}"}}],
                "more_available": True,
                "last_token": f"token-{call_count}",
            }

        with patch("netnewswire_feed_booster.bandcamp_following.time.sleep"):
            sources = fetch_bandcamp_following_bands(
                html, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"), post_page=post_page
            )

        from netnewswire_feed_booster.bandcamp_following import MAX_FOLLOWING_PAGES

        self.assertEqual(call_count, MAX_FOLLOWING_PAGES)
        self.assertEqual(len(sources), MAX_FOLLOWING_PAGES)

    def test_fetch_bandcamp_following_bands_paginates_until_exhausted(self) -> None:
        html = following_bands_page_html(item_count=5, batch_size=2, embedded_ids=["1001", "1002"])
        calls: list[tuple] = []

        def post_page(api_url, fan_id, older_than_token, count):
            calls.append((api_url, fan_id, older_than_token, count))
            if len(calls) == 1:
                return {
                    "followeers": [
                        {"band_id": 1003, "name": "Fixture Artist 1003", "url_hints": {"subdomain": "fixture-artist-1003"}},
                        {"band_id": 1004, "name": "Fixture Artist 1004", "url_hints": {"subdomain": "fixture-artist-1004"}},
                    ],
                    "more_available": True,
                    "last_token": "token-2",
                }
            return {
                "followeers": [
                    {"band_id": 1005, "name": "Fixture Artist 1005", "url_hints": {"subdomain": "fixture-artist-1005"}},
                ],
                "more_available": False,
                "last_token": "",
            }

        sources = fetch_bandcamp_following_bands(
            html, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"), post_page=post_page
        )

        self.assertEqual(len(sources), 5)
        self.assertEqual(len(calls), 2)
        self.assertEqual({source.id for source in sources}, {
            "bandcamp-fixture-artist-1001",
            "bandcamp-fixture-artist-1002",
            "bandcamp-fixture-artist-1003",
            "bandcamp-fixture-artist-1004",
            "bandcamp-fixture-artist-1005",
        })

    def test_fetch_bandcamp_following_bands_stops_without_extra_calls_when_fully_embedded(self) -> None:
        html = following_bands_page_html(item_count=2, batch_size=20, embedded_ids=["1001", "1002"])
        calls: list[tuple] = []

        def post_page(api_url, fan_id, older_than_token, count):
            calls.append((api_url, fan_id, older_than_token, count))
            return {"followeers": [], "more_available": False, "last_token": ""}

        sources = fetch_bandcamp_following_bands(
            html, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"), post_page=post_page
        )

        self.assertEqual(len(sources), 2)
        self.assertEqual(calls, [])

    def test_fetch_bandcamp_following_fans_resolves_from_embedded_cache(self) -> None:
        html = following_fans_page_html(item_count=1, embedded_ids=["2001"])

        sources = fetch_bandcamp_following_fans(
            html, profile="test-user", group="Bandcamp", out_dir=Path("exports/generated"), post_page=None
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "Bandcamp Fan: Fixture Fan 2001")

    def test_import_bandcamp_following_fetches_both_lists(self) -> None:
        fetched_urls: list[str] = []

        def fetcher(url: str) -> str:
            fetched_urls.append(url)
            if url.endswith("/following/artists_and_labels"):
                return following_bands_page_html(item_count=2, batch_size=20, embedded_ids=["1001", "1002"])
            return following_fans_page_html(item_count=1, embedded_ids=["2001"])

        sources = import_bandcamp_following(
            "https://bandcamp.com/skerbz",
            profile="test-user",
            out_dir=Path("exports/generated"),
            fetcher=fetcher,
            post_page=lambda *args: {"followeers": [], "more_available": False, "last_token": ""},
        )

        self.assertEqual(len(sources), 3)
        self.assertEqual(
            fetched_urls,
            ["https://bandcamp.com/skerbz/following/artists_and_labels", "https://bandcamp.com/skerbz/following/fans"],
        )
        self.assertEqual(len([source for source in sources if source.title.startswith("Bandcamp Fan:")]), 1)

    def test_import_bandcamp_following_keeps_already_fetched_bands_when_fans_fetch_fails(self) -> None:
        fetched_urls: list[str] = []

        def fetcher(url: str) -> str:
            fetched_urls.append(url)
            if url.endswith("/following/artists_and_labels"):
                return following_bands_page_html(item_count=2, batch_size=20, embedded_ids=["1001", "1002"])
            raise ConnectionError("simulated network failure fetching the fans list")

        sources = import_bandcamp_following(
            "https://bandcamp.com/skerbz",
            profile="test-user",
            out_dir=Path("exports/generated"),
            fetcher=fetcher,
            post_page=lambda *args: {"followeers": [], "more_available": False, "last_token": ""},
        )

        # The bands fetch fully succeeded before the fans fetch raised — those 2
        # already-parsed bands must not be discarded just because the second,
        # independent fetch failed afterward.
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(not source.title.startswith("Bandcamp Fan:") for source in sources))

    def test_import_bandcamp_following_default_fetcher_rejects_non_bandcamp_host(self) -> None:
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
                import_bandcamp_following("https://metadata.example/skerbz", profile="test-user", out_dir=Path("exports/generated"))
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
