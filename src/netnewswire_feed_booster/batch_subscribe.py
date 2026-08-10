"""Read a list of public source URLs and route each one to the subscribe command
it already belongs to.

This module owns only two decisions: how to read a batch file, and which existing
subcommand a URL belongs to. It deliberately holds no subscribe logic of its own —
the CLI dispatches each detected line to the same single-URL command a person
would have typed, so a batch run and eight hand-typed commands do the same work.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


# Which single-URL subcommand each detected or forced adapter speaks to. Every
# value here has to stay a real subparser name in cli.build_parser().
#
# "adapter" rather than "kind" on purpose: a source's `kind` is a separate field
# with its own vocabulary (website, newsletter, other), and `subscribe-feed-url
# --kind` already means that field. The two overlap on bandcamp/youtube/substack/
# podcast and diverge everywhere else, so a batch line picks an adapter.
BATCH_ADAPTER_COMMANDS = {
    "bandcamp": "subscribe-bandcamp-source",
    "youtube": "import-youtube-channel-url",
    "soundcloud": "subscribe-soundcloud-profile",
    "substack": "subscribe-substack",
    "mixcloud": "subscribe-mixcloud-profile",
    "nts": "subscribe-nts-show",
    "webpage": "subscribe-webpage-feed",
    "podcast": "subscribe-podcast",
    "feed-url": "subscribe-feed-url",
}

# Channel-page shapes import-youtube-channel-url can read. A watch, playlist, or
# search URL is not a channel, so it falls through to feed discovery instead of
# being fetched as one.
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_YOUTUBE_CHANNEL_PREFIXES = ("/@", "/channel/", "/c/", "/user/")
_SOUNDCLOUD_HOSTS = frozenset({"soundcloud.com", "www.soundcloud.com"})
_MIXCLOUD_HOSTS = frozenset({"mixcloud.com", "www.mixcloud.com"})
_NTS_HOSTS = frozenset({"nts.live", "www.nts.live"})


@dataclass(frozen=True)
class BatchLine:
    """One URL to subscribe, plus the adapter the line forced, if any."""

    url: str
    adapter: str
    line_number: int


def parse_batch_lines(text: str) -> list[BatchLine]:
    """Read a batch file body into URLs and their optional per-line adapter override.

    Blank lines and `#` comment lines are ignored, as is a trailing `#` comment on
    a URL line. The only token accepted after a URL is `--adapter=<adapter>`;
    anything else is an error rather than a silently dropped flag, because quietly
    ignoring `--group=Music` would file a batch of feeds somewhere the caller did
    not ask for.
    """

    lines: list[BatchLine] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        url = ""
        adapter = ""
        for token in stripped.split():
            if token.startswith("#"):
                break
            if not url:
                url = token
                continue
            if token.startswith("--adapter="):
                adapter = _validated_adapter(token.split("=", 1)[1], line_number)
                continue
            if token.startswith("--kind="):
                raise ValueError(
                    f"Line {line_number}: use --adapter= rather than --kind=. "
                    "In this tool --kind names a source's own kind, which is a "
                    "different vocabulary; a batch line picks which adapter runs."
                )
            raise ValueError(
                f"Line {line_number}: unsupported token {token!r}. A batch line "
                "accepts one URL and an optional --adapter=<adapter> override."
            )

        if url:
            lines.append(BatchLine(url=url, adapter=adapter, line_number=line_number))
    return lines


def _validated_adapter(adapter: str, line_number: int) -> str:
    if adapter not in BATCH_ADAPTER_COMMANDS:
        supported = ", ".join(sorted(BATCH_ADAPTER_COMMANDS))
        raise ValueError(
            f"Line {line_number}: unknown --adapter={adapter}. Supported adapters: {supported}."
        )
    return adapter


def detect_batch_adapter(url: str) -> str:
    """Return the adapter a URL belongs to, defaulting to public feed discovery.

    A registered webpage recipe is consulted through the existing allowlist rather
    than any URL guessing of its own, so a recipe page keeps going through
    subscribe-webpage-feed instead of the generic fallback.
    """

    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path or "/"

    if hostname == "bandcamp.com" or hostname.endswith(".bandcamp.com"):
        return "bandcamp"
    if hostname in _YOUTUBE_HOSTS and path.startswith(_YOUTUBE_CHANNEL_PREFIXES):
        return "youtube"
    if hostname in _SOUNDCLOUD_HOSTS:
        return "soundcloud"
    if hostname == "substack.com" or hostname.endswith(".substack.com"):
        return "substack"
    if hostname in _MIXCLOUD_HOSTS:
        return "mixcloud"
    if hostname in _NTS_HOSTS and path.startswith("/shows/"):
        return "nts"

    from .webpage_recipes import webpage_recipe_for_url

    if webpage_recipe_for_url(url) is not None:
        return "webpage"
    return "feed-url"
