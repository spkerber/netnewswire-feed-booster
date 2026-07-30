import unittest

from netnewswire_feed_booster.webpage_feeds import (
    ParsedWebpageFeed,
    WebpageFeedItem,
    WebpageFeedRecipe,
    parse_webpage_feed,
    recipe_fetch_url,
    render_webpage_feed_rss,
)
from netnewswire_feed_booster.webpage_recipes import HYDEFM_ARCHIVE_RECIPE


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


if __name__ == "__main__":
    unittest.main()
