"""The /inbound/email route as deployed, through FastAPI's test client.

The checks themselves are tested in test_email_feeds.py. This covers the glue in
modal_bandcamp_app.py, which only runs where modal and fastapi are installed
(the optional .venv-modal), so elsewhere it skips:

    .venv-modal/bin/python -m unittest discover -s tests
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.feed_store import EmailIntake, FeedStore, Source


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "email"
SECRET = "fixture-inbound-secret-0123456789"
ADDRESS = "inbox+weekly-notes-0123456789abcdef@feeds.example.com"
HAS_BRIDGE_DEPENDENCIES = all(importlib.util.find_spec(name) for name in ("modal", "fastapi", "httpx"))


class FakeVolume:
    def __init__(self) -> None:
        self.commits = 0

    def reload(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


@unittest.skipUnless(HAS_BRIDGE_DEPENDENCIES, "needs the optional modal extra")
class InboundEmailRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The app module resolves its deploy paths relative to the repo root.
        previous = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            import modal_bandcamp_app
        finally:
            os.chdir(previous)
        cls.app = modal_bandcamp_app

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = self.root / "sources.json"
        store = FeedStore(registry)
        store.add_or_update(
            Source(
                id="email-weekly-notes",
                title="Weekly Notes",
                feed_url="file:///tmp/email-weekly-notes.rss",
                kind="newsletter",
                profiles=[self.app.RSS_PROFILE],
                source="email-inbound",
                email=EmailIntake(ADDRESS, ["news.example.net"], ["news.example.net"]),
            )
        )
        store.save()
        self.volume = FakeVolume()
        cache = self.root / "cache"
        for name, value in {
            "DATA_PATH": registry,
            "CACHE_DIR": cache / "generated",
            "VALIDATOR_DIR": cache / "validators",
            "REFRESH_STATE_DIR": cache / "refresh-state",
            "EMAIL_ITEM_DIR": cache / "email",
            "SEED_DIR": self.root / "seed",
            "cache_volume": self.volume,
        }.items():
            patcher = patch.object(self.app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ, {"RSS_INBOUND_EMAIL_SECRET": SECRET, "RSS_FEED_TOKEN": "fixture-token"})
        environment.start()
        self.addCleanup(environment.stop)
        self.app._email_sources_by_address.cache_clear()
        self.addCleanup(self.app._email_sources_by_address.cache_clear)
        self.client = TestClient(self.app.web.get_raw_f()())

    def post(self, raw, **headers):
        sent = {"Authorization": f"Bearer {SECRET}", "X-Envelope-To": ADDRESS, "Content-Type": "message/rfc822"}
        sent.update(headers)
        return self.client.post("/inbound/email", content=raw, headers={key: value for key, value in sent.items() if value is not None})

    def stored(self):
        cache = self.root / "cache"
        return sorted(path.relative_to(cache).as_posix() for path in cache.rglob("*") if path.is_file()) if cache.exists() else []

    def test_rejections_store_nothing(self) -> None:
        newsletter = (FIXTURES / "newsletter.eml").read_bytes()
        cases = [
            (401, newsletter, {"Authorization": None}),
            (401, newsletter, {"Authorization": "Bearer wrong"}),
            (404, newsletter, {"X-Envelope-To": "inbox+nobody-0@feeds.example.com"}),
            (403, (FIXTURES / "spoofed-sender.eml").read_bytes(), {}),
        ]
        for status, raw, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.assertEqual(self.post(raw, **headers).status_code, status)
        with patch.object(self.app, "INBOUND_EMAIL_MAX_BYTES", 1024):
            self.assertEqual(self.post(newsletter).status_code, 413)

        self.assertEqual(self.stored(), [])
        self.assertEqual(self.volume.commits, 0)

    def test_an_accepted_message_is_served_and_a_redelivery_changes_nothing(self) -> None:
        newsletter = (FIXTURES / "newsletter.eml").read_bytes()

        accepted = self.post(newsletter)
        duplicate = self.post(newsletter)
        feed = self.client.get("/feeds/fixture-token/generated/email-weekly-notes.rss")

        self.assertEqual((accepted.status_code, accepted.json()), (202, {"status": "accepted"}))
        self.assertEqual((duplicate.status_code, duplicate.json()), (200, {"status": "duplicate"}))
        self.assertEqual(self.volume.commits, 1)
        self.assertEqual(len([name for name in self.stored() if name.startswith("email/")]), 1)
        self.assertEqual(feed.status_code, 200)
        self.assertEqual(feed.text.count("<item>"), 1)
        self.assertNotIn(ADDRESS, feed.text)
        health = self.client.get("/feeds/fixture-token/health").text
        self.assertRegex(health, r"email-weekly-notes\t\d{4}-\d{2}-\d{2}\n")

    def test_the_scheduled_refresh_never_fetches_an_email_source(self) -> None:
        with patch.object(self.app.time, "sleep"):
            result = self.app.refresh_generated_cache.get_raw_f()()

        self.assertEqual(result["attempted"], 0)


if __name__ == "__main__":
    unittest.main()
