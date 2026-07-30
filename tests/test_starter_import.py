from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from netnewswire_feed_booster.starter_import import build_starter_import
from netnewswire_feed_booster.opml import parse_opml


BANDCAMP_HTML = """
<meta property="og:site_name" content="Fixture Label">
<ol id="music-grid" data-client-items="[{&quot;art_id&quot;:1463768112,&quot;artist&quot;:&quot;Fixture Label&quot;,&quot;page_url&quot;:&quot;/album/fixture-record&quot;,&quot;title&quot;:&quot;Fixture Record&quot;,&quot;type&quot;:&quot;album&quot;}]"></ol>
"""
DIRECT_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture</title></channel></rss>"""


class StarterImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self.manifest_path = self.project_root / "examples" / "starter-import.json"

    def test_builds_requested_sources_opml_report_and_generated_feeds(self) -> None:
        def fake_fetch(url: str, **_kwargs) -> str:
            return BANDCAMP_HTML if url.rstrip("/").endswith("/music") else DIRECT_RSS

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with patch("netnewswire_feed_booster.starter_import.fetch_text", side_effect=fake_fetch):
                result = build_starter_import(
                    repo_root=root,
                    manifest_path=self.manifest_path,
                    profile="starter",
                )

            registry = json.loads((root / "data" / "sources.starter.json").read_text(encoding="utf-8"))
            report = (root / "exports" / "starter-import-report.html").read_text(encoding="utf-8")
            parsed = parse_opml(root / "exports" / "starter-netnewswire.opml", profile="starter")
            generated = sorted((root / "exports" / "bandcamp").glob("*.rss"))

        self.assertEqual(result.source_count, 8)
        self.assertEqual(result.direct_count, 5)
        self.assertEqual(result.generated_count, 3)
        self.assertEqual(len(registry["sources"]), 8)
        self.assertEqual(len(parsed), 8)
        self.assertEqual(len(generated), 3)
        self.assertIn("Pitchfork Album Reviews", report)
        self.assertIn("Bandcamp: Dark Entries Records", report)
        self.assertIn("This is a build report, not a feed reader", report)
        self.assertIn("did not import or subscribe to anything", report)
        self.assertNotIn("file://", report)

    def test_refuses_to_replace_an_existing_profile_by_default(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            existing = root / "data" / "sources.starter.json"
            existing.parent.mkdir(parents=True)
            existing.write_text("keep me", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                build_starter_import(
                    repo_root=root,
                    manifest_path=self.manifest_path,
                    profile="starter",
                    validate_network=False,
                )

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")


if __name__ == "__main__":
    unittest.main()
