from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from netnewswire_feed_booster.subscription_history import SubscriptionHistoryStore
from netnewswire_feed_booster.feed_store import FeedStore, Source


class SubscriptionHistoryTests(unittest.TestCase):
    def test_records_rss_unsubscribe_once_per_source_and_profile(self) -> None:
        source = Source(
            id="fixture-letter",
            title="Fixture Letter",
            feed_url="https://fixture-letter.example/feed",
            site_url="https://fixture-letter.example",
            kind="substack",
        )

        with TemporaryDirectory() as tmp_dir:
            history_store = SubscriptionHistoryStore(Path(tmp_dir) / "subscription-history.json")
            first = history_store.record_rss_unsubscribe(source, profile="test-user", reason="Too noisy")
            second = history_store.record_rss_unsubscribe(source, profile="test-user", reason="Still too noisy")
            history_store.save()

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(history_store.entries()), 1)
        self.assertEqual(history_store.entries()[0].reason, "Still too noisy")

    def test_external_unfollow_candidates_include_substack_and_youtube(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = FeedStore(Path(tmp_dir) / "sources.json")
            history_store = SubscriptionHistoryStore(Path(tmp_dir) / "subscription-history.json")
            store.add_or_update(
                Source(
                    id="video-source",
                    title="Video Source",
                    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC123",
                    kind="youtube",
                )
            )
            store.add_or_update(
                Source(
                    id="plain-website",
                    title="Plain Website",
                    feed_url="https://example.com/feed",
                    kind="website",
                )
            )
            for source in store.sources():
                history_store.record_rss_unsubscribe(source, profile="test-user")

            candidates = history_store.external_unfollow_candidates(profile="test-user")

        self.assertEqual([candidate.source_id for candidate in candidates], ["video-source"])


if __name__ == "__main__":
    unittest.main()
