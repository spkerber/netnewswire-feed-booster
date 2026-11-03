# Slow Reading And Refresh Policy

NetNewsWire Feed Booster is designed for deliberate catch-up, not real-time alerts. The default hosted bridge favors predictable provider load, low Modal usage, and a compact cache over minute-by-minute updates.

Read in NetNewsWire. Use its read-later, starred, folder, and sync features to decide what stays in your attention. This project only maintains subscriptions and current generated feeds.

## What Refreshes Where

| Source type | Who fetches it | What you can tune here |
| --- | --- | --- |
| Native RSS, Atom, or JSON Feed | Your reader, directly from the publisher | Nothing in the bridge. Keep it direct. |
| Generated NTS, Mixcloud, or webpage-recipe feed, Mac only | Your Mac, when you run `refresh-local` | How often you run it. |
| Generated NTS, Mixcloud, or webpage-recipe feed, hosted | The optional HTTPS bridge, on a schedule | Refresh cadence, per-run batch size, and retained RSS items. |
| Generated Bandcamp feed | Your Mac: `refresh-local` on one Mac, `catch-up` with the bridge | How often you run it. The bridge can refresh Bandcamp only from a host Bandcamp still answers; see [When Bandcamp Blocks The Host](#when-bandcamp-blocks-the-host). |

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

A route over capacity still reaches every source. Each run takes the sources that have waited longest, so the cycle stretches to the first-pass time `refresh-plan` reports instead of skipping the end of the list. The refresh interval is a minimum gap, not a speed: raising it reduces upstream checks but never makes an over-capacity route cycle faster, and because both routes share it, it slows the smaller route too.

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

The Modal Volume stores only the latest generated RSS, HTTP validators, and one last-attempt timestamp per active generated source. It does not keep an article archive, request log, or per-item consumption history. Removed sources, Bandcamp included, are pruned from the cache, validator, and scheduler-state directories during the generated-feed refresh job, so pruning continues when the Bandcamp refresh is off.

Generated feeds retain 50 current items by default. This keeps RSS payloads small and avoids re-exporting an artist's entire catalog on every refresh. Increase `RSS_MAX_ITEMS_PER_SOURCE` only if a source genuinely needs a deeper current window.

The reader, not the bridge, owns read/unread state and item history. Stable RSS item GUIDs let NetNewsWire preserve that context across refreshes without the bridge storing personal reading behavior.

## Failure Behavior

Reader requests serve only a cached or seeded feed and never force an upstream fetch. If neither exists, the bridge returns a temporary `503` until the scheduled job succeeds. Failed refreshes retain the last good RSS and wait until the next eligible interval instead of retrying in a tight loop.

This is intentional: slower recovery is preferable to repeatedly hitting a source during an outage or anti-abuse response. If a source is time-sensitive, use its native feed when available or configure a shorter interval after confirming the source can tolerate it.

### When Bandcamp Blocks The Host

Bandcamp can answer a whole network with a bot check: a "Client Challenge" page, sent as HTTP 200, in place of every artist page and API response. Since about October 2026 it has answered every request from Modal this way, while the same requests from a home connection still get real pages, so assume other cloud hosts get it too. The bridge recognizes the page and reports it as `BandcampBotCheck` rather than as a page with no releases. A refresh run stops at the first one, and so does `refresh-bandcamp-local-feeds`, which then exits with status 1, because every further request would get the same answer.

Never try to get past the check. Turn the bridge's Bandcamp refresh off instead, in ignored `data/private.env`, and redeploy. The example file already sets it off, because Modal is the bundled host:

```dotenv
RSS_BRIDGE_BANDCAMP_REFRESH=off
```

With the refresh off, the bridge sends Bandcamp nothing. Bandcamp feeds serve their last cached or deploy-time copy, and `refresh-plan` leaves them out of its capacity check. `bridge-health` lists them as "refresh off" rather than failing or stale, with the date of the last catch-up, and says when the next one is due. To catch them up, run this from a network Bandcamp still answers, such as your home connection:

```bash
./scripts/netnewswire_workflow.sh catch-up
```

It rebuilds the Bandcamp feeds on your Mac, then deploys. A seed built after the cached copy is served in its place (see [Seeds Come From Your Checkout](hosting.md#seeds-come-from-your-checkout)). Every two weeks or so suits slow reading. See [Catching Up Bandcamp](hosting.md#catching-up-bandcamp) for the details.

If your host is one Bandcamp still answers, set the refresh back to `on`. The bridge then refreshes Bandcamp on the same schedule as everything else, and stops at the first bot check if that ever changes.
