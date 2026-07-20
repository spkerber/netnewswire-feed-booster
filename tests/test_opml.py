from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from netnewswire_feed_booster.feed_store import FeedStore, Source
from netnewswire_feed_booster.opml import parse_opml, render_opml


SAMPLE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>NetNewsWire Export</title></head>
  <body>
    <outline text="AI">
      <outline text="One Useful Thing" title="One Useful Thing" type="rss" xmlUrl="https://oneusefulthing.substack.com/feed" htmlUrl="https://oneusefulthing.substack.com"/>
    </outline>
    <outline text="Google Developers" type="rss" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw" htmlUrl="https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"/>
  </body>
</opml>
"""


class OpmlTests(unittest.TestCase):
    def test_parse_opml_preserves_groups_and_kinds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "feeds.opml"
            path.write_text(SAMPLE_OPML, encoding="utf-8")

            sources = parse_opml(path, profile="test-user")

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].groups, ["AI"])
        self.assertEqual(sources[0].kind, "substack")
        self.assertEqual(sources[1].kind, "youtube")

    def test_parse_opml_tolerates_nested_folders_and_lowercase_feed_attrs(self) -> None:
        opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <body>
    <outline text="culture">
      <outline text="movies">
        <outline text="Example Feed" type="rss" xmlurl="https://example.com/feed.xml" htmlurl="https://example.com"/>
      </outline>
    </outline>
  </body>
</opml>
"""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "feeds.opml"
            path.write_text(opml, encoding="utf-8")

            sources = parse_opml(path, profile="test-user")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "Example Feed")
        self.assertEqual(sources[0].feed_url, "https://example.com/feed.xml")
        self.assertEqual(sources[0].site_url, "https://example.com")
        self.assertEqual(sources[0].groups, ["culture", "movies"])

    def test_store_exports_only_active_profile_sources(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="active-source",
                    title="Active Source",
                    feed_url="https://example.com/feed",
                    profiles=["test-user"],
                    status="active",
                )
            )
            store.add_or_update(
                Source(
                    id="paused-source",
                    title="Paused Source",
                    feed_url="https://paused.example.com/feed",
                    profiles=["test-user"],
                    status="paused",
                )
            )
            store.save()

            rendered = render_opml(store.active_sources("test-user"))

        self.assertIn("Active Source", rendered)
        self.assertNotIn("Paused Source", rendered)

    def test_store_normalizes_messy_groups_on_save(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="bandcamp-ghost-dubs",
                    title="Bandcamp: Ghost Dubs",
                    feed_url="file:///tmp/bandcamp-ghost-dubs.rss",
                    kind="bandcamp",
                    groups=["Bandcamp Artists"],
                )
            )
            store.add_or_update(
                Source(
                    id="split-infinitives",
                    title="Split Infinitives",
                    feed_url="https://splitinfinitives.com/feed",
                    groups=["video"],
                )
            )
            store.add_or_update(
                Source(
                    id="nyt-movies",
                    title="NYT > Movies",
                    feed_url="https://www.nytimes.com/svc/collections/v1/publish/section/movies/rss.xml",
                    groups=["video"],
                )
            )
            store.save()

            updated = FeedStore(data_path)

        self.assertEqual(updated.source_by_id("bandcamp-ghost-dubs").groups, ["Bandcamp"])
        self.assertEqual(updated.source_by_id("split-infinitives").groups, ["video"])
        self.assertEqual(updated.source_by_id("nyt-movies").groups, ["video"])


if __name__ == "__main__":
    unittest.main()
