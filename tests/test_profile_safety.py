from pathlib import Path
from tempfile import TemporaryDirectory
import io
import os
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from netnewswire_feed_booster.cli import main
from netnewswire_feed_booster.feed_store import default_sources_path, validate_profile_id
from netnewswire_feed_booster.subscription_history import default_subscription_history_path


class ProfileSafetyTests(unittest.TestCase):
    def test_profile_paths_never_fall_back_to_tracked_starter_files(self) -> None:
        sources = default_sources_path("fresh-profile")
        history = default_subscription_history_path("fresh-profile")

        self.assertEqual(sources.name, "sources.fresh-profile.json")
        self.assertEqual(history.name, "subscription-history.fresh-profile.json")

    def test_invalid_profile_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_profile_id("../outside")

    def test_implicit_missing_profile_fails_before_writing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            missing_data = Path(tmp_dir) / "sources.missing.json"
            missing_history = Path(tmp_dir) / "subscription-history.missing.json"
            stderr = io.StringIO()
            with (
                patch("netnewswire_feed_booster.cli.default_sources_path", return_value=missing_data),
                patch("netnewswire_feed_booster.cli.default_subscription_history_path", return_value=missing_history),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                main(
                    [
                        "subscribe-substack",
                        "fixture-letter.example",
                        "--title",
                        "Fixture Letter",
                        "--profile",
                        "missing",
                    ]
                )

            self.assertFalse(missing_data.exists())
            self.assertIn("./scripts/bootstrap_profile.sh missing", stderr.getvalue())

    def test_bootstrap_preflights_all_targets_and_uses_private_permissions(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_profile.sh"
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            existing = data_dir / "subscription-history.safe.json"
            existing.write_text("keep me", encoding="utf-8")

            refused = subprocess.run(
                [str(script), "safe"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((data_dir / "sources.safe.json").exists())
            self.assertFalse((data_dir / "profiles.safe.json").exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")

            created = subprocess.run(
                [str(script), "fresh"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            mode = os.stat(data_dir / "sources.fresh.json").st_mode & 0o777
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
