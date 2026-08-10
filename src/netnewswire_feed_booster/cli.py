from __future__ import annotations

import argparse
import io
import os
import sys
import time
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from .bandcamp import (
    write_bandcamp_collection_rss,
)
from .batch_subscribe import BATCH_ADAPTER_COMMANDS, detect_batch_adapter, parse_batch_lines
from .bandcamp_following import import_bandcamp_following
from .bandcamp_sources import build_bandcamp_source_from_url
from .subscription_history import SubscriptionHistoryStore, default_subscription_history_path
from .feed_store import FeedStore, Source, default_private_sources_path, default_sources_path, normalize_url, slugify, source_id_from_title
from .feed_identity import (
    FeedIdentity,
    canonical_url,
    identity_match_reason,
    likely_same_title,
    parse_feed_identity,
)
from .feed_validation import audit_sources, discover_feed_url
from .generated_migration import apply_generated_source_migration, plan_generated_source_migration
from .generated_adapters import BANDCAMP_ADAPTER, NTS_ADAPTER, WEBPAGE_ADAPTER, adapter_for_source
from .bridge_policy import (
    DEFAULT_MAX_REFRESH_SOURCES_PER_RUN,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    DEFAULT_REFRESH_SCHEDULE_HOURS,
    refresh_route_plan,
)
from .hosted_bandcamp import bandcamp_fetch_url, bandcamp_items_for_source, sources_with_hosted_generated_feeds
from .hosted_bandcamp import render_bandcamp_source_rss
from .nts import parse_nts_show_html
from .mixcloud import mixcloud_source
from .opml import parse_opml, write_opml
from .http_client import fetch_text
from .podcasts import podcast_source_from_url
from .soundcloud import fetch_soundcloud_following_sources, fetch_soundcloud_profile_source
from .source_collections import (
    active_sources_with_private,
    drift_has_failures,
    filtered_sources_with_private,
    netnewswire_drift_report,
    print_drift_report,
    resolve_source_identifier,
    display_feed_url,
)
from .substack import parse_substack_library_html, parse_substack_profile_html
from .webpage_feeds import parse_webpage_feed
from .webpage_recipes import HYDEFM_ARCHIVE_RECIPE, require_webpage_recipe
from .youtube import parse_youtube_channel_html, parse_youtube_subscriptions_file


DEFAULT_PROFILE = os.environ.get("RSS_PROFILE", "me")

# Which adapter a subscribe or import command speaks to. The key drives both the
# built-in folder default and the RSS_DEFAULT_GROUP_<KEY> environment override.
ADAPTER_KEY_BY_COMMAND = {
    "import-bandcamp-following": "BANDCAMP",
    "import-soundcloud-following": "SOUNDCLOUD",
    "import-substack-library": "SUBSTACK",
    "import-substack-profile": "SUBSTACK",
    "import-youtube-channel-url": "YOUTUBE",
    "import-youtube-subscriptions": "YOUTUBE",
    "subscribe-bandcamp-source": "BANDCAMP",
    "subscribe-mixcloud-profile": "MIXCLOUD",
    "subscribe-nts-show": "NTS",
    "subscribe-podcast": "PODCAST",
    "subscribe-soundcloud-profile": "SOUNDCLOUD",
    "subscribe-substack": "SUBSTACK",
    "subscribe-webpage-feed": "WEBPAGE",
    "subscribe-youtube": "YOUTUBE",
}

# Same keys for the generic commands, where the adapter is whatever --kind says.
# "auto", "website", "newsletter", and "other" are deliberately absent: they name
# no single upstream, so those sources stay at the OPML root.
ADAPTER_KEY_BY_KIND = {
    "bandcamp": "BANDCAMP",
    "podcast": "PODCAST",
    "substack": "SUBSTACK",
    "youtube": "YOUTUBE",
}

# Platforms: many publishers share one host, and the platform name is a fact
# about where the feed comes from rather than a claim about what it contains.
# Defaulting to it is safe for any reader's list, so these need no configuration.
PLATFORM_GROUPS = {
    "BANDCAMP": "Bandcamp",
    "MIXCLOUD": "Mixcloud",
    "PODCAST": "Podcasts",
    "SOUNDCLOUD": "SoundCloud",
    "SUBSTACK": "Substack",
    "YOUTUBE": "YouTube",
}

# Independent sites: NTS and the webpage recipes are single stations and
# publications, not platforms other people also publish on. A folder named "NTS"
# would hold exactly one site, and only the reader knows the category it belongs
# to — Online Radio for one person, Archives or Music for another. So there is no
# built-in default. Set RSS_DEFAULT_GROUP_NTS or RSS_DEFAULT_GROUP_WEBPAGE once
# and every later subscribe inherits it; until then these land at the OPML root
# with a note saying so.
INDEPENDENT_SITE_KEYS = frozenset({"NTS", "WEBPAGE"})

# Direct-feed one-off fetches don't go through a GeneratedAdapter, so they don't
# get its host allowlist for free. Restrict them here so a redirect can't carry
# the fetch to an unexpected host, matching every generated adapter's posture.
YOUTUBE_HOSTS = frozenset({"www.youtube.com", "youtube.com", "m.youtube.com"})
SUBSTACK_ALLOWED_SUFFIXES = frozenset({"substack.com"})

# TODO: decide whether grouping should become per-feed rather than per-adapter.
# The split above is still adapter-shaped: every Bandcamp feed shares one folder,
# which suits a list dominated by one source type and suits nobody who would
# rather sort by genre, label, or reading priority. Doing that well needs a
# per-source rule or a mapping file, plus a decision about what re-import does to
# hand-placed feeds. Worth settling before these defaults harden into an
# assumption. RSS_DEFAULT_GROUP_<KEY> is a stopgap, not that answer.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netnewswire-feed-booster",
        description="Manage local RSS sources and export NetNewsWire-compatible OPML.",
    )
    parser.add_argument("--data", type=Path, default=None, help="Path to sources.json")
    parser.add_argument("--private-data", type=Path, default=default_private_sources_path(), help="Path to local gitignored private sources overlay")
    parser.add_argument("--history", type=Path, default=None, help="Path to subscription-history.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-opml", help="Import sources from an OPML file")
    import_parser.add_argument("path", type=Path)
    import_parser.add_argument("--profile", default=DEFAULT_PROFILE)

    migration_parser = subparsers.add_parser(
        "migrate-generated-sources",
        help="Rebuild generated-source metadata from an old private registry without copying it",
    )
    migration_parser.add_argument("reference", type=Path, help="Ignored old sources.<profile>.json file to read as migration reference")
    migration_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    migration_parser.add_argument("--bandcamp-out-dir", type=Path, default=Path("exports/generated"))
    migration_parser.add_argument("--generated-out-dir", type=Path, default=Path("exports/generated"))
    migration_parser.add_argument("--apply", action="store_true", help="Write the rebuilt generated-source metadata into --data")

    export_parser = subparsers.add_parser("export-opml", help="Export active sources to OPML")
    export_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    export_parser.add_argument("--out", type=Path, required=True)
    export_parser.add_argument("--title", default=None)
    export_parser.add_argument(
        "--bandcamp-feed-base",
        default="",
        help="Rewrite Bandcamp feed URLs to hosted HTTPS URLs under this base, e.g. https://example.modal.run",
    )
    export_parser.add_argument(
        "--bandcamp-feed-token",
        default=os.environ.get("RSS_FEED_TOKEN", os.environ.get("BANDCAMP_FEED_TOKEN", "")),
        help="Token path segment for hosted generated feed URLs. Defaults to RSS_FEED_TOKEN, then BANDCAMP_FEED_TOKEN.",
    )

    list_parser = subparsers.add_parser("list", help="List sources")
    list_parser.add_argument("--profile", default=None)
    list_parser.add_argument("--status", default=None)
    list_parser.add_argument("--kind", default=None)
    list_parser.add_argument(
        "--show-sensitive",
        action="store_true",
        help="Include raw feed URLs. Omit this when sharing terminal output.",
    )

    add_parser = subparsers.add_parser("add", help="Add a source")
    add_source_arguments(add_parser)

    subscribe_feed_parser = subparsers.add_parser(
        "subscribe-feed-url",
        help="Discover and add one public feed after checking canonical metadata and recent item IDs for duplicates",
    )
    subscribe_feed_parser.add_argument("url")
    subscribe_feed_parser.add_argument("--title", default="")
    subscribe_feed_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    subscribe_feed_parser.add_argument("--group", action="append", default=[])
    subscribe_feed_parser.add_argument(
        "--kind",
        default="auto",
        choices=["auto", "website", "substack", "youtube", "newsletter", "podcast", "other"],
    )
    subscribe_feed_parser.add_argument(
        "--allow-possible-duplicate",
        action="store_true",
        help="Add even when another same-kind source has the same normalized title but no strong identity match",
    )
    configured_netnewswire_opml = os.environ.get("NETNEWSWIRE_OPML", "").strip()
    subscribe_feed_parser.add_argument(
        "--against-opml",
        type=Path,
        default=Path(configured_netnewswire_opml) if configured_netnewswire_opml else None,
        help="Also check an existing NetNewsWire OPML export. Defaults to NETNEWSWIRE_OPML when configured.",
    )

    subscribe_substack_parser = subparsers.add_parser("subscribe-substack", help="One-off add or reactivate a Substack feed")
    subscribe_substack_parser.add_argument("domain_or_url")
    subscribe_substack_parser.add_argument("--title", default="")
    subscribe_substack_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    subscribe_substack_parser.add_argument("--group", default="")
    subscribe_substack_parser.add_argument("--notes", default="")

    subscribe_youtube_parser = subparsers.add_parser("subscribe-youtube", help="One-off add or reactivate a YouTube channel RSS feed by channel ID")
    subscribe_youtube_parser.add_argument("channel_id")
    subscribe_youtube_parser.add_argument("--title", required=True)
    subscribe_youtube_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    subscribe_youtube_parser.add_argument("--group", default="")
    subscribe_youtube_parser.add_argument("--notes", default="")

    youtube_url_parser = subparsers.add_parser("import-youtube-channel-url", help="Import one YouTube channel URL by reading its RSS metadata")
    youtube_url_parser.add_argument("url")
    youtube_url_parser.add_argument("--title", default="")
    youtube_url_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    youtube_url_parser.add_argument("--group", default="")

    youtube_subs_parser = subparsers.add_parser("import-youtube-subscriptions", help="Import YouTube subscriptions from a CSV or local list")
    youtube_subs_parser.add_argument("path", type=Path)
    youtube_subs_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    youtube_subs_parser.add_argument("--group", default="")

    substack_profile_parser = subparsers.add_parser("import-substack-profile", help="Import public Substack subscriptions from a profile page")
    substack_profile_parser.add_argument("url")
    substack_profile_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    substack_profile_parser.add_argument("--group", default="")

    substack_library_parser = subparsers.add_parser("import-substack-library", help="Import Substack subscriptions from a saved library HTML page")
    substack_library_parser.add_argument("path", type=Path)
    substack_library_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    substack_library_parser.add_argument("--group", default="")

    soundcloud_following_parser = subparsers.add_parser("import-soundcloud-following", help="Import followed SoundCloud profiles as public profile RSS feeds")
    soundcloud_following_parser.add_argument("url")
    soundcloud_following_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    soundcloud_following_parser.add_argument("--group", default="")

    soundcloud_profile_parser = subparsers.add_parser("subscribe-soundcloud-profile", help="Add or reactivate one public SoundCloud profile as an RSS feed, independent of anyone's following list")
    soundcloud_profile_parser.add_argument("url")
    soundcloud_profile_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    soundcloud_profile_parser.add_argument("--group", default="")

    nts_show_parser = subparsers.add_parser("subscribe-nts-show", help="Generate and subscribe to a local RSS feed for an NTS show page")
    nts_show_parser.add_argument("url")
    nts_show_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    nts_show_parser.add_argument("--group", default="")
    nts_show_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    webpage_parser = subparsers.add_parser(
        "subscribe-webpage-feed",
        help="Generate RSS from a public page covered by a registered webpage recipe",
    )
    webpage_parser.add_argument("url")
    webpage_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    webpage_parser.add_argument("--group", default="")
    webpage_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    mixcloud_parser = subparsers.add_parser("subscribe-mixcloud-profile", help="Generate and subscribe to RSS from a public Mixcloud profile")
    mixcloud_parser.add_argument("url")
    mixcloud_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    mixcloud_parser.add_argument("--group", default="")
    mixcloud_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    bandcamp_local_parser = subparsers.add_parser("refresh-bandcamp-local-feeds", help="Generate local RSS files for all saved Bandcamp artist and fan sources")
    bandcamp_local_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    bandcamp_local_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))
    bandcamp_local_parser.add_argument("--fan-max-items", type=int, default=40, help="Maximum items to fetch for followed fan collection feeds")
    bandcamp_local_parser.add_argument("--max-items", type=int, default=50, help="Maximum RSS items retained for any Bandcamp source")
    bandcamp_local_parser.add_argument(
        "--full-fan-source-id",
        action="append",
        default=[],
        help="Fan source ID to refresh without the fan item cap; can be passed multiple times",
    )
    bandcamp_local_parser.add_argument("--show-sensitive", action="store_true")
    bandcamp_local_parser.add_argument(
        "--pause-seconds", type=float, default=1.0, help="Delay between Bandcamp requests, so a large batch doesn't hammer their servers"
    )
    bandcamp_local_parser.add_argument(
        "--save-every", type=int, default=20, help="Write progress to disk every N sources, so a long run doesn't lose work if interrupted"
    )

    generated_local_parser = subparsers.add_parser(
        "refresh-generated-local-feeds",
        help="Regenerate saved NTS, Mixcloud, and registered webpage-recipe RSS files",
    )
    generated_local_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    generated_local_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))
    generated_local_parser.add_argument("--show-sensitive", action="store_true")
    generated_local_parser.add_argument(
        "--pause-seconds", type=float, default=1.0, help="Delay between requests, so a large batch doesn't hammer these sites"
    )
    generated_local_parser.add_argument(
        "--save-every", type=int, default=20, help="Write progress to disk every N sources, so a long run doesn't lose work if interrupted"
    )

    bandcamp_following_parser = subparsers.add_parser(
        "import-bandcamp-following",
        help="Import everyone a Bandcamp fan profile follows: artists/labels and other fans, queued for local RSS generation",
    )
    bandcamp_following_parser.add_argument("url")
    bandcamp_following_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    bandcamp_following_parser.add_argument("--group", default="")
    bandcamp_following_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    bandcamp_source_parser = subparsers.add_parser("subscribe-bandcamp-source", help="Add or reactivate a Bandcamp artist/label or fan source and generate its local RSS")
    bandcamp_source_parser.add_argument("url")
    bandcamp_source_parser.add_argument("--title", default="")
    bandcamp_source_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    bandcamp_source_parser.add_argument("--group", default="")
    bandcamp_source_parser.add_argument("--source-type", choices=["auto", "artist", "fan"], default="auto")
    bandcamp_source_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))
    bandcamp_source_parser.add_argument("--fan-max-items", type=int, default=40)
    bandcamp_source_parser.add_argument("--max-items", type=int, default=50, help="Maximum RSS items retained for this source")
    bandcamp_source_parser.add_argument("--no-refresh", action="store_true", help="Only update registry metadata; do not fetch Bandcamp or write local RSS")
    bandcamp_source_parser.add_argument("--show-sensitive", action="store_true")

    podcast_parser = subparsers.add_parser("subscribe-podcast", help="Subscribe to a podcast from an RSS URL or Apple Podcasts URL")
    podcast_parser.add_argument("url")
    podcast_parser.add_argument("--title", default="")
    podcast_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    podcast_parser.add_argument("--group", default="")
    podcast_parser.add_argument("--private", action="store_true", help="Write to the local gitignored private source overlay")

    batch_parser = subparsers.add_parser(
        "batch-subscribe",
        help="Subscribe to several mixed-type URLs in one run, from a file, standard input, or repeated --url flags",
        description=(
            "Subscribe to several public sources in one run.\n"
            "\n"
            "Each line of the batch file holds one URL. Blank lines, lines starting with\n"
            '"#", and a trailing "#" comment are ignored. A line may end with\n'
            "--adapter=<adapter> to force one adapter instead of the detected one:\n"
            "\n"
            "  # music\n"
            "  https://artist.bandcamp.com/\n"
            "  https://www.youtube.com/@example\n"
            "  https://publisher.example/feed.xml  --adapter=podcast\n"
            "\n"
            "Detected automatically: bandcamp.com, youtube.com channel pages,\n"
            "soundcloud.com, substack.com, mixcloud.com, nts.live show pages, and any page\n"
            "already covered by a registered webpage recipe. Anything else goes through\n"
            "public feed discovery, the same as subscribe-feed-url.\n"
            "\n"
            "Forceable with --adapter=:\n"
            f"  {', '.join(sorted(BATCH_ADAPTER_COMMANDS))}\n"
            "\n"
            "Each URL is dispatched to the single-URL command it belongs to, so folder\n"
            "defaults, duplicate checks, and host allowlists behave exactly as they do\n"
            "when those commands are run by hand. --group applies to every URL in the\n"
            "batch; omit it to let each adapter apply its own default folder per URL.\n"
            "The registry is chosen by --profile and the top-level --data, as usual.\n"
            "\n"
            "URLs are processed in order, one at a time. A URL that fails is reported and\n"
            "the run continues; the exit code is nonzero if any URL failed. A URL already\n"
            "in the registry is skipped and does not count as a failure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    batch_parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help='Batch file of URLs, or "-" to read standard input. Omit when using --url.',
    )
    batch_parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="One URL to subscribe. Repeatable, for a short batch without creating a file.",
    )
    batch_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    batch_parser.add_argument(
        "--group",
        default="",
        help='Folder for every URL in the batch. Omit for each adapter\'s own default; pass "" for the OPML root.',
    )
    batch_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))
    batch_parser.add_argument(
        "--pause-seconds",
        type=float,
        default=1.0,
        help="Delay between URLs, so a large batch doesn't hammer these sites",
    )
    batch_parser.add_argument("--show-sensitive", action="store_true")

    status_parser = subparsers.add_parser("set-status", help="Set a source status")
    status_parser.add_argument("source_id")
    status_parser.add_argument("--status", required=True, choices=["active", "candidate", "paused", "unsubscribed"])
    status_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    status_parser.add_argument("--reason", default="")

    folder_parser = subparsers.add_parser("set-folder", help="Set one ordered OPML folder path for a source")
    folder_parser.add_argument("identifier", help="Exact source ID, site URL, feed URL, or unique title")
    folder_parser.add_argument("folders", nargs="*", help="Folder path, from top level to leaf; omit to place at OPML root")
    folder_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    folder_parser.add_argument("--private", action="store_true", help="Update the local gitignored private source overlay")

    unsubscribe_parser = subparsers.add_parser("unsubscribe", help="Remove one or more exact sources from RSS export intent")
    unsubscribe_parser.add_argument("identifiers", nargs="+", help="Exact source ID, site URL, feed URL, or unique title")
    unsubscribe_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    unsubscribe_parser.add_argument("--reason", default="")

    history_parser = subparsers.add_parser("list-history", help="List subscription-history entries")
    history_parser.add_argument("--status", default=None)
    history_parser.add_argument("--profile", default=None)
    history_parser.add_argument("--kind", default=None)
    history_parser.add_argument("--show-sensitive", action="store_true")

    unfollow_parser = subparsers.add_parser("unfollow-checklist", help="List upstream unfollows implied by subscription-history entries")
    unfollow_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    unfollow_parser.add_argument("--show-sensitive", action="store_true")

    history_status_parser = subparsers.add_parser("set-history-status", help="Set a subscription-history entry status")
    history_status_parser.add_argument("entry_id")
    history_status_parser.add_argument(
        "--status",
        required=True,
        choices=["rss_unsubscribed", "external_unfollow_needed", "external_unfollow_confirmed", "ignored"],
    )
    history_status_parser.add_argument("--profile", default=DEFAULT_PROFILE)

    reconcile_parser = subparsers.add_parser("reconcile-netnewswire", help="Compare a NetNewsWire OPML export against repo intent")
    reconcile_parser.add_argument("path", type=Path)
    reconcile_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    reconcile_parser.add_argument("--apply", action="store_true", help="Mark missing active repo sources as unsubscribed")
    reconcile_parser.add_argument("--reason", default="Missing from latest NetNewsWire export")
    reconcile_parser.add_argument("--show-sensitive", action="store_true")

    verify_nnw_parser = subparsers.add_parser("verify-netnewswire", help="Verify a live NetNewsWire OPML matches the hosted repo export")
    verify_nnw_parser.add_argument("path", type=Path, help="Path to NetNewsWire's active Subscriptions.opml")
    verify_nnw_parser.add_argument("--expected", type=Path, required=True, help="Path to the expected hosted OPML export")
    verify_nnw_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    verify_nnw_parser.add_argument("--show-sensitive", action="store_true")

    discover_feed_parser = subparsers.add_parser("discover-feed", help="Discover a page's RSS, Atom, or JSON Feed URL from alternate links")
    discover_feed_parser.add_argument("url")

    audit_parser = subparsers.add_parser("audit-sources", help="Fetch active sources and report RSS/Atom/JSON Feed validation status")
    audit_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    audit_parser.add_argument("--status", default="active")
    audit_parser.add_argument("--kind", default=None)
    audit_parser.add_argument("--limit", type=int, default=0, help="Limit sources audited; useful for quick spot checks")
    audit_parser.add_argument("--show-sensitive", action="store_true")

    refresh_plan_parser = subparsers.add_parser(
        "refresh-plan",
        help="Show whether generated-source volume fits the hosted refresh settings",
    )
    refresh_plan_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    refresh_plan_parser.add_argument(
        "--refresh-interval-hours",
        type=int,
        default=max(1, int(os.environ.get("RSS_REFRESH_INTERVAL_SECONDS", str(DEFAULT_REFRESH_INTERVAL_SECONDS))) // 3600),
    )
    refresh_plan_parser.add_argument(
        "--schedule-hours",
        type=int,
        default=int(os.environ.get("RSS_REFRESH_SCHEDULE_HOURS", str(DEFAULT_REFRESH_SCHEDULE_HOURS))),
    )
    refresh_plan_parser.add_argument(
        "--max-sources-per-run",
        type=int,
        default=int(os.environ.get("RSS_MAX_REFRESH_SOURCES_PER_RUN", str(DEFAULT_MAX_REFRESH_SOURCES_PER_RUN))),
    )

    return parser


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", required=True)
    parser.add_argument("--feed-url", required=True)
    parser.add_argument("--site-url", default="")
    parser.add_argument("--kind", default="website", choices=["website", "substack", "youtube", "bandcamp", "newsletter", "podcast", "other"])
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--status", default="active", choices=["active", "candidate", "paused", "unsubscribed"])
    parser.add_argument("--notes", default="")


def _find_feed_identity_duplicate(
    existing_sources: list[Source],
    incoming: Source,
    incoming_identity: FeedIdentity,
) -> tuple[Optional[Source], str, bool]:
    possible: Optional[Source] = None
    incoming_feed_url = canonical_url(incoming.feed_url)
    incoming_site_url = canonical_url(incoming.site_url)
    for existing in existing_sources:
        if existing.kind != incoming.kind:
            continue
        if incoming_feed_url and canonical_url(existing.feed_url) == incoming_feed_url:
            return existing, "same normalized feed URL", False
        if incoming_site_url and canonical_url(existing.site_url) == incoming_site_url:
            return existing, "same canonical publication URL", False
        if not likely_same_title(
            incoming_identity,
            FeedIdentity(existing.title, "", "", frozenset()),
        ):
            continue
        possible = possible or existing
        try:
            existing_identity = parse_feed_identity(
                _read_identity_source(existing.feed_url),
                fallback_url=existing.feed_url,
            )
        except Exception:
            continue
        reason = identity_match_reason(incoming_identity, existing_identity)
        if reason:
            return existing, reason, False
    return possible, "", possible is not None


# Batch targets that write a generated RSS seed, and so accept --out-dir. The rest
# take only the URL, --profile, and --group.
BATCH_OUT_DIR_COMMANDS = frozenset(
    {
        "subscribe-bandcamp-source",
        "subscribe-mixcloud-profile",
        "subscribe-nts-show",
        "subscribe-webpage-feed",
    }
)


def _batch_registered_source(sources: list[Source], url: str) -> Optional[Source]:
    """Find a source already standing for this URL, before spending a fetch on it.

    Only an exact canonical match counts. The per-adapter commands still run their
    own richer duplicate checks; this is the cheap pass that keeps a re-run of the
    same batch file from refetching every page it already holds.
    """

    target = canonical_url(normalize_url(url))
    if not target:
        return None
    return next(
        (
            source
            for source in sources
            if canonical_url(source.site_url) == target or canonical_url(source.feed_url) == target
        ),
        None,
    )


def _read_identity_source(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(parsed.path).read_text(encoding="utf-8", errors="replace")
    return fetch_text(url)


def normalize_legacy_webpage_command(argv: Optional[list[str]]) -> list[str]:
    """Keep the v0.1 HydeFM shortcut working without presenting it as a feature."""

    normalized = list(sys.argv[1:] if argv is None else argv)
    try:
        command_index = normalized.index("subscribe-hydefm-archive")
    except ValueError:
        return normalized

    normalized[command_index] = "subscribe-webpage-feed"
    site_url = HYDEFM_ARCHIVE_RECIPE.default_url
    url_flag_index = next(
        (
            index
            for index in range(command_index + 1, len(normalized))
            if normalized[index] == "--url" or normalized[index].startswith("--url=")
        ),
        None,
    )
    if url_flag_index is not None:
        url_argument = normalized[url_flag_index]
        if url_argument.startswith("--url="):
            site_url = url_argument.split("=", 1)[1]
            del normalized[url_flag_index]
        else:
            if url_flag_index + 1 >= len(normalized):
                return normalized
            site_url = normalized[url_flag_index + 1]
            del normalized[url_flag_index : url_flag_index + 2]
    normalized.insert(command_index + 1, site_url)
    return normalized


def _option_was_supplied(argv: list[str], option: str) -> bool:
    return any(argument == option or argument.startswith(f"{option}=") for argument in argv)


def _apply_default_group(args: argparse.Namespace, argv: list[str]) -> None:
    """Choose a folder for a new source when the caller named none.

    Resolution order: an explicit --group, then RSS_DEFAULT_GROUP_<KEY>, then a
    built-in platform folder. Independent sites have no built-in folder, so they
    stay at the OPML root. Silently landing at the root is what scattered feeds
    across the top level, so every outcome here is announced. An explicit --group
    always wins, including --group "" for the root.
    """

    if not hasattr(args, "group") or _option_was_supplied(argv, "--group"):
        return

    key = ADAPTER_KEY_BY_COMMAND.get(args.command) or ADAPTER_KEY_BY_KIND.get(getattr(args, "kind", ""))
    if key is None:
        return

    variable = f"RSS_DEFAULT_GROUP_{key}"
    group = os.environ.get(variable, "").strip()
    if group:
        origin = f"set by {variable}"
    elif key in PLATFORM_GROUPS:
        group = PLATFORM_GROUPS[key]
        origin = f"the default folder for {group} sources"
    else:
        print(
            f"No --group given and {variable} is not set, so this source stays at the OPML root. "
            f'This source is an independent site rather than a platform, so only you can say which '
            f'folder it belongs in: pass --group "Your Folder", or set {variable} to file every '
            f"later one automatically.",
            file=sys.stderr,
        )
        return

    if isinstance(args.group, list):
        args.group = [group]
    else:
        args.group = group
    print(
        f'No --group given; filing this source under "{group}" ({origin}). '
        'Pass --group "Your Folder" to choose another, or --group "" for the OPML root.',
        file=sys.stderr,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    normalized_argv = normalize_legacy_webpage_command(argv)
    profile_was_explicit = _option_was_supplied(normalized_argv, "--profile")
    args = parser.parse_args(normalized_argv)
    _apply_default_group(args, normalized_argv)
    profile = getattr(args, "profile", None) or os.environ.get("RSS_PROFILE", "")
    data_was_explicit = args.data is not None
    if args.data is None:
        try:
            args.data = default_sources_path(
                profile,
                prefer_configured=not profile_was_explicit,
            )
        except ValueError as error:
            parser.error(str(error))
    if args.history is None:
        try:
            args.history = default_subscription_history_path(
                profile,
                prefer_configured=not profile_was_explicit,
            )
        except ValueError as error:
            parser.error(str(error))
    if (
        profile
        and not data_was_explicit
        and args.command != "discover-feed"
        and not args.data.exists()
    ):
        parser.error(
            f"Missing private profile files for RSS_PROFILE={profile}. "
            f"Run ./scripts/bootstrap_profile.sh {profile} first."
        )
    store = FeedStore(args.data)
    private_store = FeedStore(args.private_data)
    history_store = SubscriptionHistoryStore(args.history)

    if args.command == "import-opml":
        sources = parse_opml(args.path, profile=args.profile)
        store.add_or_update_many(sources)
        store.save()
        print(f"Imported {len(sources)} sources.")
        return 0

    if args.command == "migrate-generated-sources":
        migration = plan_generated_source_migration(
            FeedStore(args.reference),
            store,
            profile=args.profile,
            bandcamp_out_dir=args.bandcamp_out_dir,
            generated_out_dir=args.generated_out_dir,
        )
        print(f"Generated sources found in reference: {sum(migration.source_counts.values())}")
        for source_label, count in migration.source_counts.items():
            print(f"{source_label}: {count}")
        print(f"Would replace legacy imported feeds: {len(migration.replacements)}")
        print(f"Would add missing generated feeds: {len(migration.additions)}")
        print(f"Would remove duplicate legacy feeds: {len(migration.removals)}")
        print(f"Conflicts: {len(migration.conflicts)}")
        for conflict in migration.conflicts:
            print(f"CONFLICT\t{conflict}")
        if migration.conflicts:
            return 1
        if not args.apply:
            print("Dry run only. Re-run with --apply to write rebuilt generated-source metadata.")
            return 0
        apply_generated_source_migration(store, migration)
        print(
            "Applied generated-source migration: "
            f"rebuilt {migration.total}, removed duplicates {len(migration.removals)}"
        )
        return 0

    if args.command == "export-opml":
        sources = active_sources_with_private(store, private_store, args.profile)
        if args.bandcamp_feed_base:
            if not args.bandcamp_feed_token:
                parser.error("--bandcamp-feed-token is required when --bandcamp-feed-base is set")
            sources = sources_with_hosted_generated_feeds(sources, args.bandcamp_feed_base, token=args.bandcamp_feed_token)
        title = args.title or f"netnewswire-feed-booster: {args.profile}"
        write_opml(args.out, sources, title=title)
        print(f"Exported {len(sources)} active sources.")
        return 0

    if args.command == "list":
        sources = filtered_sources_with_private(store, private_store, profile=args.profile, status=args.status, kind=args.kind)
        for source in sources:
            groups = ", ".join(source.groups) if source.groups else "-"
            feed_url = display_feed_url(source, args.show_sensitive)
            print(f"{source.id}\t{source.status}\t{source.kind}\t{groups}\t{source.title}\t{feed_url}")
        print(f"{len(sources)} sources")
        return 0

    if args.command == "add":
        feed_url = normalize_url(args.feed_url)
        source = Source(
            id=source_id_from_title(args.title, feed_url),
            title=args.title,
            feed_url=feed_url,
            site_url=normalize_url(args.site_url) if args.site_url else "",
            kind=args.kind,
            profiles=[args.profile],
            groups=args.group,
            status=args.status,
            notes=args.notes,
            source="manual",
        )
        source_id = store.add_or_update(source)
        store.save()
        print(f"Saved source {source_id}")
        return 0

    if args.command == "subscribe-feed-url":
        feed_url = discover_feed_url(args.url)
        incoming_text = fetch_text(feed_url)
        incoming_identity = parse_feed_identity(incoming_text, fallback_url=feed_url)
        title = args.title.strip() or incoming_identity.title or args.url
        kind = args.kind if args.kind != "auto" else incoming_identity.provider or "website"
        incoming = Source(
            id=source_id_from_title(title, feed_url),
            title=title,
            feed_url=feed_url,
            site_url=incoming_identity.home_url or normalize_url(args.url),
            kind=kind,
            profiles=[args.profile],
            groups=args.group,
            status="active",
            source="public-feed-discovery",
        )
        stored_sources = store.sources()
        identity_sources = stored_sources + private_store.sources()
        if args.against_opml is not None:
            if not args.against_opml.is_file():
                parser.error(f"Missing NetNewsWire OPML: {args.against_opml}")
            identity_sources.extend(parse_opml(args.against_opml, profile=args.profile))
        duplicate, reason, possible = _find_feed_identity_duplicate(identity_sources, incoming, incoming_identity)
        if duplicate and reason:
            stored_duplicate = next(
                (
                    source
                    for source in stored_sources
                    if source.id == duplicate.id and source.feed_url == duplicate.feed_url
                ),
                None,
            )
            if stored_duplicate is not None and stored_duplicate.status != "active":
                store.set_status(stored_duplicate.id, "active")
                store.save()
                print(f"Reactivated {stored_duplicate.id}: {reason}")
            else:
                print(f"Already subscribed as {duplicate.id}: {reason}")
            return 0
        if duplicate and possible and not args.allow_possible_duplicate:
            print(
                f"Possible duplicate of {duplicate.id}: same normalized title. "
                "Re-run with --allow-possible-duplicate only after confirming they are separate.",
                file=sys.stderr,
            )
            return 1
        source_id = store.add_or_update(incoming)
        store.set_status(source_id, "active")
        store.save()
        print(f"Subscribed public feed {source_id}.")
        return 0

    if args.command == "subscribe-substack":
        domain = args.domain_or_url.replace("https://", "").replace("http://", "").strip("/")
        title = args.title or domain.replace(".substack.com", "").replace("www.", "")
        source = Source(
            id=source_id_from_title(title, domain),
            title=title,
            feed_url=f"https://{domain}/feed",
            site_url=f"https://{domain}",
            kind="substack",
            profiles=[args.profile],
            groups=[args.group],
            status="active",
            notes=args.notes,
            source="manual-oneoff",
        )
        source_id = store.add_or_update(source)
        store.set_status(source_id, "active")
        store.save()
        print(f"Subscribed Substack source {source_id}.")
        return 0

    if args.command == "subscribe-youtube":
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={args.channel_id}"
        source = Source(
            id=source_id_from_title(args.title, args.channel_id),
            title=args.title,
            feed_url=feed_url,
            site_url=f"https://www.youtube.com/channel/{args.channel_id}",
            kind="youtube",
            profiles=[args.profile],
            groups=[args.group],
            status="active",
            notes=args.notes,
            source="manual-oneoff",
        )
        source_id = store.add_or_update(source)
        store.set_status(source_id, "active")
        store.save()
        print(f"Subscribed YouTube source {source_id}.")
        return 0

    if args.command == "import-youtube-channel-url":
        html = fetch_text(args.url, allowed_hosts=YOUTUBE_HOSTS)
        source = parse_youtube_channel_html(html, profile=args.profile, group=args.group, fallback_title=args.title)
        source_id = store.add_or_update(source)
        store.save()
        print(f"Saved YouTube source {source_id}.")
        return 0

    if args.command == "import-youtube-subscriptions":
        sources = parse_youtube_subscriptions_file(args.path, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} YouTube subscriptions.")
        return 0

    if args.command == "import-substack-profile":
        html = fetch_text(args.url, allowed_suffixes=SUBSTACK_ALLOWED_SUFFIXES)
        sources = parse_substack_profile_html(html, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} public Substack subscriptions.")
        return 0

    if args.command == "import-substack-library":
        html = args.path.read_text(encoding="utf-8", errors="replace")
        sources = parse_substack_library_html(html, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} Substack library subscriptions.")
        return 0

    if args.command == "import-soundcloud-following":
        sources = fetch_soundcloud_following_sources(args.url, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} SoundCloud following sources.")
        return 0

    if args.command == "subscribe-soundcloud-profile":
        source = fetch_soundcloud_profile_source(args.url, profile=args.profile, group=args.group)
        source_id = store.add_or_update(source)
        store.set_status(source_id, "active")
        store.save()
        print(f"Subscribed SoundCloud source {source_id}.")
        return 0

    if args.command == "subscribe-nts-show":
        candidate = Source(
            id="nts-source",
            title="NTS",
            feed_url="generated",
            site_url=args.url.rstrip("/"),
            kind="other",
            source="nts-local-generated",
        )
        NTS_ADAPTER.validate(candidate)
        html = fetch_text(
            NTS_ADAPTER.upstream_url(candidate),
            allowed_hosts=NTS_ADAPTER.allowed_hosts,
            allowed_suffixes=NTS_ADAPTER.allowed_suffixes,
        )
        title, _, _ = parse_nts_show_html(html, args.url)
        source_id = slugify(f"NTS {title}")
        out_path = args.out_dir / f"{source_id}.rss"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(NTS_ADAPTER.render(candidate, html), encoding="utf-8")
        source = Source(
            id=source_id,
            title=f"NTS: {title}",
            feed_url=out_path.resolve().as_uri(),
            site_url=args.url.rstrip("/"),
            kind="other",
            profiles=[args.profile],
            groups=[args.group],
            source="nts-local-generated",
            notes="Generated local RSS feed from the NTS show page because NTS does not expose a first-party RSS feed.",
        )
        changed_id = store.add_or_update(source)
        store.set_status(changed_id, "active")
        store.save()
        print(f"Subscribed NTS show {changed_id}.")
        return 0

    if args.command == "subscribe-webpage-feed":
        site_url = args.url.strip()
        recipe = require_webpage_recipe(site_url)
        candidate = Source(
            id=slugify(f"{recipe.source_id_prefix} {recipe.name}"),
            title=recipe.name,
            feed_url="generated",
            site_url=site_url,
            kind="other",
            source=WEBPAGE_ADAPTER.source_label,
        )
        WEBPAGE_ADAPTER.validate(candidate)
        content = fetch_text(
            WEBPAGE_ADAPTER.upstream_url(candidate),
            allowed_hosts=WEBPAGE_ADAPTER.allowed_hosts_for(candidate),
            allowed_suffixes=WEBPAGE_ADAPTER.allowed_suffixes_for(candidate),
        )
        parsed_feed = parse_webpage_feed(recipe, content, candidate.site_url)
        source_id = slugify(f"{recipe.source_id_prefix} {parsed_feed.title}")
        out_path = args.out_dir / f"{source_id}.rss"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(WEBPAGE_ADAPTER.render(candidate, content), encoding="utf-8")
        source = Source(
            id=source_id,
            title=parsed_feed.title,
            feed_url=out_path.resolve().as_uri(),
            site_url=site_url,
            kind="other",
            profiles=[args.profile],
            groups=[args.group],
            source=WEBPAGE_ADAPTER.source_label,
            notes=(
                f"Generated local RSS from the public {recipe.name} page "
                "using a reviewed, allowlisted webpage recipe."
            ),
        )
        changed_id = store.add_or_update(source)
        store.set_status(changed_id, "active")
        store.save()
        print(f"Subscribed webpage feed {changed_id} with recipe {recipe.id}.")
        return 0

    if args.command == "subscribe-mixcloud-profile":
        source = mixcloud_source(args.url, profile=args.profile, group=args.group)
        adapter = adapter_for_source(source)
        if adapter is None:
            raise ValueError(f"Unsupported generated source: {source.site_url}")
        adapter.validate(source)
        content = fetch_text(
            adapter.upstream_url(source),
            allowed_hosts=adapter.allowed_hosts_for(source),
            allowed_suffixes=adapter.allowed_suffixes_for(source),
        )
        rss = adapter.render(source, content)
        out_path = args.out_dir / f"{source.id}.rss"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rss, encoding="utf-8")
        source.feed_url = out_path.resolve().as_uri()
        changed_id = store.add_or_update(source)
        store.set_status(changed_id, "active")
        store.save()
        print(f"Subscribed Mixcloud profile {changed_id}.")
        return 0

    if args.command == "refresh-bandcamp-local-feeds":
        updated = 0
        failed = 0
        sources = store.sources()
        full_fan_source_ids = set(args.full_fan_source_id or [])
        indexed_sources = list(enumerate(sources))
        bandcamp_sources = [
            (index, source)
            for index, source in indexed_sources
            if source.kind == "bandcamp" and source.status == "active" and args.profile in source.profiles
        ]
        bandcamp_sources.sort(key=lambda indexed_source: (indexed_source[1].id not in full_fan_source_ids, indexed_source[0]))

        processed = 0
        for _, source in bandcamp_sources:
            if source.source == "bandcamp-generated-music-feed":
                continue

            try:
                try:
                    html = fetch_text(
                        bandcamp_fetch_url(source),
                        allowed_hosts=BANDCAMP_ADAPTER.allowed_hosts_for(source),
                        allowed_suffixes=BANDCAMP_ADAPTER.allowed_suffixes_for(source),
                    )
                    items = bandcamp_items_for_source(
                        source,
                        html,
                        fan_max_items=args.fan_max_items,
                        full_fan_source_ids=full_fan_source_ids,
                        max_items=args.max_items,
                    )
                    if not items:
                        failed += 1
                        detail = source.site_url if args.show_sensitive else "[details redacted; use --show-sensitive]"
                        print(f"FAILED\t{source.id}\tNo items found\t{detail}")
                        continue

                    out_path = args.out_dir / f"{source.id}.rss"
                    write_bandcamp_collection_rss(out_path, profile_url=source.site_url, title=source.title, items=items)
                    source.feed_url = out_path.resolve().as_uri()
                    source.source = "bandcamp-local-generated"
                    source.notes = "Generated local RSS feed from the saved Bandcamp source page because OpenRSS did not mirror this Bandcamp feed reliably."
                    updated += 1
                    print(f"UPDATED\t{source.id}\t{len(items)} items")
                except Exception as error:
                    failed += 1
                    detail = (
                        f"{type(error).__name__}: {error}\t{source.site_url}"
                        if args.show_sensitive
                        else f"{type(error).__name__}\t[details redacted; use --show-sensitive]"
                    )
                    print(f"FAILED\t{source.id}\t{detail}")
            finally:
                processed += 1
                if args.save_every > 0 and processed % args.save_every == 0:
                    store.set_sources(sources)
                    store.save()
                    print(f"...progress saved ({updated} updated, {failed} failed so far)")
                if args.pause_seconds > 0:
                    time.sleep(args.pause_seconds)

        store.set_sources(sources)
        store.save()
        print(f"Updated local Bandcamp feeds: {updated}")
        print(f"Failed local Bandcamp feeds: {failed}")
        return 0

    if args.command == "refresh-generated-local-feeds":
        updated = 0
        failed = 0
        processed = 0
        sources = store.sources()
        for source in sources:
            if source.status != "active" or args.profile not in source.profiles:
                continue
            adapter = adapter_for_source(source)
            if adapter is None or adapter.hosted_route != "generated" or adapter is BANDCAMP_ADAPTER:
                # Bandcamp shares the "generated" route but keeps its own refresh
                # path (refresh-bandcamp-local-feeds) for fan-collection pagination.
                continue
            try:
                try:
                    adapter.validate(source)
                    content = fetch_text(
                        adapter.upstream_url(source),
                        allowed_hosts=adapter.allowed_hosts_for(source),
                        allowed_suffixes=adapter.allowed_suffixes_for(source),
                    )
                    rss = adapter.render(source, content)
                    out_path = args.out_dir / f"{source.id}.rss"
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(rss, encoding="utf-8")
                    source.feed_url = out_path.resolve().as_uri()
                    updated += 1
                    print(f"UPDATED\t{source.id}")
                except Exception as error:
                    failed += 1
                    detail = (
                        f"{type(error).__name__}: {error}\t{source.site_url}"
                        if args.show_sensitive
                        else f"{type(error).__name__}\t[details redacted; use --show-sensitive]"
                    )
                    print(f"FAILED\t{source.id}\t{detail}")
            finally:
                processed += 1
                if args.save_every > 0 and processed % args.save_every == 0:
                    store.set_sources(sources)
                    store.save()
                    print(f"...progress saved ({updated} updated, {failed} failed so far)")
                if args.pause_seconds > 0:
                    time.sleep(args.pause_seconds)
        store.set_sources(sources)
        store.save()
        print(f"Updated local generated feeds: {updated}")
        print(f"Failed local generated feeds: {failed}")
        return 0

    if args.command == "import-bandcamp-following":
        sources = import_bandcamp_following(args.url, profile=args.profile, group=args.group, out_dir=args.out_dir)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} Bandcamp following sources.")
        print("Their RSS isn't generated yet — run refresh-bandcamp-local-feeds to populate it.")
        return 0

    if args.command == "subscribe-bandcamp-source":
        source = build_bandcamp_source_from_url(
            args.url,
            title=args.title,
            profile=args.profile,
            group=args.group,
            source_type=args.source_type,
            out_dir=args.out_dir,
        )

        if not args.no_refresh:
            rss = render_bandcamp_source_rss(
                source,
                fetcher=lambda url: fetch_text(
                    url,
                    allowed_hosts=BANDCAMP_ADAPTER.allowed_hosts_for(source),
                    allowed_suffixes=BANDCAMP_ADAPTER.allowed_suffixes_for(source),
                ),
                fan_max_items=args.fan_max_items,
                full_fan_source_ids=set(),
                max_items=args.max_items,
            )
            out_path = args.out_dir / f"{source.id}.rss"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rss, encoding="utf-8")
            source.feed_url = out_path.resolve().as_uri()
            print(f"Generated local Bandcamp RSS for {source.id}.")

        source_id = store.add_or_update(source)
        store.set_status(source_id, "active")
        store.save()
        print(f"Subscribed Bandcamp source {source_id}.")
        return 0

    if args.command == "subscribe-podcast":
        target_store = private_store if args.private else store
        source = podcast_source_from_url(args.url, title=args.title, profile=args.profile, group=args.group)
        source_id = target_store.add_or_update(source)
        target_store.set_status(source_id, "active")
        target_store.save()
        destination = "private overlay" if args.private else "profile registry"
        print(f"Subscribed podcast source {source_id} into the {destination}.")
        return 0

    if args.command == "batch-subscribe":
        if args.path is not None and args.url:
            parser.error("Pass a batch file or --url flags, not both.")
        if args.path is None and not args.url:
            parser.error('Pass a batch file path, "-" for standard input, or at least one --url.')

        try:
            if args.url:
                batch_lines = parse_batch_lines("\n".join(args.url))
            elif str(args.path) == "-":
                batch_lines = parse_batch_lines(sys.stdin.read())
            else:
                if not args.path.is_file():
                    parser.error(f"Missing batch file: {args.path}")
                batch_lines = parse_batch_lines(args.path.read_text(encoding="utf-8"))
        except ValueError as error:
            parser.error(str(error))

        # An explicit --group applies to the whole batch. Without one, each URL is
        # dispatched with no --group at all, so the target command's own
        # _apply_default_group picks the folder for that adapter — which is the only
        # way a mixed batch can land Bandcamp and YouTube in their own folders.
        group_was_supplied = _option_was_supplied(normalized_argv, "--group")
        succeeded: list[str] = []
        batch_skipped: list[tuple[str, str]] = []
        batch_failed: list[tuple[str, str]] = []

        for line in batch_lines:
            adapter = line.adapter or detect_batch_adapter(line.url)
            command = BATCH_ADAPTER_COMMANDS[adapter]
            before_sources = FeedStore(args.data).sources()

            already_registered = _batch_registered_source(before_sources, line.url)
            if already_registered is not None:
                batch_skipped.append((line.url, f"already subscribed as {already_registered.id}"))
                print(f"SKIPPED\t{line.url}\t{adapter}\t{already_registered.id}\talready subscribed")
                continue

            child_argv = [
                "--data",
                str(args.data),
                "--private-data",
                str(args.private_data),
                "--history",
                str(args.history),
                command,
                line.url,
                "--profile",
                args.profile,
            ]
            if group_was_supplied:
                child_argv += ["--group", args.group]
            if command in BATCH_OUT_DIR_COMMANDS:
                child_argv += ["--out-dir", str(args.out_dir)]

            # The target command owns the user-facing message for one URL; a batch
            # needs one line per URL instead, so its output is captured and only
            # surfaced when it explains a failure.
            child_output = io.StringIO()
            child_result = 0
            child_error: Optional[BaseException] = None
            try:
                with redirect_stdout(child_output), redirect_stderr(child_output):
                    child_result = main(child_argv)
            except SystemExit as error:
                child_result = int(error.code or 0)
            except Exception as error:  # one bad URL must not end the batch
                child_error = error

            if child_error is not None:
                detail = (
                    f"{type(child_error).__name__}: {child_error}"
                    if args.show_sensitive
                    else f"{type(child_error).__name__}\t[details redacted; use --show-sensitive]"
                )
                batch_failed.append((line.url, detail))
                print(f"FAILED\t{line.url}\t{adapter}\t{detail}")
            elif child_result != 0:
                reported = child_output.getvalue().strip().splitlines()
                detail = reported[-1].strip() if reported else f"{command} exited {child_result}"
                batch_failed.append((line.url, detail))
                print(f"FAILED\t{line.url}\t{adapter}\t{detail}")
            else:
                after_sources = FeedStore(args.data).sources()
                before_ids = {source.id for source in before_sources}
                added = [source for source in after_sources if source.id not in before_ids]
                if added:
                    folders = " / ".join(added[0].groups) or "OPML root"
                    succeeded.append(line.url)
                    print(f"OK\t{line.url}\t{adapter}\t{added[0].id}\t{folders}")
                else:
                    # The command reported success without adding a row, so it
                    # recognized the URL as one already held.
                    existing = _batch_registered_source(after_sources, line.url)
                    label = existing.id if existing is not None else "already present"
                    batch_skipped.append((line.url, f"already subscribed as {label}"))
                    print(f"SKIPPED\t{line.url}\t{adapter}\t{label}\talready subscribed")

            if args.pause_seconds > 0:
                time.sleep(args.pause_seconds)

        print(
            f"{len(batch_lines)} processed, {len(succeeded)} succeeded, "
            f"{len(batch_skipped)} skipped, {len(batch_failed)} failed"
        )
        if batch_failed:
            print("Re-run these after fixing them:")
            for url, detail in batch_failed:
                print(f"FAILED\t{url}\t{detail}")
        return 1 if batch_failed else 0

    if args.command == "set-status":
        source = store.set_status(args.source_id, args.status)
        if args.status == "unsubscribed":
            history_store.record_rss_unsubscribe(source, profile=args.profile, reason=args.reason)
            history_store.save()
        store.save()
        print(f"Set {source.id} to {source.status}")
        return 0

    if args.command == "set-folder":
        target_store = private_store if args.private else store
        source = resolve_source_identifier(target_store.sources(), args.identifier, profile=args.profile)
        updated_source = target_store.set_folder_path(source.id, args.folders)
        target_store.save()
        location = " / ".join(updated_source.groups) or "OPML root"
        print(f"Set folder path for {updated_source.id}: {location}")
        return 0

    if args.command == "unsubscribe":
        resolved = [resolve_source_identifier(store.sources(), identifier, profile=args.profile) for identifier in args.identifiers]
        for source in resolved:
            updated_source = store.set_status(source.id, "unsubscribed")
            history_store.record_rss_unsubscribe(updated_source, profile=args.profile, reason=args.reason)
            print(f"Unsubscribed {updated_source.id}\t{updated_source.kind}\t{updated_source.title}")
        store.save()
        history_store.save()
        print(f"Unsubscribed {len(resolved)} sources")
        return 0

    if args.command == "list-history":
        entries = history_store.filtered(status=args.status, profile=args.profile, source_kind=args.kind)
        for entry in entries:
            location = entry.external_url or entry.feed_url
            if not args.show_sensitive:
                location = "[redacted; use --show-sensitive]"
            print(f"{entry.id}\t{entry.status}\t{entry.source_kind}\t{entry.profile}\t{entry.source_title}\t{location}")
        print(f"{len(entries)} subscription-history entries")
        return 0

    if args.command == "unfollow-checklist":
        entries = history_store.external_unfollow_candidates(profile=args.profile)
        for entry in entries:
            location = entry.external_url or entry.feed_url
            if not args.show_sensitive:
                location = "[redacted; use --show-sensitive]"
            print(
                f"{entry.id}\t{entry.source_kind}\t{entry.source_title}\t"
                f"{location}\t{entry.status}"
            )
        print(f"{len(entries)} upstream unfollows")
        return 0

    if args.command == "set-history-status":
        entry = history_store.set_status(args.entry_id, args.status)
        history_store.save()
        print(f"Set {entry.id} to {entry.status}")
        return 0

    if args.command == "reconcile-netnewswire":
        observed_sources = parse_opml(args.path, profile=args.profile)
        observed_feed_urls = {source.feed_url for source in observed_sources}
        known_feed_urls = {source.feed_url for source in store.sources()}
        missing_from_netnewswire = [
            source
            for source in store.active_sources(args.profile)
            if source.feed_url not in observed_feed_urls
        ]
        extra_in_netnewswire = [
            source
            for source in observed_sources
            if source.feed_url not in known_feed_urls
        ]

        print(f"Missing active repo sources from NetNewsWire: {len(missing_from_netnewswire)}")
        for source in missing_from_netnewswire:
            print(f"MISSING\t{source.id}\t{source.kind}\t{source.title}\t{display_feed_url(source, args.show_sensitive)}")

        print(f"NetNewsWire sources not in repo: {len(extra_in_netnewswire)}")
        for source in extra_in_netnewswire:
            print(f"EXTRA\t{source.kind}\t{source.title}\t{display_feed_url(source, args.show_sensitive)}")

        if args.apply:
            for source in missing_from_netnewswire:
                updated_source = store.set_status(source.id, "unsubscribed")
                history_store.record_rss_unsubscribe(updated_source, profile=args.profile, reason=args.reason)
            store.save()
            history_store.save()
            print(f"Applied {len(missing_from_netnewswire)} subscription-history entries")
        else:
            print("Dry run only. Re-run with --apply to record subscription-history entries.")
        return 0

    if args.command == "verify-netnewswire":
        current_sources = parse_opml(args.path, profile=args.profile)
        expected_sources = parse_opml(args.expected, profile=args.profile)
        drift = netnewswire_drift_report(
            current_sources,
            expected_sources,
            store.filtered(profile=args.profile, status="unsubscribed"),
        )
        print_drift_report(drift, show_sensitive=args.show_sensitive)
        return 1 if drift_has_failures(drift) else 0

    if args.command == "discover-feed":
        print(discover_feed_url(args.url))
        return 0

    if args.command == "audit-sources":
        sources = filtered_sources_with_private(store, private_store, profile=args.profile, status=args.status, kind=args.kind)
        if args.limit:
            sources = sources[: args.limit]
        results = audit_sources(sources)
        failures = [result for result in results if result.status != "ok"]
        for result in results:
            fields = [result.status, result.feed_type or "-", result.source_id, result.title]
            fields.append(result.url if args.show_sensitive else "[redacted; use --show-sensitive]")
            if result.discovered_url:
                discovered_url = result.discovered_url if args.show_sensitive else "[redacted; use --show-sensitive]"
                fields.append(f"discovered={discovered_url}")
            if result.detail and result.detail != "ok":
                fields.append(result.detail)
            print("\t".join(fields))
        print(f"Audited {len(results)} sources; failures: {len(failures)}")
        return 1 if failures else 0

    if args.command == "refresh-plan":
        # Bandcamp and the rest of the "generated" route are scheduled and batched
        # independently (see refresh_bandcamp_cache/refresh_generated_cache in
        # modal_bandcamp_app.py), so the capacity report still splits on adapter
        # identity even though both now share one hosted_route.
        route_counts: Counter[str] = Counter()
        direct_count = 0
        for source in store.active_sources(args.profile):
            adapter = adapter_for_source(source)
            if adapter is None:
                direct_count += 1
            elif adapter is BANDCAMP_ADAPTER:
                route_counts["bandcamp"] += 1
            else:
                route_counts["generated"] += 1

        print(f"Refresh plan for profile: {args.profile}")
        print(
            f"Generated feed target: every {args.refresh_interval_hours}h; "
            f"scheduler: every {args.schedule_hours}h; batch: {args.max_sources_per_run} sources"
        )
        print(f"Direct reader-managed feeds: {direct_count}")
        has_capacity_gap = False
        for route in ("bandcamp", "generated"):
            plan = refresh_route_plan(
                route,
                route_counts[route],
                args.max_sources_per_run,
                args.schedule_hours,
                args.refresh_interval_hours,
            )
            status = "fits" if plan.meets_target else "exceeds capacity"
            print(
                f"{route}: {plan.source_count} sources; first pass {plan.first_pass_hours}h; "
                f"capacity per target interval {plan.capacity_per_interval}; {status}"
            )
            has_capacity_gap = has_capacity_gap or not plan.meets_target
        if has_capacity_gap:
            print("Increase the refresh interval or lower the schedule interval before deploying.")
            return 1
        return 0

    parser.error("Unknown command")
    return 2
