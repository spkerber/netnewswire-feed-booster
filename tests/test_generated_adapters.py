import unittest
from unittest.mock import patch

from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.generated_adapters import (
    BANDCAMP_ADAPTER,
    HYDEFM_ADAPTER,
    MIXCLOUD_ADAPTER,
    NTS_ADAPTER,
    WEBPAGE_ADAPTER,
    adapter_for_source,
    configured_bandcamp_redirect_hosts,
)
from netnewswire_feed_booster.http_client import _RestrictedRedirectHandler, fetch_json, fetch_text
from netnewswire_feed_booster.hosted_bandcamp import sources_with_hosted_generated_feeds


class GeneratedAdapterTests(unittest.TestCase):
    def test_bandcamp_source_allowed_hosts_validates_independently_of_adapter_validate(self) -> None:
        # refresh-bandcamp-local-feeds calls allowed_hosts_for(source) directly,
        # never adapter.validate()/_bandcamp_matches — and a kind="bandcamp" Source
        # can also reach the registry via the generic `add` command with no
        # Bandcamp-specific vetting at all. The trust function itself must refuse
        # an implausible host rather than assuming it was already checked.
        ip_literal = Source(
            id="bad-bandcamp-ip",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="https://192.0.2.10/",
            kind="bandcamp",
            source="manual",
        )
        malformed = Source(
            id="bad-bandcamp-malformed",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="not-a-url",
            kind="bandcamp",
            source="manual",
        )
        for source in (ip_literal, malformed):
            allowed = BANDCAMP_ADAPTER.allowed_hosts_for(source)
            self.assertEqual(allowed, frozenset({"bandcamp.com"}) | configured_bandcamp_redirect_hosts(), source.id)

    def test_bandcamp_adapter_accepts_a_custom_domain_storefront(self) -> None:
        # Bandcamp's own following-list API reports these directly for a specific
        # followed artist (url_hints.custom_domain) — see bandcamp_following_band_source.
        source = Source(
            id="bandcamp-agogo-records",
            title="Bandcamp: Agogo Records",
            feed_url="file:///tmp/bandcamp-agogo-records.rss",
            site_url="https://shop.agogo-records.com",
            kind="bandcamp",
            source="bandcamp-following-import",
        )

        self.assertIs(adapter_for_source(source), BANDCAMP_ADAPTER)
        BANDCAMP_ADAPTER.validate(source)  # does not raise
        self.assertIn("shop.agogo-records.com", BANDCAMP_ADAPTER.allowed_hosts_for(source))
        # A different, unrelated host must still be rejected — accepting a custom
        # domain is scoped to that source's own site_url, not a blanket allow-all.
        self.assertNotIn("some-other-storefront.example", BANDCAMP_ADAPTER.allowed_hosts_for(source))

    def test_bandcamp_adapter_rejects_structurally_invalid_hosts(self) -> None:
        credentials = Source(
            id="bad-bandcamp-credentials",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="https://user:pass@fixture-artist.bandcamp.com/",
            kind="bandcamp",
        )
        ip_host = Source(
            id="bad-bandcamp-ip",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="https://192.0.2.10/",
            kind="bandcamp",
        )
        no_host = Source(
            id="bad-bandcamp-no-host",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="not-a-url",
            kind="bandcamp",
        )

        for source in (credentials, ip_host, no_host):
            self.assertIsNone(adapter_for_source(source), source.id)
            with self.assertRaises(ValueError):
                BANDCAMP_ADAPTER.validate(source)

    def test_nts_and_mixcloud_require_supported_public_url_shapes(self) -> None:
        nts = Source(
            id="nts-example",
            title="NTS: Example",
            feed_url="file:///tmp/nts.rss",
            site_url="https://www.nts.live/shows/example",
            source="nts-local-generated",
        )
        mixcloud = Source(
            id="mixcloud-example",
            title="Mixcloud: example",
            feed_url="file:///tmp/mixcloud.rss",
            site_url="https://www.mixcloud.com/example/episodes/not-a-profile/",
            source="mixcloud-local-generated",
        )

        self.assertIsNotNone(adapter_for_source(nts))
        self.assertIsNone(adapter_for_source(mixcloud))
        NTS_ADAPTER.validate(nts)
        with self.assertRaises(ValueError):
            MIXCLOUD_ADAPTER.validate(mixcloud)

    def test_webpage_adapter_uses_registered_recipes_and_keeps_legacy_sources_working(self) -> None:
        current = Source(
            id="webpage-hydefm",
            title="HydeFM Archives",
            feed_url="file:///tmp/hydefm.rss",
            site_url="https://hydefm.com/archives/",
            source="webpage-local-generated",
        )
        legacy = Source(
            id="radio-hydefm-archives",
            title="HydeFM Archives",
            feed_url="file:///tmp/hydefm.rss",
            site_url="https://hydefm.com/archives/",
            source="radio-local-generated",
        )
        unsupported = Source(
            id="arbitrary-webpage",
            title="Arbitrary webpage",
            feed_url="file:///tmp/arbitrary.rss",
            site_url="https://metadata.example/archive/",
            source="webpage-local-generated",
        )

        self.assertIs(adapter_for_source(current), WEBPAGE_ADAPTER)
        self.assertIs(adapter_for_source(legacy), WEBPAGE_ADAPTER)
        self.assertIsNone(adapter_for_source(unsupported))
        self.assertEqual(
            WEBPAGE_ADAPTER.upstream_url(current),
            "https://hydefm.com/archives/",
        )
        self.assertEqual(
            WEBPAGE_ADAPTER.allowed_hosts_for(current),
            frozenset({"hydefm.com", "www.hydefm.com"}),
        )
        HYDEFM_ADAPTER.validate(legacy)
        self.assertIsNot(HYDEFM_ADAPTER, WEBPAGE_ADAPTER)
        self.assertEqual(
            HYDEFM_ADAPTER.allowed_hosts,
            frozenset({"hydefm.com", "www.hydefm.com"}),
        )
        self.assertEqual(
            HYDEFM_ADAPTER.upstream_url(legacy),
            "https://hydefm.com/archives/",
        )

    def test_hosted_export_rewrites_only_recognized_generated_sources(self) -> None:
        unrecognized = Source(
            id="not-generated",
            title="Direct Podcast",
            feed_url="https://publisher.example/feed.xml",
            site_url="https://publisher.example/",
            kind="podcast",
        )
        custom_domain_bandcamp = Source(
            id="bandcamp-agogo-records",
            title="Bandcamp: Agogo Records",
            feed_url="file:///tmp/bandcamp-agogo-records.rss",
            site_url="https://shop.agogo-records.com",
            kind="bandcamp",
            source="bandcamp-following-import",
        )

        rewritten = sources_with_hosted_generated_feeds(
            [unrecognized, custom_domain_bandcamp], "https://example.modal.run", token="secret"
        )

        self.assertEqual(rewritten[0].feed_url, "https://publisher.example/feed.xml")
        self.assertEqual(rewritten[1].feed_url, "https://example.modal.run/feeds/secret/generated/bandcamp-agogo-records.rss")

    def test_restricted_fetch_rejects_an_unapproved_host_before_network_io(self) -> None:
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
                fetch_text("https://metadata.example/feed", allowed_hosts={"api.mixcloud.com"})
        urlopen.assert_not_called()

    def test_fetch_json_rejects_an_unapproved_host_before_network_io(self) -> None:
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
                fetch_json("https://metadata.example/api", allowed_hosts={"api-v2.soundcloud.com"})
        urlopen.assert_not_called()

    def test_restricted_fetch_rejects_redirects_outside_the_provider_allowlist(self) -> None:
        handler = _RestrictedRedirectHandler({"api.mixcloud.com"}, set())

        with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
            handler.redirect_request(None, None, 302, "Found", None, "https://metadata.example/feed")

    def test_bandcamp_custom_domain_redirect_allowlist_is_exact(self) -> None:
        self.assertEqual(
            configured_bandcamp_redirect_hosts("Label.Example, shop.label.example."),
            frozenset({"label.example", "shop.label.example"}),
        )
        with self.assertRaisesRegex(ValueError, "hostnames, not IP addresses"):
            configured_bandcamp_redirect_hosts("127.0.0.1")
        with self.assertRaisesRegex(ValueError, "Invalid"):
            configured_bandcamp_redirect_hosts("*.example.com")


if __name__ == "__main__":
    unittest.main()
