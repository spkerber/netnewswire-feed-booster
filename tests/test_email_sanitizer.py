import base64
import unittest
import xml.etree.ElementTree as ET

import isolated_env  # Clears private profile settings; keep above package imports.
from netnewswire_feed_booster.email_sanitizer import (
    SanitizePolicy,
    html_to_text,
    list_unsubscribe_urls,
    plain_text_to_html,
    remove_recipients,
    sanitize_email_html,
)


ALIAS = "inbox+weekly-notes-0123456789abcdef@feeds.example.com"
FORWARDED = "reader@example.org"


def clean(markup, **policy):
    return sanitize_email_html(markup, SanitizePolicy(**policy))


class EmailSanitizerTests(unittest.TestCase):
    def test_drops_scripts_styles_forms_and_frames_with_everything_inside(self) -> None:
        markup = (
            "<head><title>head-title</title><style>p { color: red }</style></head>"
            "<p>kept</p><script>track('script-body')</script>"
            "<form action='https://news.example.net/poll'><p>form-text</p><input name='q'><button>form-button</button></form>"
            "<iframe src='https://news.example.net/embed'>iframe-text</iframe>"
            "<noscript><img src='https://tracker.example.com/p.gif'>noscript-text</noscript>"
            "<svg><text>svg-text</text></svg><p>after</p>"
        )

        cleaned = clean(markup)

        self.assertEqual(cleaned, "<p>kept</p><p>after</p>")

    def test_drops_event_handlers_styles_and_every_unlisted_attribute(self) -> None:
        cleaned = clean(
            '<table width="100%" bgcolor="#fff" background="https://tracker.example.com/bg.gif">'
            '<tr><td colspan="2" style="padding:0" onclick="x()" class="c">cell</td></tr></table>'
            '<p onmouseover="steal()" id="p1" data-x="1">text</p>'
        )

        self.assertEqual(cleaned, '<table><tr><td colspan="2">cell</td></tr></table><p>text</p>')

    def test_unknown_tags_are_dropped_but_their_text_stays(self) -> None:
        self.assertEqual(clean("<center><font color='red'>Hello</font> <o:p>world</o:p></center>"), "Hello world")

    def test_keeps_allowlisted_markup_well_formed(self) -> None:
        cleaned = clean("<h1>Title<p>Unclosed paragraph<ul><li>one<li>two</ul><b>bold<i>both</b> after</i><br><hr>")

        # Every tag is closed and nested, so the result parses as XML.
        ET.fromstring(f"<root>{cleaned}</root>")
        self.assertIn("<li>one", cleaned)
        self.assertIn("<br />", cleaned)

    def test_drops_links_named_in_list_unsubscribe(self) -> None:
        targets = list_unsubscribe_urls("<https://news.example.net/l/abc123>, <mailto:leave@news.example.net>")

        cleaned = clean('<p><a href="https://news.example.net/l/abc123">Leave</a></p>', unsubscribe_urls=targets)

        self.assertEqual(targets, frozenset({"https://news.example.net/l/abc123"}))
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("Leave", cleaned)

    def test_drops_links_whose_path_or_query_suggests_account_actions(self) -> None:
        paths = [
            "https://news.example.net/unsubscribe?id=1",
            "https://news.example.net/u/opt-out",
            "https://news.example.net/email/preferences",
            "https://news.example.net/manage/42",
            "https://news.example.net/account/delete",
            "https://news.example.net/settings",
            "https://news.example.net/profile?u=1",
            "https://news.example.net/subscription",
            "https://news.example.net/action?do=unsub&t=1",
        ]
        for href in paths:
            with self.subTest(href=href):
                self.assertEqual(clean(f'<p><a href="{href}">Go</a></p>'), "<p></p>")

    def test_drops_links_whose_text_or_surrounding_block_is_about_the_subscription(self) -> None:
        # Click-tracker URLs say nothing about where they lead, so the words around them decide.
        markups = [
            '<p><a href="https://click.example.net/c/1">Unsubscribe</a></p>',
            '<p><a href="https://click.example.net/c/2">Manage your email preferences</a></p>',
            '<p>To stop these emails, <a href="https://click.example.net/c/3">click here</a>.</p>',
            '<td>You can <a href="https://click.example.net/c/4">opt out</a> at any time</td>',
            '<p>Update your profile <a href="https://click.example.net/c/5">here</a></p>',
            '<p>Sign in to <a href="https://click.example.net/c/6">your account</a></p>',
        ]
        for markup in markups:
            with self.subTest(markup=markup):
                self.assertNotIn("click.example.net", clean(markup))

    def test_content_links_in_other_blocks_survive(self) -> None:
        cleaned = clean(
            '<p><a href="https://news.example.net/stories/42">Read the lead story</a></p>'
            '<p>Unsubscribe <a href="https://click.example.net/c/1">here</a></p>'
        )

        self.assertIn('<a href="https://news.example.net/stories/42">Read the lead story</a>', cleaned)
        self.assertNotIn("click.example.net", cleaned)

    def test_mailto_javascript_and_relative_links_lose_the_link_but_keep_text(self) -> None:
        cleaned = clean(
            '<p><a href="mailto:hello@news.example.net">Write to us</a> '
            '<a href="javascript:alert(1)">Script</a> <a href="/relative">Relative</a> '
            '<a href="https://user:pass@news.example.net/">Credentials</a></p>'
        )

        self.assertEqual(cleaned, "<p>Write to us Script Relative Credentials</p>")

    def test_removes_the_recipient_address_everywhere(self) -> None:
        encoded = base64.urlsafe_b64encode(ALIAS.encode()).decode().rstrip("=")
        markup = (
            f"<p>Sent to {ALIAS.upper()} and {FORWARDED}.</p>"
            f'<p><img src="https://cdn.example.net/a.jpg" alt="For {FORWARDED}" title="{ALIAS}"></p>'
            f'<p><a href="https://news.example.net/view?email={ALIAS.replace("+", "%2B").replace("@", "%40")}">View online</a></p>'
            f'<p><a href="https://news.example.net/view?email={FORWARDED}">View again</a></p>'
            f'<p><a href="https://news.example.net/view?e={encoded}">View encoded</a></p>'
            f'<p><a href="https://cdn.example.net/img?to={FORWARDED.replace("@", "%2540")}">Double encoded</a></p>'
        )

        cleaned = clean(markup, recipients=(ALIAS, FORWARDED), image_hosts=frozenset({"cdn.example.net"}))

        lowered = cleaned.lower()
        for form in (ALIAS, FORWARDED, encoded.lower(), "%40", "%2540"):
            self.assertNotIn(form.lower(), lowered)
        self.assertIn("View online", cleaned)
        self.assertNotIn("<a ", cleaned)
        self.assertIn('alt="For"', cleaned)

    def test_remove_recipients_matches_case_insensitively(self) -> None:
        self.assertEqual(remove_recipients(f"to {ALIAS.upper()} now", [ALIAS]), "to  now")
        self.assertEqual(remove_recipients("no address here", [ALIAS]), "no address here")

    def test_drops_tracking_pixels_and_remote_images_from_unlisted_hosts(self) -> None:
        hosts = frozenset({"news.example.net", "sendgrid.net"})
        dropped = [
            '<img src="https://images.news.example.net/a.gif" width="1" height="1">',
            '<img src="https://images.news.example.net/x.gif" height="0">',
            '<img src="https://images.news.example.net/open/42.gif">',
            '<img src="https://images.news.example.net/track/open.php?u=1">',
            # A known tracker host is refused even when a source allowlists it.
            '<img src="https://u1.ct.sendgrid.net/wf/x.gif">',
            '<img src="https://tracker.example.com/banner.png">',
            '<img src="http://images.news.example.net/insecure.jpg">',
            '<img src="cid:inline-attachment@news.example.net">',
            '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">',
        ]
        for markup in dropped:
            with self.subTest(markup=markup):
                self.assertEqual(clean(markup, image_hosts=hosts), "")

    def test_with_no_image_hosts_every_remote_image_is_dropped(self) -> None:
        self.assertEqual(clean('<p><img src="https://images.news.example.net/cover.jpg"></p>'), "<p></p>")

    def test_keeps_images_from_allowlisted_hosts_and_subdomains(self) -> None:
        cleaned = clean(
            '<img src="https://f4.bcbits.com/img/a1_10.jpg?utm_source=x" width="600" alt="Cover" onerror="x()">',
            image_hosts=frozenset({"bcbits.com"}),
        )

        self.assertEqual(cleaned, '<img src="https://f4.bcbits.com/img/a1_10.jpg" alt="Cover" width="600" />')

    def test_strips_tracking_parameters_and_leaves_other_urls_byte_for_byte(self) -> None:
        tracked = clean('<a href="https://news.example.net/s/1?utm_source=n&amp;mc_eid=abc&amp;_hsenc=x&amp;page=2">a</a>')
        untouched = clean('<a href="https://click.example.net/ls/click?upn=AbC%3D%3D&amp;x=1+2">b</a>')

        self.assertEqual(tracked, '<a href="https://news.example.net/s/1?page=2">a</a>')
        self.assertEqual(untouched, '<a href="https://click.example.net/ls/click?upn=AbC%3D%3D&amp;x=1+2">b</a>')

    def test_drops_content_hidden_from_the_reader(self) -> None:
        cleaned = clean(
            '<div style="display:none;max-height:0">Hidden preheader for reader</div>'
            '<span style="visibility: hidden">hidden span</span>'
            '<p style="mso-hide:all">hidden paragraph</p><p>Shown</p>'
        )

        self.assertEqual(cleaned, "<p>Shown</p>")

    def test_an_unclosed_hidden_paragraph_ends_at_the_next_block(self) -> None:
        self.assertEqual(clean('<p style="display:none">preheader<table><tr><td>body</td></tr></table>'), "<table><tr><td>body</td></tr></table>")

    def test_an_unclosed_head_ends_where_the_body_starts(self) -> None:
        self.assertEqual(clean("<html><head><title>t</title><body><p>body</p></body></html>"), "<p>body</p>")

    def test_comments_and_conditional_comments_are_dropped(self) -> None:
        self.assertEqual(clean("<!--[if mso]><p>outlook only</p><![endif]--><p>everyone</p><!-- note -->"), "<p>everyone</p>")

    def test_plain_text_becomes_escaped_paragraphs_with_links(self) -> None:
        markup = plain_text_to_html("Line one <b>\nLine two\n\nSee https://news.example.net/s/1.\n")

        self.assertEqual(
            markup,
            '<p>Line one &lt;b&gt;<br>Line two</p>\n'
            '<p>See <a href="https://news.example.net/s/1">https://news.example.net/s/1</a>.</p>',
        )
        self.assertIn('<a href="https://news.example.net/s/1">', clean(markup))

    def test_html_to_text_keeps_only_visible_text(self) -> None:
        self.assertEqual(html_to_text("<p>One</p><script>x</script><p>Two <b>three</b></p>"), "One\n\nTwo three")


if __name__ == "__main__":
    unittest.main()
