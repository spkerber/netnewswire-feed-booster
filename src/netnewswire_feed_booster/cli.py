from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional
from .bandcamp import (
    write_bandcamp_collection_rss,
)
from .bandcamp_sources import build_bandcamp_source_from_url
from .subscription_history import SubscriptionHistoryStore, default_subscription_history_path
from .feed_store import FeedStore, Source, default_private_sources_path, default_sources_path, normalize_url, slugify
from .feed_validation import audit_sources, discover_feed_url
from .generated_migration import apply_generated_source_migration, plan_generated_source_migration
from .hosted_bandcamp import bandcamp_fetch_url, bandcamp_items_for_source, sources_with_hosted_bandcamp_feeds
from .hosted_bandcamp import render_bandcamp_source_rss
from .hydefm import DEFAULT_ARCHIVE_URL as HYDEFM_ARCHIVE_URL
from .hydefm import parse_hydefm_archive_markdown, render_hydefm_archive_rss, fetch_hydefm_archive_markdown
from .nts import parse_nts_show_html, render_nts_show_rss
from .opml import parse_opml, write_opml
from .http_client import fetch_text
from .podcasts import podcast_source_from_url
from .soundcloud import fetch_soundcloud_following_sources
from .source_collections import (
    active_sources_with_private,
    drift_has_failures,
    filtered_sources_with_private,
    netnewswire_drift_report,
    print_drift_report,
    resolve_source_identifier,
)
from .substack import parse_substack_library_html, parse_substack_profile_html
from .youtube import parse_youtube_channel_html, parse_youtube_subscriptions_file


DEFAULT_PROFILE = os.environ.get("RSS_PROFILE", "me")


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
    migration_parser.add_argument("--bandcamp-out-dir", type=Path, default=Path("exports/bandcamp"))
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

    subscribe_substack_parser = subparsers.add_parser("subscribe-substack", help="One-off add or reactivate a Substack feed")
    subscribe_substack_parser.add_argument("domain_or_url")
    subscribe_substack_parser.add_argument("--title", default="")
    subscribe_substack_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    subscribe_substack_parser.add_argument("--group", default="Substack")
    subscribe_substack_parser.add_argument("--notes", default="")

    subscribe_youtube_parser = subparsers.add_parser("subscribe-youtube", help="One-off add or reactivate a YouTube channel RSS feed by channel ID")
    subscribe_youtube_parser.add_argument("channel_id")
    subscribe_youtube_parser.add_argument("--title", required=True)
    subscribe_youtube_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    subscribe_youtube_parser.add_argument("--group", default="YouTube")
    subscribe_youtube_parser.add_argument("--notes", default="")

    youtube_url_parser = subparsers.add_parser("import-youtube-channel-url", help="Import one YouTube channel URL by reading its RSS metadata")
    youtube_url_parser.add_argument("url")
    youtube_url_parser.add_argument("--title", default="")
    youtube_url_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    youtube_url_parser.add_argument("--group", default="YouTube")

    youtube_subs_parser = subparsers.add_parser("import-youtube-subscriptions", help="Import YouTube subscriptions from a CSV or local list")
    youtube_subs_parser.add_argument("path", type=Path)
    youtube_subs_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    youtube_subs_parser.add_argument("--group", default="YouTube")

    substack_profile_parser = subparsers.add_parser("import-substack-profile", help="Import public Substack subscriptions from a profile page")
    substack_profile_parser.add_argument("url")
    substack_profile_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    substack_profile_parser.add_argument("--group", default="Substack")

    substack_library_parser = subparsers.add_parser("import-substack-library", help="Import Substack subscriptions from a saved library HTML page")
    substack_library_parser.add_argument("path", type=Path)
    substack_library_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    substack_library_parser.add_argument("--group", default="Substack")

    soundcloud_following_parser = subparsers.add_parser("import-soundcloud-following", help="Import followed SoundCloud profiles as public profile RSS feeds")
    soundcloud_following_parser.add_argument("url")
    soundcloud_following_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    soundcloud_following_parser.add_argument("--group", default="SoundCloud")

    nts_show_parser = subparsers.add_parser("subscribe-nts-show", help="Generate and subscribe to a local RSS feed for an NTS show page")
    nts_show_parser.add_argument("url")
    nts_show_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    nts_show_parser.add_argument("--group", default="NTS")
    nts_show_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    hydefm_parser = subparsers.add_parser("subscribe-hydefm-archive", help="Generate and subscribe to a local RSS feed for HydeFM archives")
    hydefm_parser.add_argument("--url", default=HYDEFM_ARCHIVE_URL)
    hydefm_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    hydefm_parser.add_argument("--group", default="HydeFM")
    hydefm_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    bandcamp_local_parser = subparsers.add_parser("refresh-bandcamp-local-feeds", help="Generate local RSS files for all saved Bandcamp artist and fan sources")
    bandcamp_local_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    bandcamp_local_parser.add_argument("--out-dir", type=Path, default=Path("exports/bandcamp"))
    bandcamp_local_parser.add_argument("--fan-max-items", type=int, default=40, help="Maximum items to fetch for followed fan collection feeds")
    bandcamp_local_parser.add_argument(
        "--full-fan-source-id",
        action="append",
        default=[],
        help="Fan source ID to refresh without the fan item cap; can be passed multiple times",
    )

    generated_local_parser = subparsers.add_parser(
        "refresh-generated-local-feeds",
        help="Regenerate saved NTS and HydeFM local RSS files",
    )
    generated_local_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    generated_local_parser.add_argument("--out-dir", type=Path, default=Path("exports/generated"))

    bandcamp_source_parser = subparsers.add_parser("subscribe-bandcamp-source", help="Add or reactivate a Bandcamp artist/label or fan source and generate its local RSS")
    bandcamp_source_parser.add_argument("url")
    bandcamp_source_parser.add_argument("--title", default="")
    bandcamp_source_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    bandcamp_source_parser.add_argument("--source-type", choices=["auto", "artist", "fan"], default="auto")
    bandcamp_source_parser.add_argument("--out-dir", type=Path, default=Path("exports/bandcamp"))
    bandcamp_source_parser.add_argument("--fan-max-items", type=int, default=40)
    bandcamp_source_parser.add_argument("--no-refresh", action="store_true", help="Only update registry metadata; do not fetch Bandcamp or write local RSS")

    podcast_parser = subparsers.add_parser("subscribe-podcast", help="Subscribe to a podcast from an RSS URL or Apple Podcasts URL")
    podcast_parser.add_argument("url")
    podcast_parser.add_argument("--title", default="")
    podcast_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    podcast_parser.add_argument("--group", default="Podcasts")
    podcast_parser.add_argument("--private", action="store_true", help="Write to the local gitignored private source overlay")

    status_parser = subparsers.add_parser("set-status", help="Set a source status")
    status_parser.add_argument("source_id")
    status_parser.add_argument("--status", required=True, choices=["active", "candidate", "paused", "unsubscribed"])
    status_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    status_parser.add_argument("--reason", default="")

    unsubscribe_parser = subparsers.add_parser("unsubscribe", help="Remove one or more exact sources from RSS export intent")
    unsubscribe_parser.add_argument("identifiers", nargs="+", help="Exact source ID, site URL, feed URL, or unique title")
    unsubscribe_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    unsubscribe_parser.add_argument("--reason", default="")

    history_parser = subparsers.add_parser("list-history", help="List subscription-history entries")
    history_parser.add_argument("--status", default=None)
    history_parser.add_argument("--profile", default=None)
    history_parser.add_argument("--kind", default=None)

    unfollow_parser = subparsers.add_parser("unfollow-checklist", help="List upstream unfollows implied by subscription-history entries")
    unfollow_parser.add_argument("--profile", default=DEFAULT_PROFILE)

    history_status_parser = subparsers.add_parser("set-history-status", help="Set a subscription-history entry status")
    history_status_parser.add_argument("entry_id")
    history_status_parser.add_argument(
        "--status",
        required=True,
        choices=["rss_unsubscribed", "external_unfollow_needed", "external_unfollow_confirmed", "ignored"],
    )

    reconcile_parser = subparsers.add_parser("reconcile-netnewswire", help="Compare a NetNewsWire OPML export against repo intent")
    reconcile_parser.add_argument("path", type=Path)
    reconcile_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    reconcile_parser.add_argument("--apply", action="store_true", help="Mark missing active repo sources as unsubscribed")
    reconcile_parser.add_argument("--reason", default="Missing from latest NetNewsWire export")

    verify_nnw_parser = subparsers.add_parser("verify-netnewswire", help="Verify a live NetNewsWire OPML matches the hosted repo export")
    verify_nnw_parser.add_argument("path", type=Path, help="Path to NetNewsWire's active Subscriptions.opml")
    verify_nnw_parser.add_argument("--expected", type=Path, required=True, help="Path to the expected hosted OPML export")
    verify_nnw_parser.add_argument("--profile", default=DEFAULT_PROFILE)

    discover_feed_parser = subparsers.add_parser("discover-feed", help="Discover a page's RSS, Atom, or JSON Feed URL from alternate links")
    discover_feed_parser.add_argument("url")

    audit_parser = subparsers.add_parser("audit-sources", help="Fetch active sources and report RSS/Atom/JSON Feed validation status")
    audit_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    audit_parser.add_argument("--status", default="active")
    audit_parser.add_argument("--kind", default=None)
    audit_parser.add_argument("--limit", type=int, default=0, help="Limit sources audited; useful for quick spot checks")

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


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profile = getattr(args, "profile", None) or os.environ.get("RSS_PROFILE", "")
    if args.data is None:
        args.data = default_sources_path(profile)
    if args.history is None:
        args.history = default_subscription_history_path(profile)
    store = FeedStore(args.data)
    private_store = FeedStore(args.private_data)
    history_store = SubscriptionHistoryStore(args.history)

    if args.command == "import-opml":
        sources = parse_opml(args.path, profile=args.profile)
        store.add_or_update_many(sources)
        store.save()
        print(f"Imported {len(sources)} sources into {args.data}")
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
            f"Applied generated-source migration in {args.data}: "
            f"rebuilt {migration.total}, removed duplicates {len(migration.removals)}"
        )
        return 0

    if args.command == "export-opml":
        sources = active_sources_with_private(store, private_store, args.profile)
        if args.bandcamp_feed_base:
            if not args.bandcamp_feed_token:
                parser.error("--bandcamp-feed-token is required when --bandcamp-feed-base is set")
            sources = sources_with_hosted_bandcamp_feeds(sources, args.bandcamp_feed_base, token=args.bandcamp_feed_token)
        title = args.title or f"netnewswire-feed-booster: {args.profile}"
        write_opml(args.out, sources, title=title)
        print(f"Exported {len(sources)} active sources to {args.out}")
        return 0

    if args.command == "list":
        sources = filtered_sources_with_private(store, private_store, profile=args.profile, status=args.status, kind=args.kind)
        for source in sources:
            groups = ", ".join(source.groups) if source.groups else "-"
            feed_url = source.feed_url if args.show_sensitive else "[redacted; use --show-sensitive]"
            print(f"{source.id}\t{source.status}\t{source.kind}\t{groups}\t{source.title}\t{feed_url}")
        print(f"{len(sources)} sources")
        return 0

    if args.command == "add":
        source = Source(
            id=slugify(args.title),
            title=args.title,
            feed_url=normalize_url(args.feed_url),
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

    if args.command == "subscribe-substack":
        domain = args.domain_or_url.replace("https://", "").replace("http://", "").strip("/")
        title = args.title or domain.replace(".substack.com", "").replace("www.", "")
        source = Source(
            id=slugify(title),
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
        print(f"Subscribed Substack source {source_id}: {source.feed_url}")
        return 0

    if args.command == "subscribe-youtube":
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={args.channel_id}"
        source = Source(
            id=slugify(args.title),
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
        print(f"Subscribed YouTube source {source_id}: {feed_url}")
        return 0

    if args.command == "import-youtube-channel-url":
        html = fetch_text(args.url)
        source = parse_youtube_channel_html(html, profile=args.profile, group=args.group, fallback_title=args.title)
        source_id = store.add_or_update(source)
        store.save()
        print(f"Saved YouTube source {source_id}: {source.feed_url}")
        return 0

    if args.command == "import-youtube-subscriptions":
        sources = parse_youtube_subscriptions_file(args.path, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} YouTube subscriptions into {args.data}")
        return 0

    if args.command == "import-substack-profile":
        html = fetch_text(args.url)
        sources = parse_substack_profile_html(html, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} public Substack subscriptions into {args.data}")
        return 0

    if args.command == "import-substack-library":
        html = args.path.read_text(encoding="utf-8", errors="replace")
        sources = parse_substack_library_html(html, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} Substack library subscriptions into {args.data}")
        return 0

    if args.command == "import-soundcloud-following":
        sources = fetch_soundcloud_following_sources(args.url, profile=args.profile, group=args.group)
        for source in sources:
            store.add_or_update(source)
        store.save()
        print(f"Imported {len(sources)} SoundCloud following sources into {args.data}")
        return 0

    if args.command == "subscribe-nts-show":
        html = fetch_text(args.url)
        title, _, _ = parse_nts_show_html(html, args.url)
        source_id = slugify(f"NTS {title}")
        out_path = args.out_dir / f"{source_id}.rss"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_nts_show_rss(args.url, fetcher=lambda _: html), encoding="utf-8")
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
        print(f"Subscribed NTS show {changed_id}: {source.feed_url}")
        return 0

    if args.command == "subscribe-hydefm-archive":
        markdown = fetch_hydefm_archive_markdown(args.url)
        title, _, _ = parse_hydefm_archive_markdown(markdown, args.url)
        source_id = slugify(f"Radio {title}")
        out_path = args.out_dir / f"{source_id}.rss"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_hydefm_archive_rss(args.url, fetcher=lambda _: markdown), encoding="utf-8")
        source = Source(
            id=source_id,
            title=title,
            feed_url=out_path.resolve().as_uri(),
            site_url=args.url.rstrip("/") + "/",
            kind="other",
            profiles=[args.profile],
            groups=[args.group],
            source="radio-local-generated",
            notes="Generated local RSS feed from the HydeFM archive page via a text mirror because HydeFM blocks direct scripted RSS/archive fetches with Cloudflare.",
        )
        changed_id = store.add_or_update(source)
        store.set_status(changed_id, "active")
        store.save()
        print(f"Subscribed HydeFM archive {changed_id}: {source.feed_url}")
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

        for _, source in bandcamp_sources:
            if source.source == "bandcamp-generated-music-feed":
                continue

            try:
                html = fetch_text(bandcamp_fetch_url(source))
                items = bandcamp_items_for_source(source, html, fan_max_items=args.fan_max_items, full_fan_source_ids=full_fan_source_ids)
                if not items:
                    failed += 1
                    print(f"FAILED\t{source.id}\tNo items found\t{source.site_url}")
                    continue

                out_path = args.out_dir / f"{source.id}.rss"
                write_bandcamp_collection_rss(out_path, profile_url=source.site_url, title=source.title, items=items)
                source.feed_url = out_path.resolve().as_uri()
                source.source = "bandcamp-local-generated"
                source.notes = "Generated local RSS feed from the saved Bandcamp source page because OpenRSS did not mirror this Bandcamp feed reliably."
                updated += 1
                print(f"UPDATED\t{source.id}\t{len(items)} items\t{source.feed_url}")
            except Exception as error:
                failed += 1
                print(f"FAILED\t{source.id}\t{type(error).__name__}: {error}\t{source.site_url}")

        store.set_sources(sources)
        store.save()
        print(f"Updated local Bandcamp feeds: {updated}")
        print(f"Failed local Bandcamp feeds: {failed}")
        return 0

    if args.command == "refresh-generated-local-feeds":
        updated = 0
        failed = 0
        sources = store.sources()
        for source in sources:
            if source.status != "active" or args.profile not in source.profiles:
                continue
            try:
                if source.source == "nts-local-generated":
                    rss = render_nts_show_rss(source.site_url)
                elif source.source == "radio-local-generated":
                    rss = render_hydefm_archive_rss(source.site_url)
                else:
                    continue
                out_path = args.out_dir / f"{source.id}.rss"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(rss, encoding="utf-8")
                source.feed_url = out_path.resolve().as_uri()
                updated += 1
                print(f"UPDATED\t{source.id}\t{source.feed_url}")
            except Exception as error:
                failed += 1
                print(f"FAILED\t{source.id}\t{type(error).__name__}: {error}\t{source.site_url}")
        store.set_sources(sources)
        store.save()
        print(f"Updated local generated feeds: {updated}")
        print(f"Failed local generated feeds: {failed}")
        return 0

    if args.command == "subscribe-bandcamp-source":
        source = build_bandcamp_source_from_url(
            args.url,
            title=args.title,
            profile=args.profile,
            source_type=args.source_type,
            out_dir=args.out_dir,
        )

        if not args.no_refresh:
            rss = render_bandcamp_source_rss(
                source,
                fan_max_items=args.fan_max_items,
                full_fan_source_ids=set(),
            )
            out_path = args.out_dir / f"{source.id}.rss"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rss, encoding="utf-8")
            source.feed_url = out_path.resolve().as_uri()
            print(f"Generated local Bandcamp RSS for {source.id}: {out_path}")

        source_id = store.add_or_update(source)
        store.set_status(source_id, "active")
        store.save()
        print(f"Subscribed Bandcamp source {source_id}: {source.feed_url}")
        return 0

    if args.command == "subscribe-podcast":
        target_store = private_store if args.private else store
        source = podcast_source_from_url(args.url, title=args.title, profile=args.profile, group=args.group)
        source_id = target_store.add_or_update(source)
        target_store.set_status(source_id, "active")
        target_store.save()
        destination = args.private_data if args.private else args.data
        print(f"Subscribed podcast source {source_id} into {destination}")
        return 0

    if args.command == "set-status":
        source = store.set_status(args.source_id, args.status)
        if args.status == "unsubscribed":
            history_store.record_rss_unsubscribe(source, profile=args.profile, reason=args.reason)
            history_store.save()
        store.save()
        print(f"Set {source.id} to {source.status}")
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
            print(
                f"{entry.id}\t{entry.status}\t{entry.source_kind}\t"
                f"{entry.profile}\t{entry.source_title}\t{entry.external_url or entry.feed_url}"
            )
        print(f"{len(entries)} subscription-history entries")
        return 0

    if args.command == "unfollow-checklist":
        entries = history_store.external_unfollow_candidates(profile=args.profile)
        for entry in entries:
            print(
                f"{entry.id}\t{entry.source_kind}\t{entry.source_title}\t"
                f"{entry.external_url or entry.feed_url}\t{entry.status}"
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
            print(f"MISSING\t{source.id}\t{source.kind}\t{source.title}\t{source.feed_url}")

        print(f"NetNewsWire sources not in repo: {len(extra_in_netnewswire)}")
        for source in extra_in_netnewswire:
            print(f"EXTRA\t{source.kind}\t{source.title}\t{source.feed_url}")

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
        print_drift_report(drift)
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
            fields = [result.status, result.feed_type or "-", result.source_id, result.title, result.url]
            if result.discovered_url:
                fields.append(f"discovered={result.discovered_url}")
            if result.detail and result.detail != "ok":
                fields.append(result.detail)
            print("\t".join(fields))
        print(f"Audited {len(results)} sources; failures: {len(failures)}")
        return 1 if failures else 0

    parser.error("Unknown command")
    return 2
