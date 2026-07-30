from pathlib import Path
from tempfile import TemporaryDirectory
import io
import os
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
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

    def test_configured_paths_can_override_a_parser_default(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            configured_sources = root / "custom-sources.json"
            configured_history = root / "custom-history.json"
            with patch.dict(
                os.environ,
                {
                    "RSS_SOURCES_FILE": str(configured_sources),
                    "RSS_HISTORY_FILE": str(configured_history),
                },
            ):
                self.assertEqual(
                    default_sources_path("me", prefer_configured=True),
                    configured_sources,
                )
                self.assertEqual(
                    default_subscription_history_path(
                        "me",
                        prefer_configured=True,
                    ),
                    configured_history,
                )
                self.assertEqual(
                    default_sources_path("explicit-profile").name,
                    "sources.explicit-profile.json",
                )

    def test_cli_distinguishes_default_and_explicit_profiles_for_path_precedence(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_path = root / "sources.json"
            history_path = root / "history.json"
            private_path = root / "private.json"
            data_path.write_text('{"schema_version": 1, "sources": []}\n', encoding="utf-8")
            history_path.write_text('{"schema_version": 1, "entries": []}\n', encoding="utf-8")
            private_path.write_text('{"schema_version": 1, "sources": []}\n', encoding="utf-8")

            cases = (
                (["list"], "me", True),
                (["list", "--profile=explicit"], "explicit", False),
            )
            for command, expected_profile, prefer_configured in cases:
                with self.subTest(command=command):
                    with (
                        patch.dict(os.environ, {"RSS_PROFILE": "me"}),
                        patch(
                            "netnewswire_feed_booster.cli.default_sources_path",
                            return_value=data_path,
                        ) as sources_path,
                        patch(
                            "netnewswire_feed_booster.cli.default_subscription_history_path",
                            return_value=history_path,
                        ) as history_path_resolver,
                        redirect_stdout(io.StringIO()),
                    ):
                        result = main(
                            [
                                "--private-data",
                                str(private_path),
                                *command,
                            ]
                        )

                    self.assertEqual(result, 0)
                    sources_path.assert_called_once_with(
                        expected_profile,
                        prefer_configured=prefer_configured,
                    )
                    history_path_resolver.assert_called_once_with(
                        expected_profile,
                        prefer_configured=prefer_configured,
                    )

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
