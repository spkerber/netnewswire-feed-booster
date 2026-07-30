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
from netnewswire_feed_booster.http_client import _RestrictedRedirectHandler, fetch_text
from netnewswire_feed_booster.hosted_bandcamp import sources_with_hosted_bandcamp_feeds


class GeneratedAdapterTests(unittest.TestCase):
    def test_bandcamp_adapter_rejects_non_bandcamp_hosts(self) -> None:
        source = Source(
            id="bad-bandcamp",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="https://metadata.example/",
            kind="bandcamp",
            source="bandcamp-local-generated",
        )

        self.assertIsNone(adapter_for_source(source))
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
        unsafe = Source(
            id="bad-bandcamp",
            title="Bad Bandcamp",
            feed_url="file:///tmp/bad.rss",
            site_url="https://metadata.example/",
            kind="bandcamp",
            source="bandcamp-local-generated",
        )

        rewritten = sources_with_hosted_bandcamp_feeds([unsafe], "https://example.modal.run", token="secret")

        self.assertEqual(rewritten[0].feed_url, "file:///tmp/bad.rss")

    def test_restricted_fetch_rejects_an_unapproved_host_before_network_io(self) -> None:
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
                fetch_text("https://metadata.example/feed", allowed_hosts={"api.mixcloud.com"})
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
