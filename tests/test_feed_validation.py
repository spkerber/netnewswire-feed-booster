import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.feed_validation import (
    audit_source,
    detect_feed_type,
    discover_alternate_feed_links,
    discover_feed_url,
    validate_feed_text,
)


class FeedValidationTests(unittest.TestCase):
    def test_detect_feed_type_supports_rss_atom_json_and_html(self) -> None:
        self.assertEqual(detect_feed_type('<rss version="2.0"><channel><title>OK</title></channel></rss>'), "rss")
        self.assertEqual(detect_feed_type('<feed xmlns="http://www.w3.org/2005/Atom"><title>OK</title></feed>'), "atom")
        self.assertEqual(detect_feed_type('{"version":"https://jsonfeed.org/version/1.1","items":[]}'), "json")
        self.assertEqual(detect_feed_type("<!doctype html><html></html>"), "html")

    def test_validate_feed_text_rejects_malformed_rss(self) -> None:
        with self.assertRaises(ValueError):
            validate_feed_text("<rss><channel><title>broken</title>")

    def test_discover_alternate_feed_links_resolves_relative_urls(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" type="application/rss+xml" href="/feed.xml">
          <link href="https://example.com/feed.json" rel="alternate" type="application/feed+json">
        </head></html>
        """

        self.assertEqual(
            discover_alternate_feed_links(html, "https://example.com/articles/1"),
            ["https://example.com/feed.xml", "https://example.com/feed.json"],
        )

    def test_discover_feed_url_returns_direct_feed_or_first_alternate(self) -> None:
        responses = {
            "https://example.com/feed.xml": '<rss version="2.0"><channel><title>OK</title></channel></rss>',
            "https://example.com": '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>',
        }

        self.assertEqual(discover_feed_url("https://example.com/feed.xml", fetcher=responses.__getitem__), "https://example.com/feed.xml")
        self.assertEqual(discover_feed_url("https://example.com", fetcher=responses.__getitem__), "https://example.com/feed.xml")

    def test_audit_source_reads_file_feeds_and_reports_site_discovery_on_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            rss_path = Path(tmp_dir) / "feed.rss"
            rss_path.write_text('<rss version="2.0"><channel><title>OK</title></channel></rss>', encoding="utf-8")

            ok = audit_source(Source(id="local", title="Local", feed_url=rss_path.resolve().as_uri()))
            broken = audit_source(
                Source(id="broken", title="Broken", feed_url="https://example.com/not-feed", site_url="https://example.com"),
                fetcher=lambda url: '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>',
            )

        self.assertEqual(ok.status, "ok")
        self.assertEqual(ok.feed_type, "rss")
        self.assertEqual(broken.status, "error")
        self.assertEqual(broken.discovered_url, "https://example.com/feed.xml")


if __name__ == "__main__":
    unittest.main()
