import io
import unittest
from contextlib import redirect_stdout

from netnewswire_feed_booster.feed_store import Source
from netnewswire_feed_booster.source_collections import print_drift_report


class SensitiveOutputTests(unittest.TestCase):
    def test_drift_reports_redact_tokenized_feed_urls_by_default(self) -> None:
        source = Source(
            id="private-feed",
            title="Private Feed",
            feed_url="https://example.modal.run/feeds/secret-token/generated/private-feed.rss",
        )
        drift = {"missing": [source], "unexpected": [], "stale_file_bandcamp": [], "tokenless_modal": [], "unsubscribed": []}

        redacted = io.StringIO()
        with redirect_stdout(redacted):
            print_drift_report(drift)
        revealed = io.StringIO()
        with redirect_stdout(revealed):
            print_drift_report(drift, show_sensitive=True)

        self.assertNotIn("secret-token", redacted.getvalue())
        self.assertIn("[redacted; use --show-sensitive]", redacted.getvalue())
        self.assertIn("secret-token", revealed.getvalue())


if __name__ == "__main__":
    unittest.main()
