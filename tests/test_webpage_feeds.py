import unittest

from netnewswire_feed_booster.rss_safety import limit_rss_items
from netnewswire_feed_booster.webpage_feeds import (
    ParsedWebpageFeed,
    WebpageFeedItem,
    WebpageFeedRecipe,
    parse_webpage_feed,
    recipe_fetch_url,
    render_webpage_feed_rss,
)
from netnewswire_feed_booster.webpage_recipes import (
    HYDEFM_ARCHIVE_RECIPE,
    MAILCHIMP_ENGINEERING_BLOG_RECIPE,
    OSGH_NEON_SHRINES_RECIPE,
    webpage_recipe_for_url,
)


HYDEFM_HTML = """
<div data-elementor-type="loop-item">
  <div class="archivePic">
    <img src="https://hydefmradio-archive.s3.us-west-1.amazonaws.com/wp-content/uploads/2026/07/02212135/20260703-030123.jpg">
  </div>
  <h2>July 2, 2026</h2>
  <h2><a href="https://hydefm.com/archive/stooped-w-pijeon-07-02-26/">stooped w/ pijeon</a></h2>
  <div class="genres">
    <a href="https://hydefm.com/genres/bass/">Bass</a>
    <a href="https://hydefm.com/genres/club/">Club</a>
    <a href="https://hydefm.com/genres/techno/">Techno</a>
  </div>
</div>
<div data-elementor-type="loop-item">
  <div class="archivePic">
    <img src="https://hydefmradio-archive.s3.us-west-1.amazonaws.com/wp-content/uploads/2026/07/01212123/20260702-030534.jpg">
  </div>
  <h2>July 1, 2026</h2>
  <h2><a href="https://hydefm.com/archive/fluxions-w-vertigo-07-01-26/">FLUXIONS w/ Vertigo</a></h2>
</div>
"""


# Shapes copied from the live Squarespace page (October 2026), trimmed to five
# episodes: pre-wrap song lists broken by runs of spaces instead of <br>, a
# season's Download buttons stacked under its last episode, and the site footer
# straight after the last episode.
PRE_WRAP_GAP = " " * 76
NEON_SHRINES_HTML = f"""
<div class="sqs-html-content"><h3>NEON SHRINES is the official radio show of the OSGH</h3>
<p>(clicking DOWNLOAD does not actually download the show)</p></div>
<div class="sqs-html-content"><h1>Season three of NEON SHRINES</h1></div>
<div class="sqs-html-content"><h1>*</h1></div>
<div class="sqs-html-content"><h1>Season three of NEON SHRINES * Season three of NEON SHRINES *</h1></div>
<div class="sqs-html-content" data-sqsp-text-block-content><h2 style="white-space:pre-wrap;"><span class="sqsrte-text-color--black">Episode 1: The Return of NEON SHRINES, again!</span></h2></div>
<div class="sqs-html-content"><p class="" style="white-space:pre-wrap;">July 24, 2026 4:00pm</p></div>
<div class="sqs-html-content"><p style="white-space:pre-wrap;">In this episode, Stella &amp; Jojo &amp; Mac discuss the institutionalization of the OSGH.</p>
<p class="sqsrte-small" style="white-space:pre-wrap;" data-rte-preserve-empty="true">Songs:          <br>Angra - Wuthering Heights{PRE_WRAP_GAP}John Sinclair -  Ain’t Nobody’s Business{PRE_WRAP_GAP}Kylie Minogue - Love at First Sight</p></div>
<div class="sqs-html-content"><h2><span>Episode 2: Time Pilgrims &amp; Camino Report (ft. Matthew)</span></h2></div>
<div class="sqs-html-content"><p>July 31, 2026 4:00pm</p><p>We discuss time pilgrims.</p><p style="white-space:pre-wrap;" data-rte-preserve-empty="true">Songs:</p><p style="white-space:pre-wrap;" data-rte-preserve-empty="true">Gorillaz -&nbsp; Dracula{PRE_WRAP_GAP}Yeah Yeah Yeahs- Gold Lion{PRE_WRAP_GAP}&nbsp;{PRE_WRAP_GAP}Gang of Four  - Damaged Goods{PRE_WRAP_GAP}</p></div>
<div class="sqs-block-button"><a href="https://drive.google.com/file/d/1eWicgh2eHJ0MkTDemXAC46YPpXhYYd33/view?usp=sharing">Download</a></div>
<div class="sqs-block-button"><a href="https://drive.google.com/file/d/1QyEpGHVSY00KDp3Nk8dDy_YK2ydzurlf/view?usp=drive_link">Download</a></div>
<div class="sqs-html-content"><h1>Season two of NEON SHRINES</h1></div>
<div class="sqs-html-content"><h2>Episode 1: Back on the Road</h2><p>September 2, 2022 4:00pm</p><p>Stella &amp; Jojo reflect on the typology of pilgrimages.</p><p class="" style="white-space:pre-wrap;" data-rte-preserve-empty="true">Songs: </p><p class="" style="white-space:pre-wrap;" data-rte-preserve-empty="true"><br>Modjo - Lady <br>Corona - The Rhythm of the Night <br>Eiffel 65 - Blue</p></div>
<div class="sqs-block-button"><a href="https://drive.google.com/file/d/1HAlovPgl0dzmE0EaTAtX1Xi3zD8njP06/view?usp=sharing">Download</a></div>
<div class="sqs-html-content"><h2>Episode 57: Peals of laughter &amp; thunder (ft. Ricky &amp; RevDev)</h2><p>February 23, 2024</p></div>
<div class="sqs-block-button"><a href="https://evil.example/file/d/1HAlovPgl0dzmE0EaTAtX1Xi3zD8njP06/view">Download</a></div>
<div class="sqs-html-content" data-sqsp-text-block-content><h2 style="white-space:pre-wrap;">Goodbye Neon Shrines Vol. 4</h2><p class="" style="white-space:pre-wrap;">October 11, 2024</p><p class="" data-rte-preserve-empty="true" style="white-space:pre-wrap;"></p></div>
<footer class="sections" id="footer-sections" data-footer-sections><section data-test="page-section"><div class="sqs-html-content"><p><span class="sqsrte-text-color--lightAccent">Made with </span><a href="http://squarespace.com" target="_blank"><span class="sqsrte-text-color--lightAccent">Squarespace</span></a></p><p><a href="mailto:email@example.com?" target="_blank">Email </a></p><p><span class="sqsrte-text-color--lightAccent">Copyright. All rights reserved.</span></p></div></section></footer>
"""


# Shapes copied from the live blog index (October 2026), trimmed to three posts:
# the filter buttons that reuse the card badge class, then day groups of cards.
# The off-site card is added to prove a link outside the blog is dropped.
MAILCHIMP_BLOG_HTML = """
<div class="fern-changelog-filters"><button class="fern-docs-badge large grayscale outlined interactive fern-filter-badge fern-filter-badge-selected">All</button><button class="fern-docs-badge large grayscale outlined-subtle interactive fern-filter-badge fern-filter-badge-unselected">Transactional</button><button class="fern-docs-badge large grayscale outlined-subtle interactive fern-filter-badge fern-filter-badge-unselected">Engineering</button></div>
<div class="fern-changelog-timeline">
<div class="fern-changelog-day-group"><time class="fern-changelog-day" dateTime="2025-06-10T00:00:00.000Z">Jun 10, 2025</time><div class="fern-changelog-day-entries"><div class="fern-changelog-card"><span class="fern-changelog-card-marker" aria-hidden="true"></span><div class="fern-changelog-card-body"><h3 class="fern-changelog-card-title"><a data-astro-prefetch="viewport" href="/developer/blog/reintroducing-mailchimp-transactional-for-your-email-and-sms-needs" class="fern-changelog-card-link">Reintroducing Mailchimp Transactional for your Email and SMS needs</a></h3><p class="fern-changelog-card-excerpt">A New Era for Mailchimp Transactional: Get Ready to Build Faster.</p><div class="fern-changelog-card-tags"><span class="fern-docs-badge small grayscale subtle">Transactional</span></div></div></div></div></div>
<div class="fern-changelog-day-group"><time class="fern-changelog-day" dateTime="2022-03-15T00:00:00.000Z">Mar 15, 2022</time><div class="fern-changelog-day-entries"><div class="fern-changelog-card"><span class="fern-changelog-card-marker" aria-hidden="true"></span><div class="fern-changelog-card-body"><h3 class="fern-changelog-card-title"><a data-astro-prefetch="viewport" href="/developer/blog/mailchimp-and-wordpress" class="fern-changelog-card-link">Mailchimp and WordPress</a></h3><p class="fern-changelog-card-excerpt">Bridging the Gap with Gravity Forms</p><div class="fern-changelog-card-tags"><span class="fern-docs-badge small grayscale subtle">Integrations</span></div></div></div><div class="fern-changelog-card"><div class="fern-changelog-card-body"><h3 class="fern-changelog-card-title"><a href="https://evil.example/developer/blog/phish" class="fern-changelog-card-link">Totally Real Post</a></h3></div></div></div></div>
<div class="fern-changelog-day-group"><time class="fern-changelog-day" dateTime="2021-11-16T00:00:00.000Z">Nov 16, 2021</time><div class="fern-changelog-day-entries"><div class="fern-changelog-card"><span class="fern-changelog-card-marker" aria-hidden="true"></span><div class="fern-changelog-card-body"><h3 class="fern-changelog-card-title"><a data-astro-prefetch="viewport" href="/developer/blog/leading-teams-closing-loops" class="fern-changelog-card-link">Leading Teams and Closing Loops</a></h3><p class="fern-changelog-card-excerpt">Your work isn’t solely defined by the PRs you push out</p><div class="fern-changelog-card-tags"><span class="fern-docs-badge small grayscale subtle">Culture</span><span class="fern-docs-badge small grayscale subtle">Engineering</span></div></div></div></div></div>
</div>
"""


def parse_fixture_updates(_content: str, site_url: str) -> ParsedWebpageFeed:
    return ParsedWebpageFeed(
        title="Fixture Updates",
        description=f"Updates from {site_url}.",
        items=(
            WebpageFeedItem(
                title="First update",
                url="https://updates.example/posts/first",
                published_at="2026-07-30T12:00:00Z",
                image_url="https://media.updates.example/first.jpg",
                details={"Topics": ["Music", "Software"]},
            ),
        ),
    )


FIXTURE_RECIPE = WebpageFeedRecipe(
    id="fixture-updates",
    name="fixture updates",
    default_url="https://updates.example/archive/",
    source_id_prefix="webpage",
    allowed_site_hosts=frozenset({"updates.example"}),
    allowed_path_prefixes=("/archive",),
    allowed_fetch_hosts=frozenset({"updates.example"}),
    allowed_item_hosts=frozenset({"updates.example"}),
    allowed_image_hosts=frozenset({"media.updates.example"}),
    parse=parse_fixture_updates,
)


class WebpageFeedTests(unittest.TestCase):
    def test_generic_recipe_renders_rss_without_site_specific_code(self) -> None:
        rss = render_webpage_feed_rss(
            FIXTURE_RECIPE,
            "https://updates.example/archive/",
            "fixture content",
        )

        self.assertIn("<title>Fixture Updates</title>", rss)
        self.assertIn("<title>First update</title>", rss)
        self.assertIn("Thu, 30 Jul 2026 12:00:00 +0000", rss)
        self.assertIn("Topics: Music, Software", rss)
        self.assertIn("https://media.updates.example/first.jpg", rss)

    def test_recipe_page_url_is_bounded(self) -> None:
        self.assertEqual(
            recipe_fetch_url(FIXTURE_RECIPE, "https://updates.example/archive/"),
            "https://updates.example/archive/",
        )
        for unsupported_url in (
            "https://evil.example/archive/",
            "https://updates.example/archive-elsewhere/",
            "https://updates.example:8443/archive/",
            "https://updates.example/archive/?private=1",
        ):
            with self.subTest(url=unsupported_url):
                with self.assertRaises(ValueError):
                    recipe_fetch_url(FIXTURE_RECIPE, unsupported_url)

    def test_recipe_rejects_an_item_url_outside_its_allowlist(self) -> None:
        unsafe_recipe = WebpageFeedRecipe(
            id="unsafe-fixture",
            name="unsafe fixture",
            default_url=FIXTURE_RECIPE.default_url,
            source_id_prefix="webpage",
            allowed_site_hosts=FIXTURE_RECIPE.allowed_site_hosts,
            allowed_path_prefixes=FIXTURE_RECIPE.allowed_path_prefixes,
            allowed_fetch_hosts=FIXTURE_RECIPE.allowed_fetch_hosts,
            allowed_item_hosts=FIXTURE_RECIPE.allowed_item_hosts,
            allowed_image_hosts=frozenset(),
            parse=lambda _content, _site_url: ParsedWebpageFeed(
                title="Unsafe",
                description="Unsafe fixture",
                items=(WebpageFeedItem(title="Bad", url="https://evil.example/post"),),
            ),
        )

        with self.assertRaisesRegex(ValueError, "unsupported item URL"):
            render_webpage_feed_rss(
                unsafe_recipe,
                "https://updates.example/archive/",
                "fixture content",
            )

    def test_recipe_rejects_a_fetch_url_outside_its_allowlist(self) -> None:
        unsafe_fetch_recipe = WebpageFeedRecipe(
            id="unsafe-fetch-fixture",
            name="unsafe fetch fixture",
            default_url=FIXTURE_RECIPE.default_url,
            source_id_prefix="webpage",
            allowed_site_hosts=FIXTURE_RECIPE.allowed_site_hosts,
            allowed_path_prefixes=FIXTURE_RECIPE.allowed_path_prefixes,
            allowed_fetch_hosts=FIXTURE_RECIPE.allowed_fetch_hosts,
            allowed_item_hosts=FIXTURE_RECIPE.allowed_item_hosts,
            allowed_image_hosts=FIXTURE_RECIPE.allowed_image_hosts,
            parse=parse_fixture_updates,
            build_fetch_url=lambda _site_url: "https://evil.example/rendered",
        )

        with self.assertRaisesRegex(ValueError, "unsupported fetch URL"):
            recipe_fetch_url(unsafe_fetch_recipe, unsafe_fetch_recipe.default_url)

    def test_hydefm_is_one_registered_webpage_recipe(self) -> None:
        parsed = parse_webpage_feed(
            HYDEFM_ARCHIVE_RECIPE,
            HYDEFM_HTML,
            HYDEFM_ARCHIVE_RECIPE.default_url,
        )
        rss = render_webpage_feed_rss(
            HYDEFM_ARCHIVE_RECIPE,
            HYDEFM_ARCHIVE_RECIPE.default_url,
            HYDEFM_HTML,
        )

        self.assertEqual(parsed.title, "HydeFM Archives")
        self.assertEqual(len(parsed.items), 2)
        self.assertEqual(parsed.items[0].details["Genres"], ["Bass", "Club", "Techno"])
        self.assertIn("<title>stooped w/ pijeon</title>", rss)
        self.assertIn("Thu, 02 Jul 2026 00:00:00 +0000", rss)

    def test_hydefm_recipe_fetches_only_its_public_archive_page(self) -> None:
        self.assertEqual(
            recipe_fetch_url(
                HYDEFM_ARCHIVE_RECIPE,
                "https://www.hydefm.com/archives/",
            ),
            "https://www.hydefm.com/archives/",
        )
        for unsupported_url in (
            "https://evil.example/archives/",
            "https://hydefm.com.evil.example/archives/",
            "https://hydefm.com/archives-elsewhere/",
            "https://hydefm.com/archives/?private=1",
        ):
            with self.subTest(url=unsupported_url):
                with self.assertRaises(ValueError):
                    recipe_fetch_url(HYDEFM_ARCHIVE_RECIPE, unsupported_url)


    def test_neon_shrines_recipe_reads_seasons_dates_songs_and_safe_audio_links(self) -> None:
        parsed = parse_webpage_feed(
            OSGH_NEON_SHRINES_RECIPE,
            NEON_SHRINES_HTML,
            OSGH_NEON_SHRINES_RECIPE.default_url,
        )
        titles = [item.title for item in parsed.items]

        self.assertEqual(parsed.title, "NEON SHRINES")
        self.assertEqual(
            titles,
            [
                "S3 Episode 2: Time Pilgrims & Camino Report (ft. Matthew)",
                "S3 Episode 1: The Return of NEON SHRINES, again!",
                "S2 Goodbye Neon Shrines Vol. 4",
                "S2 Episode 57: Peals of laughter & thunder (ft. Ricky & RevDev)",
                "S2 Episode 1: Back on the Road",
            ],
        )
        second, first, goodbye, no_time, season_two = parsed.items
        self.assertEqual(first.published_at, "July 24, 2026 4:00pm")
        # A season's buttons stacked under its last episode are ambiguous, so none is attached.
        self.assertNotIn("Audio", second.details)
        self.assertEqual(
            season_two.details["Audio"],
            ["https://drive.google.com/file/d/1HAlovPgl0dzmE0EaTAtX1Xi3zD8njP06/view"],
        )
        self.assertNotIn("Audio", no_time.details)
        self.assertNotIn("Audio", goodbye.details)

    def test_neon_shrines_splits_song_lists_on_runs_of_spaces_and_breaks(self) -> None:
        second, first, _goodbye, _no_time, season_two = parse_webpage_feed(
            OSGH_NEON_SHRINES_RECIPE,
            NEON_SHRINES_HTML,
            OSGH_NEON_SHRINES_RECIPE.default_url,
        ).items

        # The double space inside "John Sinclair -  Ain’t" is not a break.
        self.assertEqual(
            first.details["Songs"],
            ["Angra - Wuthering Heights", "John Sinclair - Ain’t Nobody’s Business", "Kylie Minogue - Love at First Sight"],
        )
        self.assertEqual(
            second.details["Songs"],
            ["Gorillaz - Dracula", "Yeah Yeah Yeahs- Gold Lion", "Gang of Four - Damaged Goods"],
        )
        self.assertEqual(
            season_two.details["Songs"],
            ["Modjo - Lady", "Corona - The Rhythm of the Night", "Eiffel 65 - Blue"],
        )

    def test_neon_shrines_ignores_the_site_footer(self) -> None:
        goodbye = parse_webpage_feed(
            OSGH_NEON_SHRINES_RECIPE,
            NEON_SHRINES_HTML,
            OSGH_NEON_SHRINES_RECIPE.default_url,
        ).items[2]

        self.assertEqual(goodbye.title, "S2 Goodbye Neon Shrines Vol. 4")
        self.assertEqual(goodbye.details, {"Aired": ["October 11, 2024"]})

    def test_neon_shrines_lists_newest_first_so_the_bridge_cap_keeps_recent_episodes(self) -> None:
        rss = render_webpage_feed_rss(
            OSGH_NEON_SHRINES_RECIPE,
            OSGH_NEON_SHRINES_RECIPE.default_url,
            NEON_SHRINES_HTML,
        )

        # In page order, a cap of three would keep season two's oldest episode
        # and drop its newest.
        capped = limit_rss_items(rss, 3)
        self.assertIn("Goodbye Neon Shrines Vol. 4", capped)
        self.assertNotIn("Back on the Road", capped)

    def test_neon_shrines_identity_is_the_air_date(self) -> None:
        def url_aired_on(html: str, published_at: str) -> str:
            items = parse_webpage_feed(OSGH_NEON_SHRINES_RECIPE, html, OSGH_NEON_SHRINES_RECIPE.default_url).items
            return next(item.url for item in items if item.published_at == published_at)

        self.assertEqual(
            url_aired_on(NEON_SHRINES_HTML, "September 2, 2022 4:00pm"),
            "https://www.osghpress.com/neon-shrines?episode=2022-09-02",
        )
        # Retitling an episode on the page must not make it a new item.
        retitled = NEON_SHRINES_HTML.replace("Episode 1: Back on the Road", "Episode 1: Back on the Road (ft. Jeb)")
        self.assertEqual(
            url_aired_on(retitled, "September 2, 2022 4:00pm"),
            "https://www.osghpress.com/neon-shrines?episode=2022-09-02",
        )

    def test_neon_shrines_keeps_same_day_episodes_and_skips_non_dates(self) -> None:
        html = """
        <h1>Season two of NEON SHRINES</h1>
        <h2>Episode 9: Morning Special</h2><p>Volume 2, 2024 of the zine is out.</p><p>March 1, 2024 10:00am</p>
        <h2>Episode 10: Afternoon Special</h2><p>March 1, 2024 4:00pm</p>
        """
        afternoon, morning = parse_webpage_feed(
            OSGH_NEON_SHRINES_RECIPE,
            html,
            OSGH_NEON_SHRINES_RECIPE.default_url,
        ).items

        self.assertEqual(morning.url, "https://www.osghpress.com/neon-shrines?episode=2024-03-01")
        self.assertEqual(
            afternoon.url,
            "https://www.osghpress.com/neon-shrines?episode=2024-03-01-episode-10-afternoon-special",
        )
        # "Volume 2, 2024" fits the date pattern but is not a date.
        self.assertEqual(morning.published_at, "March 1, 2024 10:00am")
        self.assertEqual(morning.details["About"], ["Volume 2, 2024 of the zine is out."])

    def test_neon_shrines_rss_uses_pacific_air_times(self) -> None:
        rss = render_webpage_feed_rss(
            OSGH_NEON_SHRINES_RECIPE,
            OSGH_NEON_SHRINES_RECIPE.default_url,
            NEON_SHRINES_HTML,
        )

        self.assertIn("<title>NEON SHRINES</title>", rss)
        self.assertIn("Fri, 24 Jul 2026 16:00:00 -0700", rss)
        # No listed time falls back to the 4pm slot, in PST for February.
        self.assertIn("Fri, 23 Feb 2024 16:00:00 -0800", rss)
        self.assertIn(
            "Songs: Angra - Wuthering Heights, John Sinclair - Ain’t Nobody’s Business, Kylie Minogue - Love at First Sight",
            rss,
        )
        self.assertNotIn("evil.example", rss)
        self.assertNotIn("Squarespace", rss)

    def test_neon_shrines_recipe_is_bounded_to_its_page(self) -> None:
        self.assertIs(webpage_recipe_for_url("https://www.osghpress.com/neon-shrines"), OSGH_NEON_SHRINES_RECIPE)
        self.assertIs(webpage_recipe_for_url("https://hydefm.com/archives/"), HYDEFM_ARCHIVE_RECIPE)
        for unsupported_url in (
            "https://www.osghpress.com/paper-shrines",
            "https://www.osghpress.com/neon-shrines?format=json",
            "https://osghpress.com.evil.example/neon-shrines",
            "http://www.osghpress.com/neon-shrines",
        ):
            with self.subTest(url=unsupported_url):
                with self.assertRaises(ValueError):
                    recipe_fetch_url(OSGH_NEON_SHRINES_RECIPE, unsupported_url)

    def test_mailchimp_blog_recipe_reads_posts_dates_summaries_and_tags(self) -> None:
        parsed = parse_webpage_feed(
            MAILCHIMP_ENGINEERING_BLOG_RECIPE,
            MAILCHIMP_BLOG_HTML,
            MAILCHIMP_ENGINEERING_BLOG_RECIPE.default_url,
        )

        self.assertEqual(parsed.title, "Mailchimp Engineering")
        self.assertEqual(
            [(item.title, item.url, item.published_at) for item in parsed.items],
            [
                (
                    "Reintroducing Mailchimp Transactional for your Email and SMS needs",
                    "https://mailchimp.com/developer/blog/reintroducing-mailchimp-transactional-for-your-email-and-sms-needs",
                    "2025-06-10T00:00:00.000Z",
                ),
                ("Mailchimp and WordPress", "https://mailchimp.com/developer/blog/mailchimp-and-wordpress", "2022-03-15T00:00:00.000Z"),
                ("Leading Teams and Closing Loops", "https://mailchimp.com/developer/blog/leading-teams-closing-loops", "2021-11-16T00:00:00.000Z"),
            ],
        )
        newest, _wordpress, culture = parsed.items
        self.assertEqual(newest.details["Summary"], ["A New Era for Mailchimp Transactional: Get Ready to Build Faster."])
        # The filter buttons above the timeline share the badge class but are not tags.
        self.assertEqual(newest.details["Tags"], ["Transactional"])
        self.assertEqual(culture.details["Tags"], ["Culture", "Engineering"])

    def test_mailchimp_blog_rss_dates_posts_and_drops_off_site_links(self) -> None:
        rss = render_webpage_feed_rss(
            MAILCHIMP_ENGINEERING_BLOG_RECIPE,
            MAILCHIMP_ENGINEERING_BLOG_RECIPE.default_url,
            MAILCHIMP_BLOG_HTML,
        )

        self.assertIn("<title>Mailchimp Engineering</title>", rss)
        self.assertIn("<pubDate>Tue, 10 Jun 2025 00:00:00 +0000</pubDate>", rss)
        self.assertIn("Summary: Bridging the Gap with Gravity Forms", rss)
        self.assertNotIn("evil.example", rss)
        self.assertNotIn("Totally Real Post", rss)

    def test_mailchimp_blog_recipe_is_bounded_to_the_blog(self) -> None:
        self.assertIs(webpage_recipe_for_url("https://mailchimp.com/developer/blog/"), MAILCHIMP_ENGINEERING_BLOG_RECIPE)
        for unsupported_url in (
            "https://mailchimp.com/developer/",
            "https://mailchimp.com/developer/blog/?tag=engineering",
            "https://mailchimp.com.evil.example/developer/blog/",
            "http://mailchimp.com/developer/blog/",
        ):
            with self.subTest(url=unsupported_url):
                with self.assertRaises(ValueError):
                    recipe_fetch_url(MAILCHIMP_ENGINEERING_BLOG_RECIPE, unsupported_url)


if __name__ == "__main__":
    unittest.main()
