import ast
import asyncio
import hashlib
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.email_feeds import (
    CLOUDFLARE_AUTHSERV_ID,
    EMAIL_SOURCE_LABEL,
    EmailItem,
    InboundRejected,
    accept_inbound_email,
    email_intake_problem,
    email_sources_by_address,
    handle_inbound_email,
    new_inbound_address,
    normalize_recipient,
    parse_address_base,
    parse_authentication_results,
    read_capped_body,
    read_message,
    render_email_feed_rss,
    sender_problem,
)
from netnewswire_feed_booster.feed_store import EmailIntake, Source


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "email"
SECRET = "fixture-inbound-secret-0123456789"
NOW = 1793664000.0  # 2026-11-03 00:00 UTC
WEEKLY_ADDRESS = "inbox+weekly-notes-0123456789abcdef@feeds.example.com"
LETTERS_ADDRESS = "inbox+letters-fedcba9876543210@feeds.example.com"
FORWARDED_FROM = "reader@example.org"


def email_source(source_id="email-weekly-notes", address=WEEKLY_ADDRESS, senders=("news.example.net",), images=("news.example.net",), status="active"):
    return Source(
        id=source_id,
        title="Weekly Notes",
        feed_url=f"file:///tmp/{source_id}.rss",
        site_url="https://news.example.net/",
        kind="newsletter",
        status=status,
        source=EMAIL_SOURCE_LABEL,
        email=EmailIntake(address=address, sender_domains=list(senders), image_hosts=list(images)),
    )


def fixture(name):
    return (FIXTURES / name).read_bytes()


def message(
    from_header="Weekly Notes <hello@news.example.net>",
    auth=("mx.cloudflare.net; dkim=pass header.d=news.example.net; spf=none; dmarc=none",),
    subject="Issue",
    message_id="<issue@news.example.net>",
    date="Mon, 02 Nov 2026 14:00:00 +0000",
    body="<p>Body</p>",
    extra=(),
):
    built = EmailMessage()
    for value in auth:
        built["Authentication-Results"] = value
    for name, value in extra:
        built[name] = value
    if from_header is not None:
        built["From"] = from_header
    built["To"] = WEEKLY_ADDRESS
    built["Subject"] = subject
    if date:
        built["Date"] = date
    if message_id:
        built["Message-ID"] = message_id
    built.set_content(body, subtype="html")
    return built.as_bytes()


def files_under(*roots):
    return sorted(path for root in roots if root.exists() for path in root.rglob("*") if path.is_file())


class InboundCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.store_root = root / "cache" / "email"
        self.generated_dir = root / "cache" / "generated"
        self.weekly = email_source()
        self.letters = email_source("email-letters", LETTERS_ADDRESS, ("letters.example.net",), ())
        self.by_address = email_sources_by_address([self.weekly, self.letters])

    def handle(self, raw, *, authorization=f"Bearer {SECRET}", recipient=WEEKLY_ADDRESS, content_length=None, max_items=50, max_bytes=2 * 1024 * 1024, expected_secret=SECRET, now=NOW):
        return handle_inbound_email(
            authorization=authorization,
            content_length=content_length,
            recipient=recipient,
            raw=raw,
            expected_secret=expected_secret,
            sources_by_address=self.by_address,
            store_root=self.store_root,
            generated_dir=self.generated_dir,
            now=now,
            max_items=max_items,
            max_bytes=max_bytes,
        )

    def assert_rejected_and_nothing_stored(self, status, **kwargs) -> None:
        raw = kwargs.pop("raw", fixture("newsletter.eml"))
        with self.assertRaises(InboundRejected) as caught:
            self.handle(raw, **kwargs)
        self.assertEqual(caught.exception.status, status)
        self.assertEqual(files_under(self.store_root, self.generated_dir), [])

    def served_feed(self, source_id="email-weekly-notes"):
        return (self.generated_dir / f"{source_id}.rss").read_text(encoding="utf-8")


class InboundRejectionTests(InboundCase):
    """Security requirement 2: every rejection, and nothing stored for any of them."""

    def test_missing_secret_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(401, authorization=None)

    def test_wrong_secret_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(401, authorization="Bearer not-the-secret")

    def test_secret_in_another_scheme_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(401, authorization=f"Basic {SECRET}")

    def test_non_ascii_secret_is_rejected_rather_than_crashing(self) -> None:
        self.assert_rejected_and_nothing_stored(401, authorization="Bearer sécret")

    def test_an_unconfigured_secret_rejects_everything(self) -> None:
        self.assert_rejected_and_nothing_stored(401, authorization="Bearer ", expected_secret="")
        self.assert_rejected_and_nothing_stored(401, authorization="Bearer anything", expected_secret="  ")

    def test_a_short_secret_counts_as_none(self) -> None:
        self.assert_rejected_and_nothing_stored(401, authorization="Bearer short-secret", expected_secret="short-secret")

    def test_unknown_address_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(404, recipient="inbox+weekly-notes-0000000000000000@feeds.example.com")

    def test_guessing_the_slug_without_the_random_part_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(404, recipient="inbox+weekly-notes@feeds.example.com")

    def test_the_same_detail_on_another_domain_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(404, recipient="inbox+weekly-notes-0123456789abcdef@example.com")

    def test_a_paused_source_no_longer_receives_mail(self) -> None:
        self.by_address = email_sources_by_address([email_source(status="paused")])
        self.assert_rejected_and_nothing_stored(404)

    def test_missing_or_malformed_recipient_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(400, recipient=None)
        self.assert_rejected_and_nothing_stored(400, recipient="not an address")

    def test_unknown_address_is_rejected_before_the_message_is_read(self) -> None:
        self.assert_rejected_and_nothing_stored(404, recipient="inbox+nobody-1@feeds.example.com", raw=b"\xff\xfe not mail")

    def test_sender_outside_the_source_domains_is_rejected(self) -> None:
        raw = message(from_header="Other <news@other.example.com>", auth=("mx.cloudflare.net; dkim=pass header.d=other.example.com",))
        self.assert_rejected_and_nothing_stored(403, raw=raw)

    def test_spoofed_sender_with_a_forged_later_header_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(403, raw=fixture("spoofed-sender.eml"))

    def test_mail_for_one_source_from_another_sources_sender_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(403, recipient=LETTERS_ADDRESS)

    def test_declared_oversized_message_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(413, content_length="5000", max_bytes=4096)

    def test_oversized_message_without_a_declared_size_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(413, raw=message(body="<p>" + "x" * 5000 + "</p>"), max_bytes=4096)

    def test_malformed_content_length_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(400, content_length="lots")

    def test_empty_body_is_rejected(self) -> None:
        self.assert_rejected_and_nothing_stored(400, raw=b"")

    def test_reading_a_body_stops_at_the_cap(self) -> None:
        chunks_read = []

        async def chunks():
            for _ in range(10):
                chunks_read.append(1)
                yield b"x" * 1000

        with self.assertRaises(InboundRejected) as caught:
            asyncio.run(read_capped_body(chunks(), 2500))
        self.assertEqual(caught.exception.status, 413)
        self.assertEqual(len(chunks_read), 3)

        async def small():
            yield b"ab"
            yield b"cd"

        self.assertEqual(asyncio.run(read_capped_body(small(), 10)), b"abcd")


class SenderAuthenticationTests(unittest.TestCase):
    def problem(self, raw, domains=("news.example.net",)):
        return sender_problem(read_message(raw), domains)

    def test_dkim_pass_for_a_sender_domain_is_accepted(self) -> None:
        self.assertEqual(self.problem(message()), "")

    def test_dkim_signed_by_a_subdomain_is_accepted(self) -> None:
        raw = message(auth=("mx.cloudflare.net; dkim=pass header.d=mail.news.example.net",))
        self.assertEqual(self.problem(raw), "")

    def test_dmarc_pass_for_the_from_domain_is_accepted(self) -> None:
        raw = message(auth=("mx.cloudflare.net; dkim=pass header.d=esp.example.com; dmarc=pass header.from=news.example.net",))
        self.assertEqual(self.problem(raw), "")

    def test_spf_pass_for_a_sender_domain_envelope_is_accepted(self) -> None:
        raw = message(auth=("mx.cloudflare.net; dkim=none; spf=pass smtp.mailfrom=bounce@mail.news.example.net",))
        self.assertEqual(self.problem(raw), "")

    def test_passes_that_vouch_only_for_other_domains_are_rejected(self) -> None:
        raw = message(
            auth=(
                "mx.cloudflare.net; dkim=pass header.d=esp.example.com; "
                "spf=pass smtp.mailfrom=bounce@esp.example.com; dmarc=pass header.from=other.example.com",
            )
        )
        self.assertNotEqual(self.problem(raw), "")

    def test_failing_results_are_rejected(self) -> None:
        raw = message(auth=("mx.cloudflare.net; dkim=fail header.d=news.example.net; spf=softfail smtp.mailfrom=news.example.net; dmarc=fail",))
        self.assertNotEqual(self.problem(raw), "")

    def test_no_results_from_the_mail_host_are_rejected(self) -> None:
        self.assertIn(CLOUDFLARE_AUTHSERV_ID, self.problem(message(auth=())))

    def test_results_from_any_other_authserv_id_are_ignored(self) -> None:
        raw = message(auth=("mx.example.com; dkim=pass header.d=news.example.net; dmarc=pass header.from=news.example.net",))
        self.assertIn(CLOUDFLARE_AUTHSERV_ID, self.problem(raw))

    def test_only_the_first_cloudflare_header_counts(self) -> None:
        raw = message(
            auth=(
                "mx.cloudflare.net; dkim=none; spf=pass smtp.mailfrom=attacker@attacker.example.com",
                "mx.cloudflare.net; dkim=pass header.d=news.example.net",
            )
        )
        self.assertNotEqual(self.problem(raw), "")

    def test_from_must_name_exactly_one_address(self) -> None:
        self.assertNotEqual(self.problem(message(from_header=None)), "")
        self.assertNotEqual(self.problem(message(from_header="a@news.example.net, b@news.example.net")), "")
        # EmailMessage refuses to build a second From header, so it is spliced into the bytes.
        two_froms = message().replace(b"From: ", b"From: second@news.example.net\nFrom: ", 1)
        self.assertNotEqual(self.problem(two_froms), "")

    def test_a_lookalike_domain_is_not_a_subdomain(self) -> None:
        raw = message(from_header="x@evilnews.example.net", auth=("mx.cloudflare.net; dkim=pass header.d=evilnews.example.net",))
        self.assertNotEqual(self.problem(raw), "")


class AuthenticationResultsParsingTests(unittest.TestCase):
    def test_parses_methods_properties_comments_and_versions(self) -> None:
        authserv_id, results = parse_authentication_results(
            'MX.Cloudflare.net 1; dkim=pass (2048-bit key; "quoted ; comment") header.d=news.example.net header.s=s1;'
            " spf = pass (mx.cloudflare.net: domain of bounce@news.example.net designates 192.0.2.1) smtp.mailfrom=bounce@news.example.net;"
            ' dmarc=pass header.from="news.example.net"; arc=none'
        )

        self.assertEqual(authserv_id, "mx.cloudflare.net")
        self.assertEqual([(result.method, result.result) for result in results], [("dkim", "pass"), ("spf", "pass"), ("dmarc", "pass"), ("arc", "none")])
        self.assertEqual(results[0].properties, {"header.d": "news.example.net", "header.s": "s1"})
        self.assertEqual(results[1].properties, {"smtp.mailfrom": "bounce@news.example.net"})
        self.assertEqual(results[2].properties, {"header.from": "news.example.net"})

    def test_no_result_header(self) -> None:
        self.assertEqual(parse_authentication_results("mx.cloudflare.net; none"), ("mx.cloudflare.net", []))


class AcceptedEmailTests(InboundCase):
    def test_a_newsletter_becomes_one_stored_item_and_a_served_feed(self) -> None:
        accepted = self.handle(fixture("newsletter.eml"))

        digest = hashlib.sha256(b"<fixture-weekly-42@news.example.net>").hexdigest()
        self.assertEqual((accepted.source_id, accepted.guid, accepted.duplicate), ("email-weekly-notes", digest, False))
        self.assertEqual(
            files_under(self.store_root, self.generated_dir),
            [self.store_root / "email-weekly-notes" / f"{digest}.json", self.generated_dir / "email-weekly-notes.rss"],
        )
        channel = ET.fromstring(self.served_feed()).find("channel")
        self.assertEqual(channel.findtext("title"), "Weekly Notes")
        items = channel.findall("item")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].findtext("title"), "Weekly Notes #42 for")
        self.assertEqual(items[0].findtext("guid"), digest)
        self.assertEqual(items[0].find("guid").get("isPermaLink"), "false")
        self.assertEqual(items[0].findtext("pubDate"), "Mon, 02 Nov 2026 14:00:00 +0000")
        description = items[0].findtext("description")
        self.assertIn('<a href="https://news.example.net/stories/42?ref=home">Read the lead story</a>', description)
        self.assertIn('<img src="https://images.news.example.net/covers/42.jpg"', description)

    def test_served_xml_carries_no_subscription_links_recipient_or_trackers(self) -> None:
        """Security requirement 3, against the fixture's every trap."""
        self.handle(fixture("newsletter.eml"))

        served = self.served_feed().lower()
        for forbidden in (
            WEEKLY_ADDRESS,
            FORWARDED_FROM,
            "inbox%2b",
            "aw5ib3grd2vla2x5",  # The base64 spelling of the address in the "view online" link.
            "/l/abc123",
            "click.news.example.net",
            "/settings",
            "/account/",
            "mailto:",
            "javascript:",
            "tracker.example.com",
            "/open/42.gif",
            "sendgrid",
            "utm_",
            "onclick",
            "onload",
            "style",
            "script-body-marker",
            "iframe-body-marker",
            "head title",
            "<form",
            "vote now",
            "this week in notes",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, served)

    def test_the_raw_message_is_never_written(self) -> None:
        """Security requirement 4: only the sanitized item and the rendered feed reach disk."""
        self.handle(fixture("newsletter.eml"))

        written = "\n".join(path.read_text(encoding="utf-8") for path in files_under(self.store_root, self.generated_dir))
        for raw_only in (
            "raw-header-marker-7f3a",
            "Authentication-Results",
            "Received:",
            "192.0.2.10",
            "fixture-weekly-42@news.example.net",
            "List-Unsubscribe",
            "plain-part-marker-5c1d",
            "fixture-boundary",
        ):
            with self.subTest(raw_only=raw_only):
                self.assertNotIn(raw_only, written)
        stored = next((self.store_root / "email-weekly-notes").glob("*.json"))
        self.assertEqual(set(EmailItem.from_json(stored.read_text(encoding="utf-8")).__dict__), {"guid", "title", "published", "received", "html"})

    def test_a_redelivered_message_is_a_duplicate_and_changes_nothing(self) -> None:
        self.handle(fixture("newsletter.eml"))
        before = {path: path.read_bytes() for path in files_under(self.store_root, self.generated_dir)}

        again = self.handle(fixture("newsletter.eml"), now=NOW + 60)

        self.assertTrue(again.duplicate)
        self.assertEqual({path: path.read_bytes() for path in files_under(self.store_root, self.generated_dir)}, before)

    def test_a_plain_text_message_is_decoded_linked_and_scrubbed(self) -> None:
        self.handle(fixture("plain-text.eml"), recipient=LETTERS_ADDRESS)

        item = ET.fromstring(self.served_feed("email-letters")).find("channel/item")
        self.assertEqual(item.findtext("title"), "Café letter no. 7")
        self.assertEqual(item.findtext("pubDate"), "Sun, 01 Nov 2026 17:30:00 +0000")
        description = item.findtext("description")
        self.assertIn("café letter", description)
        self.assertIn('<a href="https://letters.example.net/7?id=7">', description)
        self.assertNotIn(LETTERS_ADDRESS, description)
        self.assertNotIn("unsubscribe?", description)

    def test_the_feed_keeps_the_newest_items_and_deletes_older_ones(self) -> None:
        for day in (1, 3, 2):
            self.handle(message(message_id=f"<day-{day}@news.example.net>", subject=f"Day {day}", date=f"0{day} Nov 2026 08:00:00 +0000"), max_items=2)

        titles = [item.findtext("title") for item in ET.fromstring(self.served_feed()).iter("item")]
        self.assertEqual(titles, ["Day 3", "Day 2"])
        self.assertEqual(len(list((self.store_root / "email-weekly-notes").glob("*.json"))), 2)

    def test_a_missing_or_future_date_falls_back_to_the_time_received(self) -> None:
        self.handle(message(message_id="<undated@news.example.net>", date=None))
        self.handle(message(message_id="<future@news.example.net>", date="Fri, 01 Jan 2100 00:00:00 +0000"))

        dates = {item.findtext("pubDate") for item in ET.fromstring(self.served_feed()).iter("item")}
        self.assertEqual(dates, {"Tue, 03 Nov 2026 00:00:00 +0000"})

    def test_without_a_message_id_a_redelivery_is_still_a_duplicate(self) -> None:
        first = self.handle(message(message_id=None))
        second = self.handle(message(message_id=None))

        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)

    def test_an_item_too_large_to_keep_falls_back_to_its_text(self) -> None:
        raw = message(body="<p>" + "<b>word</b> " * 30000 + "</p>")

        self.handle(raw, max_bytes=4 * 1024 * 1024)

        description = ET.fromstring(self.served_feed()).find("channel/item").findtext("description")
        self.assertLessEqual(len(description), 210_000)
        self.assertNotIn("<b>", description)

    def test_characters_xml_cannot_carry_are_removed(self) -> None:
        self.handle(message(subject="Bell\x07 and tab\x0b", body="<p>null\x00 form\x0cfeed</p>"))
        self.handle(message(message_id="<next@news.example.net>", subject="Next"))

        titles = [item.findtext("title") for item in ET.fromstring(self.served_feed()).iter("item")]
        self.assertEqual(sorted(titles), ["Bell and tab", "Next"])

    def test_a_message_older_than_every_kept_item_is_not_stored(self) -> None:
        self.handle(message(message_id="<new@news.example.net>", date="02 Nov 2026 08:00:00 +0000"), max_items=1)

        accepted = self.handle(message(message_id="<old@news.example.net>", date="01 Jan 2020 08:00:00 +0000"), max_items=1)

        self.assertFalse(accepted.duplicate)
        self.assertEqual(len(list((self.store_root / "email-weekly-notes").glob("*.json"))), 1)
        self.assertEqual([item.findtext("title") for item in ET.fromstring(self.served_feed()).iter("item")], ["Issue"])

    def test_accept_rejects_an_oversized_body_on_its_own(self) -> None:
        with self.assertRaises(InboundRejected) as caught:
            accept_inbound_email(b"x" * 5000, WEEKLY_ADDRESS, self.weekly, self.store_root, self.generated_dir, NOW, 50, 4096)
        self.assertEqual(caught.exception.status, 413)

    def test_an_empty_feed_renders_as_a_valid_channel(self) -> None:
        channel = ET.fromstring(render_email_feed_rss(self.weekly, [], NOW)).find("channel")

        self.assertEqual(channel.findtext("title"), "Weekly Notes")
        self.assertEqual(channel.findtext("link"), "https://news.example.net/")
        self.assertEqual(channel.findall("item"), [])


class AddressTests(unittest.TestCase):
    def test_subaddresses_hang_off_the_routed_address(self) -> None:
        address = new_inbound_address("Inbox@Feeds.Example.com", "Weekly Notes!", token="0123456789abcdef")

        self.assertEqual(address, "inbox+weekly-notes-0123456789abcdef@feeds.example.com")
        self.assertEqual(normalize_recipient(address), address)

    def test_a_bare_domain_gives_one_address_per_source(self) -> None:
        self.assertEqual(new_inbound_address("@feeds.example.com", "Letters", token="ab12"), "letters-ab12@feeds.example.com")
        self.assertEqual(new_inbound_address("feeds.example.com", "Letters", token="ab12"), "letters-ab12@feeds.example.com")

    def test_each_address_gets_a_fresh_random_part(self) -> None:
        first = new_inbound_address("inbox@feeds.example.com", "Weekly")
        second = new_inbound_address("inbox@feeds.example.com", "Weekly")

        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^inbox\+weekly-[0-9a-f]{16}@feeds\.example\.com$")

    def test_long_titles_are_cut_to_fit_the_local_part_limit(self) -> None:
        address = new_inbound_address("inbox@feeds.example.com", "A very long newsletter title " * 5)

        self.assertLessEqual(len(address.partition("@")[0]), 64)
        self.assertRegex(address, r"^inbox\+a-very-long[a-z-]*-[0-9a-f]{16}@")

    def test_invalid_bases_are_refused(self) -> None:
        for base in ("inbox+x@feeds.example.com", "inbox@localhost", "inbox@192.0.2.1", "inbox@", "", "in box@feeds.example.com"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                parse_address_base(base)

    def test_recipients_are_normalized_or_refused(self) -> None:
        self.assertEqual(normalize_recipient(" <Inbox+X-1@Feeds.Example.com> "), "inbox+x-1@feeds.example.com")
        for value in ("", "no-at-sign", "a@b", "a@192.0.2.1", "a b@feeds.example.com", "x" * 300 + "@feeds.example.com"):
            with self.subTest(value=value):
                self.assertEqual(normalize_recipient(value), "")

    def test_intake_problems(self) -> None:
        self.assertEqual(email_intake_problem(EmailIntake(WEEKLY_ADDRESS, ["news.example.net"])), "")
        self.assertNotEqual(email_intake_problem(None), "")
        self.assertNotEqual(email_intake_problem(EmailIntake(WEEKLY_ADDRESS.upper(), ["news.example.net"])), "")
        self.assertNotEqual(email_intake_problem(EmailIntake(WEEKLY_ADDRESS, [])), "")
        self.assertNotEqual(email_intake_problem(EmailIntake(WEEKLY_ADDRESS, ["News.example.net"])), "")
        self.assertNotEqual(email_intake_problem(EmailIntake(WEEKLY_ADDRESS, ["news.example.net"], ["192.0.2.1"])), "")

    def test_an_address_claimed_by_two_sources_belongs_to_neither(self) -> None:
        sources = [email_source("email-a"), email_source("email-b"), email_source("email-c", LETTERS_ADDRESS)]

        self.assertEqual(sorted(email_sources_by_address(sources)), [LETTERS_ADDRESS])


class SecretAndPrivacyPlacementTests(unittest.TestCase):
    """Security requirements 1, 5, and 6, as far as the public repo can show them."""

    def test_the_bridge_image_bakes_in_no_secret(self) -> None:
        tree = ast.parse((REPO_ROOT / "modal_bandcamp_app.py").read_text(encoding="utf-8"))
        baked = [
            key.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "env"
            for argument in node.args
            if isinstance(argument, ast.Dict)
            for key in argument.keys
            if isinstance(key, ast.Constant)
        ]

        self.assertIn("RSS_INBOUND_EMAIL_MAX_BYTES", baked)
        self.assertFalse([key for key in baked if "SECRET" in key or "TOKEN" in key], baked)

    def test_the_example_settings_leave_the_inbound_secret_empty(self) -> None:
        # A placeholder value would be a published secret; empty keeps the route shut.
        example = (REPO_ROOT / "examples" / "private.env.example").read_text(encoding="utf-8")

        self.assertRegex(example, r"(?m)^RSS_INBOUND_EMAIL_SECRET=$")
        self.assertRegex(example, r"(?m)^RSS_INBOUND_EMAIL_ADDRESS=\S+@feeds\.example\.com$")

    def test_the_email_sources_guide_says_how_to_rotate_the_inbound_secret(self) -> None:
        guide = (REPO_ROOT / "docs" / "email-sources.md").read_text(encoding="utf-8")

        self.assertIn("## Rotating the Inbound Secret", guide)
        self.assertIn("RSS_INBOUND_EMAIL_SECRET", guide)

    def test_tracked_files_hold_no_real_email_address(self) -> None:
        try:
            listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        address = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
        reserved = re.compile(r"(?:^|\.)(?:example\.(?:com|net|org)|[a-z0-9-]+\.(?:example|invalid|test|localhost))$", re.IGNORECASE)
        found = []
        for name in filter(None, listed.decode("utf-8").split("\0")):
            try:
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in address.finditer(text):
                if re.search(r"://[^\s/@]*$", text[max(0, match.start() - 200) : match.start()]):
                    continue  # Credentials in a URL, which the tests use to prove they are refused.
                if not reserved.search(match.group(1)):
                    found.append(f"{name}: {match.group(0)}")

        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
