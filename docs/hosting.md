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
| refresh off | Shown only with `RSS_BRIDGE_BANDCAMP_REFRESH=off`: Bandcamp sources the bridge no longer refreshes, kept out of failing and stale |
| email | Shown only when there are [email sources](#email-sources): each one's last accepted message. One with no mail in 30 days is called quiet, which is information, not a failure |

The route reads only the cache volume. State written before success tracking holds only the last attempt, so until every source has been refreshed once the report falls back to each cache file's age. That makes the first reports after this change overstate staleness, never understate it.

### Seeds Come From Your Checkout

Deploys bundle `exports/generated` from the local checkout as seeds. A source the bridge has never refreshed serves that copy, which is only as fresh as your last local refresh. A source with no local file has nothing to serve until its first scheduled refresh.

A source with both copies serves the cached one, unless the seed's `lastBuildDate` is later. Then the seed wins until the next refresh rewrites the cache. That is what lets a local refresh plus a redeploy update feeds whose refresh is off. Only generated Bandcamp feeds carry a `lastBuildDate` today; for every other source the cache always wins.

Two ways to keep seeds from going quietly stale, not yet implemented:

- Refresh locally before deploying (`refresh-bandcamp-local-feeds`, `refresh-generated-local-feeds`), so seeds start current.
- Stop bundling local files, and seed new sources from their first scheduled refresh instead. This trades a short "waiting for its scheduled refresh" window for never serving a months-old copy as if it were current.

`bridge-health` shows how many sources are on seed copies, so either choice can be judged against real numbers.

## Email Sources

An email source turns a newsletter with no feed into one. Its mail goes to an address of its own, the bridge keeps a sanitized copy of each message, and the feed is served on the usual `/feeds/<token>/generated/<source-id>.rss` route, so NetNewsWire needs nothing new.

```bash
python3 -m netnewswire_feed_booster subscribe-email-source \
  --title "Weekly Notes" --sender-domain news.example.net --group Newsletters
```

The command prints the source's address once, for example `inbox+weekly-notes-<random>@feeds.example.com`, built from `RSS_INBOUND_EMAIL_ADDRESS`. It also writes an empty feed, so NetNewsWire can subscribe before any mail arrives. The address is kept only in the private registry, never in an export or a served feed; `list --show-sensitive` shows it again. Running the command again for the same title replaces its sender domains and keeps the address, and `--new-address` replaces the address too. The bridge reads the registry shipped with each deploy, so run `deploy-modal` after adding or changing an email source. To turn one address off, run `set-status <source-id> --status paused` and redeploy. Its stored items stay until the source leaves the registry.

Run mail routing on a subdomain kept for feeds, never on the domain your own mail uses, and never route a personal mailbox into the bridge. With subaddressing, one routing rule for `RSS_INBOUND_EMAIL_ADDRESS` delivers every source's subaddress. If the mail host can't do subaddressing, set `RSS_INBOUND_EMAIL_ADDRESS` to the bare subdomain (`feeds.example.com`). Addresses then look like `weekly-notes-<random>@feeds.example.com`, and each one needs its own routing rule.

### What the Bridge Accepts

The mail host's inbound hook (for Cloudflare Email Routing, an Email Worker) posts each message on its own:

```text
POST /inbound/email
Authorization: Bearer <RSS_INBOUND_EMAIL_SECRET>
X-Envelope-To: <the envelope recipient>
Content-Type: message/rfc822

<the raw message>
```

The bridge checks the secret, then the declared size, then the address, all before reading the message. Then it reads the message and checks the sender. No rejected message leaves anything behind.

| Status | Meaning | The hook should |
| --- | --- | --- |
| 202 | Accepted: the item is stored and the feed rebuilt | Accept |
| 200 | Already stored: same `Message-ID` | Accept |
| 400 | No usable `X-Envelope-To`, or a message that can't be parsed | Reject |
| 401 | Missing or wrong secret, or no secret of at least 32 characters configured | Fail temporarily, so the sender retries |
| 403 | Not from one of the source's sender domains | Reject |
| 404 | No active email source holds the address | Reject |
| 413 | Larger than `RSS_INBOUND_EMAIL_MAX_BYTES` (default 2 MiB) | Reject |
| 5xx | The bridge failed | Fail temporarily |

**Senders.** The From domain must be one of the source's `--sender-domain` values or a subdomain of one, and the mail host must vouch for it with one of these: a DMARC pass for the From domain, a DKIM pass signed by a sender domain, or an SPF pass for an envelope sender in one. The bridge reads only the first `Authentication-Results` header from `mx.cloudflare.net`. Cloudflare adds that header above everything else in the message; a sender can write its own further down, and those are ignored. A newsletter sent through a mailing service may be signed with the service's domain. Read the first message's `Authentication-Results` before adding a service's domain as a sender, because that admits everyone the service sends for.

**What is kept.** No part of the raw message is written anywhere. The bridge keeps one sanitized item per message under `/cache/email/<source-id>/`, named by a hash of its `Message-ID`, and rebuilds `/cache/generated/<source-id>.rss` from the newest `RSS_MAX_ITEMS_PER_SOURCE` items, deleting older ones. The sanitizer keeps a short list of plain markup and removes the rest:

- scripts, styles, forms, frames, hidden content, event handlers, and every attribute not on its list;
- links named in `List-Unsubscribe`, links whose path, text, or surrounding text is about unsubscribing, preferences, or an account, and every `mailto:` link;
- the recipient's address wherever it appears, including original recipients named in forwarding headers, and percent-encoded and base64 forms of it inside links;
- tracking pixels, every remote image not on the source's `--image-host` list (none by default, because loading a remote image tells the sender when you read it), and tracking query parameters such as `utm_*`.

Each accepted message counts as that source's refresh, so `bridge-health` lists it with the date of its last mail.

### Rotating the Inbound Secret

The secret lives in three places only: `data/private.env`, the Modal secret that `deploy-modal` builds from it, and the inbound hook's own secret store. To rotate it:

1. Generate a new value, for example with `openssl rand -hex 32`, and set it as `RSS_INBOUND_EMAIL_SECRET` in `data/private.env`.
2. Run `./scripts/netnewswire_workflow.sh deploy-modal`. It replaces the Modal secret and redeploys.
3. Set the same value in the hook's secret store. For a Cloudflare Worker that is `wrangler secret put RSS_INBOUND_EMAIL_SECRET`.

Between steps 2 and 3 the bridge answers the hook with 401. A hook that turns a 401 into a temporary failure makes senders retry later, so no mail is lost. Rotate whenever the value may have leaked, and never paste it into a chat, an issue, or a log.
