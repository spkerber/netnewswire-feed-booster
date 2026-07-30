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
      <outline text="Fixture Letter" title="Fixture Letter" type="rss" xmlUrl="https://test-fixture.substack.com/feed" htmlUrl="https://test-fixture.substack.com"/>
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

    def test_parse_opml_keeps_multiple_non_ascii_titles(self) -> None:
        opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><body>
  <outline text="\u03a9\u03bc\u03ad\u03b3\u03b1 \u03a3\u03ae\u03bc\u03b1" xmlUrl="https://example.com/one.xml"/>
  <outline text="\u0e2a\u0e31\u0e0d\u0e0d\u0e32\u0e13" xmlUrl="https://example.com/two.xml"/>
</body></opml>
"""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "feeds.opml"
            path.write_text(opml, encoding="utf-8")
            sources = parse_opml(path, profile="test-user")

        self.assertEqual(len(sources), 2)
        self.assertEqual(len({source.id for source in sources}), 2)

    def test_render_opml_preserves_nested_folder_paths(self) -> None:
        sources = [
            Source(
                id="nyt-world",
                title="NYT World",
                feed_url="https://example.com/world.xml",
                groups=["News", "New York Times"],
            ),
            Source(
                id="nyt-movies",
                title="NYT Movies",
                feed_url="https://example.com/movies.xml",
                groups=["News", "New York Times"],
            ),
            Source(
                id="al-jazeera",
                title="Al Jazeera",
                feed_url="https://example.com/aljazeera.xml",
                groups=["News", "Al Jazeera"],
            ),
        ]

        rendered = render_opml(sources)

        self.assertIn('<outline text="News" title="News">', rendered)
        self.assertIn('<outline text="New York Times" title="New York Times">', rendered)
        self.assertLess(rendered.index('text="Al Jazeera" title="Al Jazeera"'), rendered.index('text="New York Times" title="New York Times"'))
        self.assertEqual(
            {source.id: source.groups for source in self._parse_rendered(rendered)},
            {
                "nyt-world": ["News", "New York Times"],
                "nyt-movies": ["News", "New York Times"],
                "al-jazeera": ["News", "Al Jazeera"],
            },
        )

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

    def test_store_preserves_user_defined_folder_paths_on_save(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="bandcamp-fixture-artist",
                    title="Bandcamp: Fixture Artist",
                    feed_url="file:///tmp/bandcamp-fixture-artist.rss",
                    kind="bandcamp",
                    groups=["Music", "Bandcamp Artists"],
                )
            )
            store.add_or_update(
                Source(
                    id="fixture-video",
                    title="Fixture Video",
                    feed_url="https://fixture-video.example/feed",
                    groups=["Video"],
                )
            )
            store.add_or_update(
                Source(
                    id="fixture-movies",
                    title="Fixture Movies",
                    feed_url="https://www.nytimes.com/svc/collections/v1/publish/section/movies/rss.xml",
                    groups=["News", "New York Times"],
                )
            )
            store.save()

            updated = FeedStore(data_path)

        self.assertEqual(updated.source_by_id("bandcamp-fixture-artist").groups, ["Music", "Bandcamp Artists"])
        self.assertEqual(updated.source_by_id("fixture-video").groups, ["Video"])
        self.assertEqual(updated.source_by_id("fixture-movies").groups, ["News", "New York Times"])

    def test_store_updates_a_source_folder_path_instead_of_appending_it(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="example",
                    title="Example",
                    feed_url="https://example.com/feed.xml",
                    groups=["Old Folder"],
                )
            )
            store.add_or_update(
                Source(
                    id="example",
                    title="Example",
                    feed_url="https://example.com/feed.xml",
                    groups=["New Folder", "Leaf"],
                )
            )

            source = store.source_by_id("example")

        self.assertIsNotNone(source)
        self.assertEqual(source.groups, ["New Folder", "Leaf"])

    def test_store_updates_account_source_with_same_canonical_site_url(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(
                Source(
                    id="old-label-name",
                    title="Old Label Name",
                    feed_url="file:///tmp/old-label-name.rss",
                    site_url="https://label.bandcamp.com/",
                    kind="bandcamp",
                )
            )
            store.add_or_update(
                Source(
                    id="new-label-name",
                    title="New Label Name",
                    feed_url="file:///tmp/new-label-name.rss",
                    site_url="https://LABEL.bandcamp.com",
                    kind="bandcamp",
                )
            )

            sources = store.sources()

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "New Label Name")

    @staticmethod
    def _parse_rendered(rendered: str) -> list[Source]:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "rendered.opml"
            path.write_text(rendered, encoding="utf-8")
            return parse_opml(path, profile="test-user")


if __name__ == "__main__":
    unittest.main()
