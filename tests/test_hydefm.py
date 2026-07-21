import unittest

from netnewswire_feed_booster.hydefm import hydefm_text_mirror_url, parse_hydefm_archive_markdown, render_hydefm_archive_rss


HYDEFM_MARKDOWN = """
Title: Archives | HydeFM

![Image 34](https://hydefmradio-archive.s3.us-west-1.amazonaws.com/wp-content/uploads/2026/07/02212135/20260703-030123.jpg)

## July 2, 2026

## [stooped w/ pijeon](https://hydefm.com/archive/stooped-w-pijeon-07-02-26/)

[Bass](https://hydefm.com/genres/bass/)[Club](https://hydefm.com/genres/club/)[Techno](https://hydefm.com/genres/techno/)

![Image 37](https://hydefmradio-archive.s3.us-west-1.amazonaws.com/wp-content/uploads/2026/07/01212123/20260702-030534.jpg)

## July 1, 2026

## [FLUXIONS w/ Vertigo](https://hydefm.com/archive/fluxions-w-vertigo-07-01-26/)
"""


class HydeFMTests(unittest.TestCase):
    def test_parse_hydefm_archive_markdown(self) -> None:
        title, description, items = parse_hydefm_archive_markdown(HYDEFM_MARKDOWN)

        self.assertIn("HydeFM", title)
        self.assertTrue(title.endswith("Archives"))
        self.assertIn("hydefm.com/archives", description)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "stooped w/ pijeon")
        self.assertEqual(items[0].published_at, "July 2, 2026")
        self.assertEqual(items[0].genres, ["Bass", "Club", "Techno"])
        self.assertEqual(items[1].image_url, "https://hydefmradio-archive.s3.us-west-1.amazonaws.com/wp-content/uploads/2026/07/01212123/20260702-030534.jpg")

    def test_render_hydefm_archive_rss(self) -> None:
        rss = render_hydefm_archive_rss(fetcher=lambda _: HYDEFM_MARKDOWN)

        self.assertIn("<title>HydeFM", rss)
        self.assertIn("Archives</title>", rss)
        self.assertIn("<title>stooped w/ pijeon</title>", rss)
        self.assertIn("Thu, 02 Jul 2026 00:00:00 +0000", rss)
        self.assertIn("https://hydefm.com/archive/fluxions-w-vertigo-07-01-26/", rss)

    def test_hydefm_text_mirror_url_is_not_a_generic_proxy(self) -> None:
        self.assertEqual(
            hydefm_text_mirror_url("https://www.hydefm.com/archives/?test_fixture=123"),
            "https://r.jina.ai/http://www.hydefm.com/archives/?test_fixture=123",
        )
        with self.assertRaises(ValueError):
            hydefm_text_mirror_url("https://evil.example/archives/")
        with self.assertRaises(ValueError):
            hydefm_text_mirror_url("https://hydefm.com.evil.example/archives/")


if __name__ == "__main__":
    unittest.main()
