"""scripts/netnewswire_workflow.sh: the modes that need no host or network.

Each run points RSS_PRIVATE_ENV at a file that does not exist, so a real
data/private.env in the checkout is never loaded.
"""

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import isolated_env  # Clears private profile settings; keep above package imports.


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "netnewswire_workflow.sh"


class WorkflowScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sources = self.root / "sources.me.json"
        self.history = self.root / "subscription-history.me.json"
        self.sources.write_text(json.dumps({"schema_version": 1, "sources": []}), encoding="utf-8")
        self.history.write_text(json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8")

    def run_script(self, *arguments, **environment):
        env = {
            **os.environ,
            "RSS_PRIVATE_ENV": str(self.root / "missing.env"),
            "RSS_PROFILE": "me",
            "RSS_SOURCES_FILE": str(self.sources),
            "RSS_HISTORY_FILE": str(self.history),
            "NETNEWSWIRE_LOCAL_OPML_OUT": str(self.root / "me-netnewswire.opml"),
            **environment,
        }
        return subprocess.run(["bash", str(SCRIPT), *arguments], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)

    def test_help_lists_the_modes_for_each_setup(self) -> None:
        result = self.run_script("help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Mac only (no host needed):", result.stdout)
        self.assertIn("Hosted bridge:", result.stdout)
        for mode in ("export-local", "refresh-local", "status", "catch-up", "bridge-health", "export"):
            self.assertRegex(result.stdout, rf"(?m)^  {mode}\s")

    def test_an_unknown_mode_prints_help_and_fails(self) -> None:
        result = self.run_script("no-such-mode")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Mac only (no host needed):", result.stderr)

    def test_status_reports_local_feeds_without_a_bridge(self) -> None:
        result = self.run_script("status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Generated feeds on this Mac for profile me", result.stdout)
        self.assertNotIn("Hosted bridge health", result.stdout)

    def test_export_local_writes_an_opml_for_import(self) -> None:
        result = self.run_script("export-local")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<opml", (self.root / "me-netnewswire.opml").read_text(encoding="utf-8"))
        self.assertIn("File > Import Subscriptions", result.stdout)

    def test_catch_up_checks_it_can_publish_before_refreshing_anything(self) -> None:
        # With no bridge token there is nothing to publish to, so it must stop
        # at once rather than after an hour of Bandcamp requests.
        result = self.run_script("catch-up")

        self.assertEqual(result.returncode, 1)
        self.assertIn("RSS_FEED_TOKEN", result.stderr)
        self.assertNotIn("Updated local Bandcamp feeds", result.stdout)

    def test_local_modes_refuse_tracked_starter_data(self) -> None:
        for mode in ("status", "refresh-local", "export-local"):
            with self.subTest(mode=mode):
                result = self.run_script(mode, RSS_SOURCES_FILE="data/sources.json")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Refusing to use tracked starter data", result.stderr)


if __name__ == "__main__":
    unittest.main()
