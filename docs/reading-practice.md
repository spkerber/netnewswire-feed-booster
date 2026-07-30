# Slow Reading And Refresh Policy

NetNewsWire Feed Booster is designed for deliberate catch-up, not real-time alerts. The default hosted bridge favors predictable provider load, low Modal usage, and a compact cache over minute-by-minute updates.

Read in NetNewsWire. Use its read-later, starred, folder, and sync features to decide what stays in your attention. This project only maintains subscriptions and current generated feeds.

## What Refreshes Where

| Source type | Who fetches it | What you can tune here |
| --- | --- | --- |
| Native RSS, Atom, or JSON Feed | Your reader, directly from the publisher | Nothing in the bridge. Keep it direct. |
| Generated Bandcamp, NTS, Mixcloud, or webpage-recipe feed | The optional HTTPS bridge | Refresh cadence, per-run batch size, and retained RSS items. |

Direct feeds may still update at the cadence NetNewsWire chooses. The bridge never proxies them, so it does not add latency, cost, or another failure point.

## Standard Setting

The default Modal settings in ignored `data/private.env` are:

```dotenv
RSS_REFRESH_INTERVAL_SECONDS=43200
RSS_REFRESH_SCHEDULE_HOURS=1
RSS_MAX_REFRESH_SOURCES_PER_RUN=20
RSS_MAX_ITEMS_PER_SOURCE=50
```

This means each generated source is eligible for an upstream check every 12 hours. The scheduler wakes hourly and processes at most 20 sources per hosted route each time. With one-second spacing and a 30-second request limit, a batch fits safely inside Modal's 15-minute function timeout.

Capacity per route is:

```text
(refresh interval / scheduler interval) * max sources per run
```

The defaults support up to 240 Bandcamp sources and 240 other generated sources within each 12-hour target window. The routes are separate, so 100 Bandcamp sources and 20 Mixcloud/NTS sources do not compete for the same batch.

Check a real profile before deploying:

```bash
./scripts/netnewswire_workflow.sh refresh-plan
```

The command reports the direct-feed count, generated-source count by route, first-pass duration, and whether the selected settings meet their target cadence.

## Tuning

Edit ignored `data/private.env`, run `refresh-plan`, then redeploy:

```bash
./scripts/netnewswire_workflow.sh refresh-plan
./scripts/netnewswire_workflow.sh deploy-modal
./scripts/netnewswire_workflow.sh export
```

For a narrow collection, one daily check is usually enough:

```dotenv
RSS_REFRESH_INTERVAL_SECONDS=86400
RSS_REFRESH_SCHEDULE_HOURS=6
RSS_MAX_REFRESH_SOURCES_PER_RUN=20
```

That configuration supports 80 generated sources per route per day. Use `refresh-plan` rather than guessing.

For a wide collection, keep the hourly scheduler and lengthen the refresh window before increasing the batch. For example, a 24-hour window with batches of 20 supports 480 sources per route while reducing upstream checks to once per day. Do not set a batch over 25: it removes the timeout margin and creates larger request bursts.

## Storage And Reader Context

The Modal Volume stores only the latest generated RSS, HTTP validators, and one last-attempt timestamp per active generated source. It does not keep an article archive, request log, or per-item consumption history. Removed sources are pruned from the cache, validator, and scheduler-state directories during the Bandcamp refresh job.

Generated feeds retain 50 current items by default. This keeps RSS payloads small and avoids re-exporting an artist's entire catalog on every refresh. Increase `RSS_MAX_ITEMS_PER_SOURCE` only if a source genuinely needs a deeper current window.

The reader, not the bridge, owns read/unread state and item history. Stable RSS item GUIDs let NetNewsWire preserve that context across refreshes without the bridge storing personal reading behavior.

## Failure Behavior

Reader requests serve only a cached or seeded feed and never force an upstream fetch. If neither exists, the bridge returns a temporary `503` until the scheduled job succeeds. Failed refreshes retain the last good RSS and wait until the next eligible interval instead of retrying in a tight loop.

This is intentional: slower recovery is preferable to repeatedly hitting a source during an outage or anti-abuse response. If a source is time-sensitive, use its native feed when available or configure a shorter interval after confirming the source can tolerate it.
