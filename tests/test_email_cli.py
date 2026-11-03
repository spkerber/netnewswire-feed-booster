import io
import json
import os
import re
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.cli import main
from netnewswire_feed_booster.feed_store import FeedStore, Source
from netnewswire_feed_booster.generated_adapters import EMAIL_ADAPTER, adapter_for_source


BASE = "inbox@feeds.example.com"
ADDRESS = re.compile(r"inbox\+weekly-notes-[0-9a-f]{16}@feeds\.example\.com")


class EmailSourceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "sources.json"
        self.out_dir = self.root / "generated"

    def run_cli(self, *arguments, env=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        base = [
            "--data", str(self.data),
            "--private-data", str(self.root / "private-sources.json"),
            "--history", str(self.root / "history.json"),
        ]
        with patch.dict(os.environ, env or {}), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([*base, *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def subscribe(self, *extra, env=None):
        return self.run_cli(
            "subscribe-email-source",
            "--title", "Weekly Notes",
            "--sender-domain", "News.Example.net",
            "--out-dir", str(self.out_dir),
            *extra,
            env={"RSS_INBOUND_EMAIL_ADDRESS": BASE} if env is None else env,
        )

    def test_subscribing_generates_an_address_and_an_empty_seed_feed(self) -> None:
        code, stdout, stderr = self.subscribe("--image-host", "images.news.example.net", "--group", "Newsletters")

        self.assertEqual(code, 0)
        printed = ADDRESS.findall(stdout)
        self.assertEqual(len(printed), 1)
        source = FeedStore(self.data).source_by_id("email-weekly-notes")
        self.assertEqual(source.email.address, printed[0])
        self.assertEqual(source.email.sender_domains, ["news.example.net"])
        self.assertEqual(source.email.image_hosts, ["images.news.example.net"])
        self.assertEqual((source.kind, source.source, source.status, source.groups), ("newsletter", "email-inbound", "active", ["Newsletters"]))
        self.assertIs(adapter_for_source(source), EMAIL_ADAPTER)
        seed = self.out_dir / "email-weekly-notes.rss"
        self.assertEqual(source.feed_url, seed.resolve().as_uri())
        channel = ET.fromstring(seed.read_text(encoding="utf-8")).find("channel")
        self.assertEqual(channel.findtext("title"), "Weekly Notes")
        self.assertEqual(channel.findall("item"), [])
        self.assertNotIn(printed[0], seed.read_text(encoding="utf-8"))
        self.assertIn("Redeploy", stdout)
        self.assertEqual(stderr, "")

    def test_the_address_lives_only_in_the_email_record(self) -> None:
        _code, stdout, _stderr = self.subscribe()
        address = ADDRESS.findall(stdout)[0]

        record = json.loads(self.data.read_text(encoding="utf-8"))["sources"][0]

        self.assertEqual(record["email"]["address"], address)
        del record["email"]
        self.assertNotIn(address, json.dumps(record))

    def test_without_a_group_the_source_stays_at_the_root_and_says_so(self) -> None:
        _code, _stdout, stderr = self.subscribe()

        self.assertIn("RSS_DEFAULT_GROUP_EMAIL", stderr)
        self.assertEqual(FeedStore(self.data).source_by_id("email-weekly-notes").groups, [])

    def test_resubscribing_keeps_the_address_and_does_not_print_it(self) -> None:
        _code, first, _stderr = self.subscribe("--image-host", "news.example.net")
        address = ADDRESS.findall(first)[0]
        seed = self.out_dir / "email-weekly-notes.rss"
        seed_before = seed.read_text(encoding="utf-8")

        code, second, _stderr = self.subscribe("--sender-domain", "mail.example.com")

        self.assertEqual(code, 0)
        self.assertEqual(ADDRESS.findall(second), [])
        self.assertIn("Kept its existing address", second)
        source = FeedStore(self.data).source_by_id("email-weekly-notes")
        self.assertEqual(source.email.address, address)
        self.assertEqual(source.email.sender_domains, ["mail.example.com", "news.example.net"])
        self.assertEqual(source.email.image_hosts, ["news.example.net"])
        self.assertEqual(len(FeedStore(self.data).sources()), 1)
        self.assertEqual(seed.read_text(encoding="utf-8"), seed_before)

    def test_new_address_replaces_the_old_one(self) -> None:
        _code, first, _stderr = self.subscribe()

        _code, second, _stderr = self.subscribe("--new-address")

        self.assertNotEqual(ADDRESS.findall(first), ADDRESS.findall(second))
        self.assertEqual(FeedStore(self.data).source_by_id("email-weekly-notes").email.address, ADDRESS.findall(second)[0])
        self.assertIn("previous address stops working", second)

    def test_a_bare_mail_domain_gives_a_plain_per_source_address(self) -> None:
        _code, stdout, _stderr = self.subscribe("--address-base", "@feeds.example.com", env={})

        self.assertRegex(stdout, r"Address: weekly-notes-[0-9a-f]{16}@feeds\.example\.com")

    def test_an_apex_mail_domain_gets_a_warning(self) -> None:
        _code, _stdout, stderr = self.subscribe("--group", "", env={"RSS_INBOUND_EMAIL_ADDRESS": "inbox@example.com"})

        self.assertIn("subdomain", stderr)

    def test_a_missing_address_base_is_an_error(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.subscribe(env={})

        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.data.exists())

    def test_an_invalid_sender_domain_is_an_error(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_cli(
                "subscribe-email-source", "--title", "Weekly Notes", "--sender-domain", "192.0.2.1",
                "--out-dir", str(self.out_dir), env={"RSS_INBOUND_EMAIL_ADDRESS": BASE},
            )
        self.assertFalse(self.data.exists())

    def test_an_id_held_by_another_kind_of_source_is_left_alone(self) -> None:
        store = FeedStore(self.data)
        store.add_or_update(Source(id="email-weekly-notes", title="Something else", feed_url="https://other.example.com/feed.xml"))
        store.save()

        self.subscribe()

        sources = {source.id: source for source in FeedStore(self.data).sources()}
        self.assertEqual(sources["email-weekly-notes"].feed_url, "https://other.example.com/feed.xml")
        self.assertIsNone(sources["email-weekly-notes"].email)
        self.assertIs(adapter_for_source(sources["email-weekly-notes-2"]), EMAIL_ADAPTER)

    def test_exports_never_carry_the_address(self) -> None:
        _code, stdout, _stderr = self.subscribe()
        address = ADDRESS.findall(stdout)[0]
        local, hosted = self.root / "local.opml", self.root / "hosted.opml"

        self.run_cli("export-opml", "--out", str(local))
        self.run_cli(
            "export-opml", "--out", str(hosted),
            "--bandcamp-feed-base", "https://bridge.example.com", "--bandcamp-feed-token", "fixture-token",
        )

        for path in (local, hosted):
            self.assertNotIn(address, path.read_text(encoding="utf-8"))
            self.assertNotIn("feeds.example.com", path.read_text(encoding="utf-8"))
        outline = next(node for node in ET.parse(hosted).iter("outline") if node.get("title") == "Weekly Notes")
        self.assertEqual(outline.get("xmlUrl"), "https://bridge.example.com/feeds/fixture-token/generated/email-weekly-notes.rss")

    def test_list_shows_the_address_only_with_show_sensitive(self) -> None:
        _code, stdout, _stderr = self.subscribe()
        address = ADDRESS.findall(stdout)[0]

        _code, plain, _stderr = self.run_cli("list")
        _code, sensitive, _stderr = self.run_cli("list", "--show-sensitive")

        self.assertNotIn(address, plain)
        self.assertIn(address, sensitive)

    def test_local_refresh_never_fetches_an_email_source(self) -> None:
        self.subscribe()

        with patch("netnewswire_feed_booster.cli.fetch_text", side_effect=AssertionError("fetched")) as fetch:
            code, stdout, _stderr = self.run_cli("refresh-generated-local-feeds", "--pause-seconds", "0", "--out-dir", str(self.out_dir))

        self.assertEqual(code, 0)
        fetch.assert_not_called()
        self.assertIn("Updated local generated feeds: 0", stdout)

    def test_refresh_plan_counts_email_sources_apart(self) -> None:
        self.subscribe()

        code, stdout, _stderr = self.run_cli("refresh-plan")

        self.assertEqual(code, 0)
        self.assertIn("email: 1 sources; mail arrives at the bridge, so nothing to refresh", stdout)
        self.assertIn("generated: 0 sources", stdout)


if __name__ == "__main__":
    unittest.main()
