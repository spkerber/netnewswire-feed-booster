# Operations

Set your profile once per shell:

```bash
export PYTHONPATH=src
export RSS_PROFILE=me
```

## Sources

```bash
python3 -m netnewswire_feed_booster list --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-substack oneusefulthing.substack.com --title "One Useful Thing" --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-youtube UC_x5XG1OV2P6uZZ5FSM9Ttw --title "Google Developers" --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-podcast https://example.com/feed.xml --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster unsubscribe source-id another-source-id --reason "Too noisy" --profile "$RSS_PROFILE"
```

Use `add` only for a direct feed that does not have a source-specific command. Exact unsubscribe identifiers may be source IDs, site URLs, feed URLs, or unique titles.

## Discovery And Audit

```bash
python3 -m netnewswire_feed_booster discover-feed https://example.com
python3 -m netnewswire_feed_booster audit-sources --profile "$RSS_PROFILE" --limit 25
```

`audit-sources` returns nonzero if a selected source is not a valid RSS, Atom, or JSON Feed. Run it before a large import, repair, or public cleanup pass.

## Generated Feeds

```bash
python3 -m netnewswire_feed_booster subscribe-bandcamp-source https://artist.bandcamp.com/ --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster refresh-bandcamp-local-feeds --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-nts-show https://www.nts.live/shows/example --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-hydefm-archive --profile "$RSS_PROFILE"
```

After adding or removing a generated source, redeploy the hosted bridge before exporting hosted OPML:

```bash
./scripts/netnewswire_workflow.sh all
```

If only direct feeds changed, regenerate hosted OPML without a deploy:

```bash
./scripts/netnewswire_workflow.sh export
```

## Reconciliation And Drift

```bash
python3 -m netnewswire_feed_booster reconcile-netnewswire imports/netnewswire.opml --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster reconcile-netnewswire imports/netnewswire.opml --profile "$RSS_PROFILE" --apply
python3 -m netnewswire_feed_booster list-history --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster unfollow-checklist --profile "$RSS_PROFILE"
```

`subscription-history` records intentional RSS removals so stale feeds cannot silently reappear and any upstream unfollow work remains visible.

For the iCloud subscription file, verify before repairing:

```bash
./scripts/netnewswire_workflow.sh verify-netnewswire
./scripts/netnewswire_workflow.sh repair-netnewswire
```

Repair is explicit and local: it refuses while NetNewsWire is open, creates a backup, replaces the local subscription OPML, validates it, and verifies again.
