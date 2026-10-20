# Hosted Bridge

Use an HTTPS bridge only for generated local RSS that NetNewsWire cannot refresh reliably through `file://`. Direct HTTPS feeds stay direct.

The bridge serves one tokenized route for every generated adapter, Bandcamp included:

```text
/feeds/<token>/generated/<source-id>.rss
```

Tokenless routes return `404`. This makes stale imports visible in logs rather than accidentally exposing feeds.

Bandcamp shares this route and cache with every other generated adapter, but keeps its own scheduled refresh function (`refresh_bandcamp_cache` in `modal_bandcamp_app.py`) because fan-collection pagination, a fan item cap, and a full-fan override list don't fit the single-fetch `render(source, content)` shape the other adapters use.

## Host Options

[Modal web functions](https://modal.com/docs/guide/webhooks) is the only bundled and tested deployment path. It includes the runtime, scheduled refreshes, and a volume cache.

[Cloudflare Workers](https://developers.cloudflare.com/workers/), [Railway](https://docs.railway.com/deployments), and [DigitalOcean](https://docs.digitalocean.com/) are reasonable future host targets, but this repository does not yet include a Worker handler, generic ASGI service, or container deployment for them. Do not treat those providers as turnkey instructions.

## Modal Setup

Modal is optional and this is an external deployment. Do not continue until direct feeds and local generated-source metadata are working. The commands below create or update Modal resources and may use provider credits.

### Preflight

1. Create a Modal account, then install the optional local dependency and authenticate:

```bash
python3 -m venv .venv-modal
.venv-modal/bin/python -m pip install -e ".[modal]"
.venv-modal/bin/modal setup
```

2. Confirm `.venv-modal/bin/modal` exists. Do not paste Modal credentials, the bridge token, or `data/private.env` into a chat or repository.
3. Run `./scripts/bootstrap_profile.sh me` first. The hosted workflow refuses tracked starter data and requires private profile files.

```bash
install -m 600 examples/private.env.example data/private.env
```

The tracked example contains placeholders and can remain readable in a public clone. Your copied `data/private.env` holds a real token, so the command creates it with owner-only (`0600`) permissions. Set `RSS_PROFILE`, `RSS_SOURCES_FILE`, `RSS_HISTORY_FILE`, `RSS_FEED_TOKEN`, and unique `MODAL_*` names. Keep the three profile settings aligned, for example `RSS_PROFILE=me`, `RSS_SOURCES_FILE=data/sources.me.json`, and `RSS_HISTORY_FILE=data/subscription-history.me.json`. Leave `RSS_FEED_BASE` as a placeholder until Modal deploy prints the HTTPS endpoint. If a Bandcamp storefront redirects anonymous requests to its own custom domain, add that exact hostname to the comma-separated `BANDCAMP_CUSTOM_DOMAINS` setting in the ignored private file; never add personal redirect hosts to tracked code. The `MODAL_*` values are only for the Modal deployment path; other hosts should use equivalent provider configuration while preserving `RSS_FEED_BASE` and `RSS_FEED_TOKEN`.

```bash
./scripts/netnewswire_workflow.sh deploy-modal
```

Copy the deployed HTTPS endpoint into `RSS_FEED_BASE`, then export the hosted OPML:

```bash
./scripts/netnewswire_workflow.sh export
```

The workflow loads only ignored private environment files, deploys the current source bundle, runs tests, exports hosted OPML, and validates XML. Generated feeds are served from a seed/cache; normal reader requests never force upstream scraping. The default is a 12-hour source refresh interval, an hourly scheduler, and no more than 20 sources per route per run. It sends saved `ETag`/`Last-Modified` validators when a source supports them. The token prevents casual enumeration; it is not account-grade authentication, so do not publish tokenized URLs or paste diagnostics without their default redaction. See [Slow Reading And Refresh Policy](reading-practice.md) for capacity planning and safe configuration changes.

## Checking Bridge Health

Reader requests never fetch upstream, so a generated feed whose refreshes keep failing, or that the scheduler never reaches, goes on serving an old copy and the reader shows nothing wrong. Each refresh therefore records, per source, the last attempt, the last success, a count of consecutive failures, and a short error with URLs removed. A 304 for an unchanged page counts as a success.

```bash
./scripts/netnewswire_workflow.sh bridge-health
```

This reads `/feeds/<token>/health` on the deployed bridge, which is behind the feed token because the report lists source IDs. The workflow passes the token to `curl` on stdin rather than on the command line. The report sorts active generated sources by what a reader is actually getting:

| Group | Meaning |
| --- | --- |
| healthy | Refreshed successfully within three refresh intervals |
| never attempted | The scheduler has not reached it; readers get the deploy-time seed, if any |
| failing | The latest refreshes failed; shows how many in a row and the last error |
| stale | No success within three refresh intervals |
| seed copy only | No cached refresh yet; readers get the copy bundled at deploy |
| nothing to serve | No cache and no seed; readers get "waiting for its scheduled refresh" |

The route reads only the cache volume. State written before success tracking holds only the last attempt, so until every source has been refreshed once the report falls back to each cache file's age. That makes the first reports after this change overstate staleness, never understate it.

### Seeds Come From Your Checkout

Deploys bundle `exports/generated` from the local checkout as seeds. A source the bridge has never refreshed serves that copy, which is only as fresh as your last local refresh. A source with no local file has nothing to serve until its first scheduled refresh. Two ways to keep seeds from going quietly stale, not yet implemented:

- Refresh locally before deploying (`refresh-bandcamp-local-feeds`, `refresh-generated-local-feeds`), so seeds start current.
- Stop bundling local files, and seed new sources from their first scheduled refresh instead. This trades a short "waiting for its scheduled refresh" window for never serving a months-old copy as if it were current.

`bridge-health` shows how many sources are on seed copies, so either choice can be judged against real numbers.
