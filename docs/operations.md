# Operations

Set your profile once per shell:

```bash
export PYTHONPATH=src
export RSS_PROFILE=me
```

## Maintenance Rhythm

| When | In NetNewsWire / Finder | In the project |
| --- | --- | --- |
| When you find a new source | Add a native feed directly when possible; place it in a reader folder | Use the relevant source command only when you want it in the portable registry |
| Weekly or before a large import | Export an OPML backup if you are about to reorganize an account | Run `audit-sources` and export a candidate OPML |
| When a feed is noisy | Unsubscribe or move it in the reader after deciding intentionally | Run `unsubscribe` so it cannot return during reconciliation |
| When a generated source changes | Do not repeatedly refresh it in the reader | Run `refresh-plan`; redeploy only after source or cadence changes |
| Before sharing code | Keep OPML, profile JSON, and `.env` files out of Finder shares and Git | Run `./scripts/verify_public_template.sh` |

The project is deliberately not an item archive. NetNewsWire owns read/unread state; the bridge keeps only current generated RSS and minimal refresh state. See [Slow Reading And Refresh Policy](reading-practice.md).

## Sources

```bash
python3 -m netnewswire_feed_booster list --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-substack oneusefulthing.substack.com --title "One Useful Thing" --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-youtube UC_x5XG1OV2P6uZZ5FSM9Ttw --title "Google Developers" --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster subscribe-podcast https://example.com/feed.xml --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster set-folder source-id "My Folder" "Optional Subfolder" --profile "$RSS_PROFILE"
python3 -m netnewswire_feed_booster unsubscribe source-id another-source-id --reason "Too noisy" --profile "$RSS_PROFILE"
```

For accepted URLs and source-export formats, including Google Takeout's YouTube `subscriptions.csv`, see [Collect Sources](source-collection.md).

Use `add` only for a direct feed that does not have a source-specific command. Exact source identifiers may be source IDs, site URLs, feed URLs, or unique titles. Folder names are personal: use `set-folder` only if you want portable OPML folders, and choose the structure yourself.

Human-readable source and refresh commands redact feed URLs, local paths, and failure details by default. Use `--show-sensitive` only for a local inspection and never paste that output: generated-feed URLs can contain a local checkout path or access token. `discover-feed` is the deliberate exception because returning the discovered public feed URL is its purpose.

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
python3 -m netnewswire_feed_booster subscribe-mixcloud-profile https://www.mixcloud.com/example/ --profile "$RSS_PROFILE"
```

After adding or removing a generated source on an already configured Modal host, redeploy the hosted bridge, then export hosted OPML:

```bash
./scripts/netnewswire_workflow.sh deploy-modal
./scripts/netnewswire_workflow.sh export
```

The deployment is an external action and may use provider credits. Review the private environment file and current refresh plan first. If you use a coding agent, require it to ask before `deploy-modal`. On an initial host setup, follow [Hosted Bridge](hosting.md) instead: you must save the deployed HTTPS endpoint as `RSS_FEED_BASE` before exporting hosted OPML.

Before deploying a large generated-source collection or changing its cadence, inspect the capacity plan:

```bash
./scripts/netnewswire_workflow.sh refresh-plan
```

See [Slow Reading And Refresh Policy](reading-practice.md) for the settings in `data/private.env` and their provider-load and freshness tradeoffs.

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
export NETNEWSWIRE_OPML="/path/to/the/account/Subscriptions.opml"
./scripts/netnewswire_workflow.sh verify-netnewswire
./scripts/netnewswire_workflow.sh repair-netnewswire
```

Repair is explicit and local: you must set the exact target OPML path, it refuses while NetNewsWire is open, creates a backup, replaces the local subscription OPML, validates it, and verifies again.
