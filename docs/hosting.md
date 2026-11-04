# Hosted Bridge

Use an HTTPS bridge only for generated local RSS that NetNewsWire cannot refresh reliably through `file://`, which in practice means reading generated feeds on more than one device. Direct HTTPS feeds stay direct. If you read on one Mac only, you don't need any of this: see [Two Setups For Generated Feeds](../README.md#two-setups-for-generated-feeds).

What the bridge refreshes on its own: NTS, Mixcloud, and webpage-recipe feeds, on a schedule. What it doesn't: Bandcamp, which answers Modal with a bot check, so Bandcamp feeds catch up from your Mac instead. See [Catching Up Bandcamp](#catching-up-bandcamp).

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

The tracked example contains placeholders and can remain readable in a public clone. Your copied `data/private.env` holds a real token, so the command creates it with owner-only (`0600`) permissions. Set `RSS_PROFILE`, `RSS_SOURCES_FILE`, `RSS_HISTORY_FILE`, `RSS_FEED_TOKEN`, and unique `MODAL_*` names. Keep the three profile settings aligned, for example `RSS_PROFILE=me`, `RSS_SOURCES_FILE=data/sources.me.json`, and `RSS_HISTORY_FILE=data/subscription-history.me.json`. Leave `RSS_FEED_BASE` as a placeholder until Modal deploy prints the HTTPS endpoint. If a Bandcamp storefront redirects anonymous requests to its own custom domain, add that exact hostname to the comma-separated `BANDCAMP_CUSTOM_DOMAINS` setting in the ignored private file; never add personal redirect hosts to tracked code. Leave `RSS_BRIDGE_BANDCAMP_REFRESH=off` as the example sets it: Bandcamp answers Modal with a bot check, so Bandcamp feeds are kept current by [catching up from your Mac](#catching-up-bandcamp) instead. The `MODAL_*` values are only for the Modal deployment path; other hosts should use equivalent provider configuration while preserving `RSS_FEED_BASE` and `RSS_FEED_TOKEN`.

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
| refresh off | Shown only with `RSS_BRIDGE_BANDCAMP_REFRESH=off`: Bandcamp sources the bridge no longer refreshes, kept out of failing and stale, with the date of the last [catch-up](#catching-up-bandcamp) |
| email | Shown only when there are [email sources](#email-sources-optional-future): each one's last accepted message. One with no mail in 30 days is called quiet, which is information, not a failure |

With Bandcamp refresh off, the report says "Catch-up due" once the last catch-up is more than 14 days old. It also lists every Bandcamp feed that catch-up didn't rebuild, usually because that source failed, with the date of the copy it still serves.

The route reads only the cache volume. State written before success tracking holds only the last attempt, so until every source has been refreshed once the report falls back to each cache file's age. That makes the first reports after this change overstate staleness, never understate it.

### The Status Feed

`bridge-health` only helps when you remember to run it. The bridge therefore also serves the same report as a feed, at `/feeds/<token>/status.rss`, so NetNewsWire itself tells you when the bridge needs something. A hosted `export` includes it as **Feed Booster status**, at the top level of the OPML. Pass `--no-status-feed` to `export-opml` to leave it out.

It is built from the bridge's own records on each request, the same way the health report is, and never fetches upstream. Items have stable identifiers, so each one shows up once:

| Item | When it appears |
| --- | --- |
| Bandcamp feeds caught up on *date* | After each catch-up, listing any Bandcamp feed it didn't rebuild |
| Time to catch up Bandcamp | When the last catch-up is more than 14 days old |
| *N* generated feeds need attention | When a feed has failed three refreshes in a row, or has nothing to serve. A new item appears only when that set of feeds changes |

When nothing needs you, the feed holds only the last catch-up. A failure that clears up within a day, and email sources that are merely quiet, stay in `bridge-health`.

The catch-up date is the date most Bandcamp feeds share, not the newest one, so adding a single Bandcamp source and deploying doesn't pass for a catch-up.

Adding the status feed to an existing setup takes one export. Run `./scripts/netnewswire_workflow.sh export` after deploying a bridge that serves it, then import the new OPML or run `repair-netnewswire`. `verify-netnewswire` reports the status feed as missing until you do.

### Seeds Come From Your Checkout

Deploys bundle `exports/generated` from the local checkout as seeds. A source the bridge has never refreshed serves that copy, which is only as fresh as your last local refresh. A source with no local file has nothing to serve until its first scheduled refresh.

A source with both copies serves the cached one, unless the seed's `lastBuildDate` is later. Then the seed wins until the next refresh rewrites the cache. That is what lets a local refresh plus a redeploy update feeds whose refresh is off. Only generated Bandcamp feeds carry a `lastBuildDate` today; for every other source the cache always wins.

`catch-up` relies on this: it refreshes Bandcamp locally right before deploying, so those seeds start current. For other sources, an old seed matters only until the bridge's first refresh replaces it. Stopping the bundling of local files altogether, and seeding new sources from their first scheduled refresh, is a possible future change: it would trade a short "waiting for its scheduled refresh" window for never serving a months-old copy as if it were current. `bridge-health` shows how many sources are on seed copies, so that choice can be judged against real numbers.

## Catching Up Bandcamp

Since October 2026 Bandcamp has answered every request from Modal with a bot check, a "Client Challenge" page, while the same requests from a home connection still get real pages. Assume other cloud hosts get the same. This tool never tries to get past the check. So with `RSS_BRIDGE_BANDCAMP_REFRESH=off` the bridge sends Bandcamp nothing, and Bandcamp feeds move only when you catch them up from your Mac:

```bash
./scripts/netnewswire_workflow.sh catch-up
```

`catch-up` rebuilds every Bandcamp feed on this Mac (`refresh-bandcamp-local-feeds`), then deploys, so each rebuilt feed replaces the bridge's older copy. Before refreshing anything, it checks that the deploy can run, because a large collection takes a while: about a second per source, plus one discography lookup each. It saves progress every 20 sources, so an interrupted run loses little. If Bandcamp answers your Mac with a bot check too, it stops and deploys nothing. A single source that fails doesn't stop the run; that feed keeps serving its last good copy.

Every two weeks or so is plenty for new releases. The [status feed](#the-status-feed) tells you in NetNewsWire when a catch-up is due, and so does `bridge-health`. `./scripts/netnewswire_workflow.sh status` shows both views at once: how old the feeds on this Mac are, then the bridge's report. A feed that shows up in "missed by the last catch-up" run after run keeps failing. The catch-up output names it on a `FAILED` line, along with the kind of error.

## Email Sources (Optional, Future)

**Status: groundwork for a future feature, not usable yet.** The bridge's side is built and tested: the inbound route, sender checks, sanitizer, and feed. The piece that delivers mail to it is not included yet. That piece is a mail-host hook, such as a Cloudflare Email Worker. Without it, an email source cannot receive mail end to end. Neither the Mac-only nor the hosted setup needs any of this. With no secret set, the inbound route rejects every request, so deploying the bridge without configuring email is safe.

Once the hook exists, you'll need a domain of your own on Cloudflare, Email Routing on a subdomain kept for feeds, and one secret. If you only want newsletters as feeds today, a hosted email-to-feed service such as [Kill the Newsletter](https://kill-the-newsletter.com/) does that with no setup. The rest of this section documents the design, for anyone building the hook.

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
