import socket
import unittest
from unittest.mock import patch

from netnewswire_feed_booster.http_client import (
    fetch_json_post,
    fetch_text,
    fetch_text_response,
)


def addrinfo_for(ip: str):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443) if family == socket.AF_INET else (ip, 443, 0, 0))]


class DnsRebindingTests(unittest.TestCase):
    def test_rejects_a_host_resolving_to_a_private_address(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("10.0.0.5")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                with self.assertRaisesRegex(ValueError, "non-public address"):
                    fetch_text("https://example.com/feed")
        urlopen.assert_not_called()

    def test_rejects_a_host_resolving_to_the_cloud_metadata_address(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("169.254.169.254")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                with self.assertRaisesRegex(ValueError, "non-public address"):
                    fetch_text("https://example.com/feed")
        urlopen.assert_not_called()

    def test_rejects_a_host_resolving_to_loopback(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("127.0.0.1")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                with self.assertRaisesRegex(ValueError, "non-public address"):
                    fetch_text("https://example.com/feed")
        urlopen.assert_not_called()

    def test_allows_a_host_resolving_to_a_public_address(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("8.8.8.8")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"ok"
                urlopen.return_value.__enter__.return_value.headers = {}
                fetch_text("https://example.com/feed")
        urlopen.assert_called_once()

    def test_dns_resolution_failure_does_not_block_the_request(self) -> None:
        # A dead/typo'd domain isn't a security violation — let the real request
        # surface its own clear connection error instead of masking it.
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"ok"
                urlopen.return_value.__enter__.return_value.headers = {}
                fetch_text("https://example.com/feed")
        urlopen.assert_called_once()


class ContentTypeValidationTests(unittest.TestCase):
    def test_rejects_a_binary_content_type(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("8.8.8.8")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"\x89PNG..."
                urlopen.return_value.__enter__.return_value.headers = {"Content-Type": "image/png"}
                with self.assertRaisesRegex(ValueError, "Unexpected content type"):
                    fetch_text_response("https://example.com/feed")

    def test_accepts_an_expected_text_content_type(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("8.8.8.8")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"<rss></rss>"
                urlopen.return_value.__enter__.return_value.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}
                response = fetch_text_response("https://example.com/feed")
        self.assertEqual(response.text, "<rss></rss>")

    def test_accepts_a_missing_content_type(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("8.8.8.8")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"<html></html>"
                urlopen.return_value.__enter__.return_value.headers = {}
                response = fetch_text_response("https://example.com/feed")
        self.assertEqual(response.text, "<html></html>")


class FetchJsonPostTests(unittest.TestCase):
    def test_rejects_an_oversized_response(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("8.8.8.8")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"x" * 20
                with self.assertRaisesRegex(ValueError, "byte limit"):
                    fetch_json_post("https://example.com/api", {"a": 1}, max_bytes=10)

    def test_rejects_an_unapproved_host_before_network_io(self) -> None:
        with patch("urllib.request.OpenerDirector.open") as urlopen:
            with self.assertRaisesRegex(ValueError, "Unsafe fetch URL"):
                fetch_json_post("https://metadata.example/api", {"a": 1}, allowed_hosts={"api-v2.soundcloud.com"})
        urlopen.assert_not_called()

    def test_returns_parsed_json_on_success(self) -> None:
        with patch("netnewswire_feed_booster.http_client.socket.getaddrinfo", return_value=addrinfo_for("8.8.8.8")):
            with patch("urllib.request.OpenerDirector.open") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
                result = fetch_json_post("https://example.com/api", {"a": 1})
        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
