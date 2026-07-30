import unittest

from netnewswire_feed_booster.feed_identity import (
    canonical_url,
    identity_match_reason,
    parse_feed_identity,
)


class FeedIdentityTests(unittest.TestCase):
    def test_canonical_url_normalizes_tracking_and_trailing_slashes(self) -> None:
        self.assertEqual(
            canonical_url("HTTPS://Example.COM/posts/?utm_source=test&b=2&a=1"),
            "https://example.com/posts?a=1&b=2",
        )

    def test_rss_aliases_match_by_stable_item_id(self) -> None:
        old_feed = """
        <rss><channel>
          <title>Example Publication</title>
          <link>https://old.example.com/</link>
          <item><title>Shared post</title><guid>post-123</guid></item>
        </channel></rss>
        """
        new_feed = """
        <rss><channel>
          <title>Example Publication</title>
          <link>https://new.example.com/</link>
          <item><title>Shared post</title><guid>post-123</guid></item>
        </channel></rss>
        """

        reason = identity_match_reason(
            parse_feed_identity(old_feed, "https://old.example.com/feed"),
            parse_feed_identity(new_feed, "https://new.example.com/feed"),
        )

        self.assertEqual(reason, "overlapping stable item IDs")

    def test_atom_identity_reads_self_link_and_entry_id(self) -> None:
        identity = parse_feed_identity(
            """
            <feed xmlns="http://www.w3.org/2005/Atom">
              <title>Example Atom</title>
              <link rel="self" href="https://example.com/feed.xml"/>
              <link rel="alternate" href="https://example.com/"/>
              <entry><id>tag:example.com,2026:1</id></entry>
            </feed>
            """
        )

        self.assertEqual(identity.self_url, "https://example.com/feed.xml")
        self.assertEqual(identity.home_url, "https://example.com/")
        self.assertEqual(identity.item_ids, frozenset({"tag:example.com,2026:1"}))


if __name__ == "__main__":
    unittest.main()
