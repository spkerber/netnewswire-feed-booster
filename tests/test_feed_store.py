import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from netnewswire_feed_booster.feed_store import FeedStore, Source


class FeedStoreSaveTests(unittest.TestCase):
    def test_save_is_atomic_and_leaves_no_temp_file_behind(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(Source(id="fixture", title="Fixture", feed_url="https://example.com/feed"))
            store.save()

            leftover_temp_files = list(Path(tmp_dir).glob("*.tmp-*"))
            saved = json.loads(data_path.read_text(encoding="utf-8"))

        self.assertEqual(leftover_temp_files, [])
        self.assertEqual(saved["sources"][0]["id"], "fixture")

    def test_a_failed_write_does_not_corrupt_the_existing_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(Source(id="original", title="Original", feed_url="https://example.com/feed"))
            store.save()
            original_bytes = data_path.read_bytes()

            store.add_or_update(Source(id="second", title="Second", feed_url="https://example.com/feed2"))
            # Simulate the temp file write itself failing partway (disk full, killed
            # mid-write, etc.) — os.replace() is never reached, so the original file
            # must be completely untouched, not truncated or partially overwritten.
            from unittest.mock import patch

            with patch("pathlib.Path.write_text", side_effect=OSError("simulated disk failure")):
                with self.assertRaises(OSError):
                    store.save()

            survived_bytes = data_path.read_bytes()

        self.assertEqual(survived_bytes, original_bytes)


if __name__ == "__main__":
    unittest.main()
