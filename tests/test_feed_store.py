import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import isolated_env  # Clears private profile settings; keep above package imports.
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


def bandcamp_source(site_url: str = "https://music.example.com", band_id=None, **overrides) -> Source:
    fields = {
        "id": "bandcamp-fixture-storefront",
        "title": "Bandcamp: Fixture Storefront",
        "feed_url": "file:///generated/bandcamp-fixture-storefront.rss",
        "site_url": site_url,
        "kind": "bandcamp",
        "profiles": ["test-user"],
        "groups": ["Bandcamp"],
        "bandcamp_band_id": band_id,
    }
    fields.update(overrides)
    return Source(**fields)


class BandcampBandIdTests(unittest.TestCase):
    def test_a_band_id_survives_a_save_and_is_left_out_when_unknown(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_path = Path(tmp_dir) / "sources.json"
            store = FeedStore(data_path)
            store.add_or_update(bandcamp_source(band_id=4242))
            store.add_or_update(bandcamp_source(site_url="https://other.bandcamp.com", id="bandcamp-other", title="Bandcamp: Other", feed_url="file:///other.rss"))
            store.save()
            saved = {record["id"]: record for record in json.loads(data_path.read_text(encoding="utf-8"))["sources"]}
            reloaded = FeedStore(data_path).source_by_id("bandcamp-fixture-storefront")

        self.assertEqual(saved["bandcamp-fixture-storefront"]["bandcamp_band_id"], 4242)
        self.assertNotIn("bandcamp_band_id", saved["bandcamp-other"])
        self.assertEqual(reloaded.bandcamp_band_id, 4242)

    def test_a_malformed_stored_band_id_reads_as_unknown(self) -> None:
        for value in ("abc", "-3", 0, -3, True, 1.5):
            record = bandcamp_source().to_dict()
            record["bandcamp_band_id"] = value
            self.assertIsNone(Source.from_dict(record).bandcamp_band_id, value)
        record["bandcamp_band_id"] = "4242"
        self.assertEqual(Source.from_dict(record).bandcamp_band_id, 4242)

    def test_a_merge_without_a_band_id_keeps_the_stored_one(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = FeedStore(Path(tmp_dir) / "sources.json")
            store.add_or_update(bandcamp_source(band_id=4242))
            store.add_or_update(bandcamp_source(band_id=None, notes="Re-subscribed"))
            store.add_or_update_many([bandcamp_source(band_id=None, notes="Re-imported")])
            merged = store.source_by_id("bandcamp-fixture-storefront")

        self.assertEqual(merged.bandcamp_band_id, 4242)
        self.assertEqual(merged.notes, "Re-imported")

    def test_recording_band_ids_matches_the_storefront_never_just_the_id(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = FeedStore(Path(tmp_dir) / "sources.json")
            store.add_or_update(bandcamp_source())
            store.add_or_update(
                bandcamp_source(site_url="https://namesake.bandcamp.com", id="bandcamp-namesake", title="Bandcamp: Namesake", feed_url="file:///namesake.rss")
            )
            recorded = store.record_bandcamp_band_ids(
                [
                    # Same id as the registered storefront, but a different artist's store.
                    bandcamp_source(site_url="https://someone-else.bandcamp.com", band_id=1),
                    bandcamp_source(site_url="https://music.example.com/", band_id=4242, title="Bandcamp: Upstream Title", groups=["Elsewhere"]),
                    bandcamp_source(site_url="https://unregistered.bandcamp.com", id="bandcamp-unregistered", band_id=3),
                ]
            )
            storefront = store.source_by_id("bandcamp-fixture-storefront")
            namesake = store.source_by_id("bandcamp-namesake")
            count = len(store.sources())

        self.assertEqual(recorded, 1)
        self.assertEqual(storefront.bandcamp_band_id, 4242)
        self.assertEqual(storefront.title, "Bandcamp: Fixture Storefront")
        self.assertEqual(storefront.groups, ["Bandcamp"])
        self.assertIsNone(namesake.bandcamp_band_id)
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
