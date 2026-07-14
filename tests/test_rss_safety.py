import unittest

from netnewswire_feed_booster.rss_safety import ensure_rss_channel, is_safe_source_id, parse_internet_date, safe_https_url, validate_source_id
from netnewswire_feed_booster.http_client import fetch_text


class RssSafetyTests(unittest.TestCase):
    def test_validate_source_id_allows_slug_ids_only(self) -> None:
        self.assertTrue(is_safe_source_id("nts-dark-entries-w-josh-cheon"))
        self.assertEqual(validate_source_id("radio-hydefm-archives"), "radio-hydefm-archives")

        for value in ["../secret", "bad/id", "Bad-ID", "-bad", "bad_", "a" * 129]:
            self.assertFalse(is_safe_source_id(value))
            with self.assertRaises(ValueError):
                validate_source_id(value)

    def test_safe_https_url_requires_https_and_allowed_hosts(self) -> None:
        self.assertEqual(
            safe_https_url("https://media.ntslive.co.uk/image.jpg", allowed_suffixes={"ntslive.co.uk"}),
            "https://media.ntslive.co.uk/image.jpg",
        )

        self.assertEqual(safe_https_url("javascript:alert(1)", allowed_suffixes={"ntslive.co.uk"}), "")
        self.assertEqual(safe_https_url("http://media.ntslive.co.uk/image.jpg", allowed_suffixes={"ntslive.co.uk"}), "")
        self.assertEqual(safe_https_url("https://ntslive.co.uk.evil.example/image.jpg", allowed_suffixes={"ntslive.co.uk"}), "")
        self.assertEqual(safe_https_url("https://user:pass@media.ntslive.co.uk/image.jpg", allowed_suffixes={"ntslive.co.uk"}), "")

    def test_ensure_rss_channel_rejects_corrupted_or_non_rss_content(self) -> None:
        rss = '<?xml version="1.0"?><rss version="2.0"><channel><title>OK</title></channel></rss>'

        self.assertEqual(ensure_rss_channel(rss), rss)
        for value in ["not xml", "<rss></rss>", "<feed><title>Atom</title></feed>"]:
            with self.assertRaises(ValueError):
                ensure_rss_channel(value)

    def test_parse_internet_date_supports_rfc822_and_iso8601(self) -> None:
        self.assertEqual(parse_internet_date("Tue, 13 Jul 2026 10:00:00 +0000").year, 2026)
        self.assertEqual(parse_internet_date("2026-07-13T10:00:00Z").year, 2026)
        with self.assertRaises(ValueError):
            parse_internet_date("not a date")

    def test_fetch_text_rejects_responses_over_byte_limit(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b"x" * 6

        with unittest.mock.patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(ValueError):
                fetch_text("https://example.com/feed.xml", max_bytes=5)


if __name__ == "__main__":
    unittest.main()
