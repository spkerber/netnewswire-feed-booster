import unittest

from netnewswire_feed_booster.nts import parse_nts_show_html, render_nts_show_rss


NTS_HTML = '''
<script>window._REACT_STATE_ = {"show":{"name":"NKISI","description":"Monthly show","show_alias":"nkisi","episodes":[{"name":"NKISI","description":"Latest episode","episode_alias":"nkisi-1st-july-2026","show_alias":"nkisi","broadcast":"2026-07-01T21:00:00+00:00","mixcloud":"https://www.mixcloud.com/NTSRadio/nkisi-1st-july-2026/","audio_sources":[{"url":"https://soundcloud.com/example/nkisi","source":"soundcloud"}],"media":{"picture_medium":"https://media.example/image.jpg"}}]}};</script>
'''

NTS_MALICIOUS_HTML = '''
<script>window._REACT_STATE_ = {"show":{"name":"NKISI","description":"Monthly show","show_alias":"nkisi","episodes":[{"name":"NKISI","description":"&lt;script&gt;alert(1)&lt;/script&gt;","episode_alias":"nkisi-1st-july-2026","show_alias":"nkisi","broadcast":"2026-07-01T21:00:00+00:00","audio_sources":[{"url":"javascript:alert(1)","source":"soundcloud"}],"media":{"picture_medium":"https://evil.example/image.jpg"}}]}};</script>
'''


class NTSTests(unittest.TestCase):
    def test_parse_nts_show_html(self) -> None:
        title, description, episodes = parse_nts_show_html(NTS_HTML, "https://www.nts.live/shows/nkisi")

        self.assertEqual(title, "NKISI")
        self.assertEqual(description, "Monthly show")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].url, "https://www.nts.live/shows/nkisi/episodes/nkisi-1st-july-2026")
        self.assertEqual(episodes[0].audio_url, "https://soundcloud.com/example/nkisi")

    def test_render_nts_show_rss(self) -> None:
        rss = render_nts_show_rss("https://www.nts.live/shows/nkisi", fetcher=lambda _: NTS_HTML)

        self.assertIn("<title>NTS: NKISI</title>", rss)
        self.assertIn("<title>NKISI</title>", rss)
        self.assertIn("Wed, 01 Jul 2026 21:00:00 +0000", rss)
        self.assertIn("https://soundcloud.com/example/nkisi", rss)

    def test_render_nts_show_rss_drops_unsafe_media_urls(self) -> None:
        rss = render_nts_show_rss("https://www.nts.live/shows/nkisi", fetcher=lambda _: NTS_MALICIOUS_HTML)

        self.assertNotIn("javascript:alert", rss)
        self.assertNotIn("evil.example", rss)
        self.assertNotIn("<script>", rss)


if __name__ == "__main__":
    unittest.main()
