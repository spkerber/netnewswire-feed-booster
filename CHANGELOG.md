# Changelog

Notable changes per release. The [GitHub releases](https://github.com/spkerber/netnewswire-feed-booster/releases) carry the same information with more detail; this file exists so a clone tells you what changed without a network round trip.

> **Version metadata before 0.3.0.** `pyproject.toml` read `0.1.0` through the v0.2.0 release, so no published artifact ever declared `0.2.0`. Use the git tag, not the package metadata, to identify anything older than 0.3.0.

## Unreleased

### Added

- **Two complete setups for generated feeds, Mac only and hosted bridge, each with its own routine.** The README, Setup, Operations, Hosted Bridge, and refresh-policy docs now present them side by side: where you read, what you need, and how Bandcamp and the other generated feeds stay current in each. New modes of `./scripts/netnewswire_workflow.sh` cover the routines, and `help` lists every mode, grouped by setup:
  - `refresh-local` rebuilds every generated feed on this Mac.
  - `export-local` writes the OPML a Mac-only setup imports.
  - `catch-up` rebuilds the Bandcamp feeds on this Mac and then deploys them. It is the routine for the hosted bridge now that Bandcamp blocks cloud hosts. It checks the deploy can run before starting a refresh that can take the better part of an hour, and it deploys nothing if Bandcamp answers this network with a bot check too.
  - `status` shows how current the generated feeds are in either setup, and adds `bridge-health` when a bridge is configured.
- `local-feed-status` reports, without touching the network, when the generated feeds on this Mac were last rebuilt: for Bandcamp and for everything else, when most of them were last refreshed, how many haven't been rebuilt in 14 days, and how many were never built. It says which command to run when they're behind, and `--list` names the feeds. A refresh rewrites a file only when its source succeeds, so a feed that stays on the list keeps failing.
- `bridge-health` now dates the last Bandcamp catch-up. That's the build date most refresh-off feeds' served copies share, so adding a single source and deploying doesn't pass for a catch-up. After 14 days it says a catch-up is due, and it lists every Bandcamp feed the last catch-up didn't rebuild, with the date of the copy it still serves.
- **A Feed Booster status feed**, so NetNewsWire itself says when the bridge needs you. The bridge serves its health as RSS at `/feeds/<token>/status.rss`, behind the feed token, and a hosted export includes it (`export-opml --no-status-feed` leaves it out). Items have stable identifiers, so each shows up once:
  - one per catch-up, listing the Bandcamp feeds it missed;
  - one when the next catch-up comes due;
  - one whenever the set of feeds needing attention changes. A feed needs attention once it has failed three refreshes in a row, or when it has nothing to serve.

  It is built per request from the bridge's own records and never fetches upstream.
- **Groundwork for email sources: optional, and not usable yet**, because the mail-host hook that delivers mail to the bridge is not included. The docs describe it as a future improvement that neither setup needs. With no secret configured, the new route rejects everything. What exists: email sources turn a newsletter with no feed into one, served by the hosted bridge on the usual `/feeds/<token>/generated/<id>.rss` route. `subscribe-email-source --title --sender-domain` gives a source its own address, a subaddress of `RSS_INBOUND_EMAIL_ADDRESS` with a random part, prints it once, and writes an empty feed so the reader can subscribe before any mail arrives. The address is kept only in the private registry, never in an export or a served feed. The mail host's inbound hook posts each message to a new `POST /inbound/email` route behind its own secret (`RSS_INBOUND_EMAIL_SECRET`, at least 32 characters; without one the route rejects everything). The bridge checks the secret, the size cap (`RSS_INBOUND_EMAIL_MAX_BYTES`, 2 MiB by default), and the address before reading the message, then accepts it only from the source's sender domains as authenticated by the first `Authentication-Results` header from Cloudflare Email Routing (`mx.cloudflare.net`); later copies of that header, which a sender can write, are ignored. A rejected message leaves nothing behind. An accepted one is sanitized, and only the sanitized item is stored: the raw message is never written. The sanitizer, built on the standard library's `HTMLParser` with an allowlist, removes scripts, styles, forms, frames, hidden content, unsubscribe, preference, and account links (judged by their URL, their text, and the text around them), `mailto:` links, the recipient's address in every form found, tracking pixels, remote images from hosts the source does not allow, and tracking query parameters. `bridge-health` lists email sources with the date of their last mail; one with no mail in 30 days is quiet, which is information, not a failure. The scheduled refresh, `refresh-generated-local-feeds`, and `refresh-plan` leave email sources out, since there is nothing upstream to fetch. Cloudflare setup and the Email Worker come separately. See [Email Sources](docs/hosting.md#email-sources-optional-future).
- `RSS_BRIDGE_BANDCAMP_REFRESH=off` turns off the hosted bridge's scheduled Bandcamp refresh, for a host whose network Bandcamp answers with a bot check. Since about 2026-10-01 Bandcamp has answered every request from Modal (artist pages and the discography and fan-collection APIs) with a "Client Challenge" page sent as HTTP 200, while the same requests from a home connection still get real pages. The bridge went on sending about 20 of them an hour for a month. With the setting off, the bridge sends Bandcamp nothing: its feeds serve their last cached or deploy-time copy, `bridge-health` counts them as "refresh off" instead of failing and stale, and `refresh-plan` leaves them out of its capacity check. It defaults to on. The deploy bakes the value into the image, so every function sees the same one, and any value other than on or off fails the deploy.
- Bandcamp's bot-check page is now recognized. It used to parse as a page with no releases and fail as "No Bandcamp items found", which pointed at the parser. It now raises `BandcampBotCheck` before any follow-up request. A hosted refresh run stops at the first one, and `refresh-bandcamp-local-feeds` stops and exits with status 1, because every further request from that network gets the same answer. Neither tries to get past the check.
- **`check-feed-health`** checks every active direct feed in a profile and reports what a reader would actually see. Problems are feeds that are gone, blocked by a bot challenge, closed (a Substack that went invite-only), now redirecting to a web page, failing on a server, network, or certificate error, or `http://`-only. Working feeds are split into active, quiet for one or two years, undated, or empty. A feed reached through a redirect gets a suggested new address. It is read-only, caps parallel requests per host, and retries each problem once after a pause. YouTube's feed endpoint returns 404s and 500s for live channels when it is busy, so a YouTube feed that still fails is called gone only when its channel page says so; otherwise it is reported as unavailable, not as a problem. It prints hosts rather than full URLs unless `--show-sensitive` is passed. `--report` writes every list as Markdown. An empty feed is never counted as a problem, because it is not proof the source is dead.
- `tmp/` is now ignored by Git, since health reports and other local output list personal subscriptions.
- A second webpage recipe, for the NEON SHRINES radio show page at `https://www.osghpress.com/neon-shrines`, which publishes no native feed (Squarespace's `?format=rss` returns 400 for it). An episode's identity is its air date rather than its title, so an edited title does not bring an episode back as unread. Episodes are listed newest first: the page lists each season's episodes oldest first, and the hosted bridge keeps only the first 50 items by default, so page order would drop the newest episodes of a long season. A Google Drive audio link is attached only when exactly one sits inside an episode's own block, because the page sometimes stacks a whole season's buttons under its last episode and guessing would point an episode at the wrong recording. Air dates are read as Pacific, with the show's 4pm slot assumed when no time is listed.
- Generated Bandcamp artist and label feeds now carry release dates. The `/music` page they are built from lists releases without dates in both of its layouts, so a reader dated every release by when it first saw it, and inactive artists could not be told from active ones. Each refresh that fetches a changed page now reads the artist's discography from Bandcamp's `band_details` API: one request per artist, never one per release. It joins dates to releases by ID, using the page owner's band ID rather than a release's own (on a label page, releases carry their artists' IDs). With dates known, the item cap keeps the newest releases instead of the first ones in the artist's chosen page order. The lookup is best effort: if it fails, the feed renders as before, undated and in page order. It runs in `subscribe-bandcamp-source`, `refresh-bandcamp-local-feeds`, the starter import, and the hosted bridge, where a 304 for an unchanged page skips it.
- A webpage recipe for the Mailchimp Engineering blog at `https://mailchimp.com/developer/blog/`, whose own RSS feed (`rss.xml`) now returns 404. The index page still lists every post with its date, summary, and tags, so the recipe reads them from there. Only the tag badges inside a post card count; the filter buttons above the list use the same badge style. Post links are accepted only under `mailchimp.com/developer/blog/`.
- A webpage recipe for the Langfuse blog at `https://langfuse.com/blog`, which publishes no RSS or Atom feed. The blog index lists recent posts as cards, each linking to a dated `/blog/YYYY-MM-DD-slug` path, so the recipe reads the title, summary, category, and authors from each card and dates the post from its path. A highlights row leads the page, so posts are sorted newest first rather than taken in page order, and only dated posts on `langfuse.com` count.
- The hosted bridge now records each generated source's last success, consecutive failures, and a short error (URLs removed) alongside its last attempt. A token-protected `/feeds/<token>/health` page, and `./scripts/netnewswire_workflow.sh bridge-health` to read it, sorts sources into healthy, never attempted, failing, stale, seed copy only, and nothing to serve. Until now a feed that kept failing went on serving an old copy with nothing to show for it: a site behind a bot challenge since July, Bandcamp sources the scheduler never reached, and sources with no copy at all all looked the same as healthy feeds from the reader. The summary logic lives in `bridge_health.py`, with tests, and the route reads only the cache volume.

### Changed

- `examples/private.env.example` now sets `RSS_BRIDGE_BANDCAMP_REFRESH=off`, because Bandcamp answers Modal, the bundled host, with a bot check. A new bridge copied from the example sends Bandcamp nothing and relies on `catch-up` instead. The code default is still on, for a host Bandcamp still answers, and an existing `data/private.env` is unchanged.
- The README no longer says the hosted bridge refreshes every generated feed on its own: it refreshes everything except Bandcamp on a blocked host. `refresh-plan` now names `catch-up` beside the Bandcamp sources it leaves out.
- A deploy's seed copy now replaces an older cached copy. The bridge used to serve any cached copy over the seed, so with Bandcamp refresh off, refreshing locally and redeploying could not reach a source that already had a cached copy, which in one profile was 389 of its Bandcamp sources. The seed now wins when its `lastBuildDate` is later than the cache's. Only generated Bandcamp feeds carry one, so every other source serves its cache as before.
- Removed sources are pruned in the generated-feed refresh job instead of the Bandcamp job, so pruning keeps running with Bandcamp refresh off. It still covers every generated source.

### Fixed

- **Bandcamp storefronts on a custom domain refresh again on a Mac.** Since about August 2026, some have answered every request from this tool with a redirect loop, though a browser still gets the page. The custom domain answers 303 to the artist's `bandcamp.com` address, which answers 301 straight back. Both hosts are within the source's redirect allowlist, so urllib followed the loop until it gave up. Those feeds kept serving their last copy: 17 in one profile had not been rebuilt in three months. `refresh-bandcamp-local-feeds` now builds a custom-domain artist or label from its `band_details` discography on `bandcamp.com` whenever the registry knows its band id, and never requests the storefront.
  - **Links stay stable.** The discography has no release URLs, so a release the feed already links keeps that link and GUID, matched by artwork id, and isn't shown again as unread.
  - **New releases cost one lookup each.** A release the feed hasn't linked yet gets one `tralbum_details` lookup on `bandcamp.com`, paced like every other request and capped at 10 per source per refresh. Any left over wait for a later refresh.
  - **Releases are checked.** A release link must be an album or track page on the storefront's own host or on `bandcamp.com`.
  - **Bot checks still stop the run.** A bot-check page from either API is recognized, and it stops the run as before.
  - **Band ids are recorded.** The registry keeps a Bandcamp source's `bandcamp_band_id` when it is known, and `import-bandcamp-following` now records it. For sources already in the registry, `import-bandcamp-following <url> --band-ids-only` records their ids. It matches by storefront only, never by id alone, and adds or changes nothing else.
  - **Missing ids are named.** A refresh that fails a custom-domain storefront with no id gives that command.
- The README and CONTRIBUTING said `verify_public_template.sh` needs ripgrep. It has used `grep` and `find` since its privacy checks were rewritten, so Git is the only thing to install.
- Generated Bandcamp artist and label feeds no longer leave out the newest releases. A `/music` page renders its first 16 releases as grid items and lazy-loads the rest from `data-client-items`, and the parser read only the lazy-loaded list whenever it was present, so every page with more than 16 releases lost its 16 newest: one label feed held none of the label's 16 latest releases. Grid items now come first, then the lazy-loaded ones, in page order. A grid release on a label page or a collaboration also keeps its own artist, which the page puts inside the title and the parser had been folding into it. A lazy-loaded release that names no artist, as an artist's own releases often do, is now credited to the page's artist instead of rendering as ` - Title`: about one artist feed in five had such items.
- The hosted bridge no longer starves generated sources once a route outgrows its capacity. Each run took due sources in registry order, which is alphabetical by title, so when a route held more sources than its 12-hour window allowed, the top of the alphabet became due again before the scheduler ever reached the rest. In one profile with 1,184 Bandcamp sources the bridge cycled through the first ~260 and the other ~920 kept serving whatever the last deploy had seeded, some of it two months old, with nothing in the reader to show it. Runs now take the longest-waiting due sources first, never-attempted ones before all others, so an over-capacity route refreshes every source on a longer cycle (about every 60 hours for that profile) instead of some of them never.
- Retitling an existing generated source no longer orphans its feed. `subscribe-bandcamp-source --title` derives the source ID from the title, but a source already in the registry keeps its stored ID when it merges, so the RSS was written under a name the registry never pointed at. The hosted bridge seeds and serves strictly by ID, which made this quiet rather than loud: the served feed went on returning the old title, and the cache prune deleted the newly written file for not matching any active source ID. The ID is now settled against the registry before anything is written, so the generated file and `feed_url` stay pinned to `<source_id>.rss`. The same ordering fixes a new source whose slug collides with an existing one, where the file was written under the base name before the merge appended the `-2` suffix.
- The workflow script's `export`, `verify-netnewswire`, and `repair-netnewswire` modes pass their test step again. The script loads `data/private.env` before running the suite, and the tests inherited its settings: `NETNEWSWIRE_OPML` sent seven CLI tests to the real NetNewsWire subscriptions, which macOS refused to open, and `RSS_SOURCES_FILE` and `RSS_HISTORY_FILE` could have pointed others at the real registry and history. Every test module now imports `tests/isolated_env.py` first, which drops those settings for the test run.

## 0.3.1 — 2026-08-11

### Fixed

- `--pause-seconds` now paces every upstream request rather than only the gap between URLs. Verifying a newly added source is a second request for that URL, and it was following the first one immediately, so a long `batch-subscribe` run went out in unthrottled pairs instead of at the rate the flag promised.

### Changed

- The `subscribe-substack` refusal names `--no-verify`. Anyone who meets that check while offline is the least able to go and read about the way out, so it belongs in the message.
- Added `CHANGELOG.md` and a `CONTRIBUTING.md` section explaining how `batch-subscribe` dispatches, since calling `main()` recursively reads as a mistake until you know what it buys.
- The README states that Python 3.9 is the supported floor, not a recommendation. It is the floor because macOS ships it as `/usr/bin/python3`, so most machines already satisfy it and should install nothing.

## 0.3.0 — 2026-08-11

### Added

- **`batch-subscribe`** adds several public sources in one run, from a file of URLs, repeated `--url` flags, or stdin. It detects what each URL is and routes it to the single-URL command it would have gone to anyway, so folder defaults, duplicate checks, and host allowlists behave exactly as they do when those commands are run by hand. Detection covers Bandcamp, YouTube channel pages, SoundCloud, Substack, Mixcloud, NTS shows, and any page already covered by a registered webpage recipe; anything else goes through public feed discovery. A line may end with `--adapter=<adapter>` where detection cannot know what was meant, a podcast RSS URL being the usual case.
- URLs are processed one at a time with a pause between every upstream request. A failure is reported and the run continues, failures are repeated at the end so they can be re-run as a shorter file, and the exit code is nonzero if any failed. A URL already in the registry is skipped rather than refetched, so re-running the same file is cheap and exits zero.

### Changed

- **`subscribe-substack` now requires network access.** It built its feed URL by concatenating whatever it was given with `/feed` and never fetched it, so any string at all could become an active source pointing at a URL nobody had ever requested. It now confirms the URL serves a feed before saving, with redirects pinned to that URL's own host. See *Upgrading* below.
- A batch URL matching a source that was deliberately unsubscribed is reported as previously unsubscribed, naming `set-status`, rather than as "already subscribed". The skip itself is unchanged.
- `pyproject.toml` now carries the real version.

### Fixed

- `batch-subscribe` re-reads each row it adds and confirms the feed resolves. Most commands cannot get this wrong because they had to fetch upstream to build the URL at all, but Substack and YouTube both assemble one without fetching it. A row that fails is reported and left as a `candidate` rather than `active`, so a dead feed cannot reach the next OPML export, and the feed the page actually advertises is named in the failure.
- A URL that is the right site but the wrong page — `substack.com/@handle` today — is refused before any request, naming the shape it should have taken.

### Upgrading

`subscribe-substack` can now fail where it previously always succeeded: with no network, against a publication that is temporarily down, or on an address that never served a feed. That last case is the point of the change. If you script it somewhere offline, pass `--no-verify` to restore the old behavior. `batch-subscribe` takes the same flag to skip its post-add check.

## 0.2.0 — 2026-08-07

### Added

- `import-bandcamp-following` paginates Bandcamp's own following-list API to import every artist, label, and fan a profile follows, handling custom-domain storefronts and id collisions between accounts sharing a display name.
- `subscribe-soundcloud-profile` adds one SoundCloud account from its URL, independent of anyone's follow graph.
- YouTube subscription import accepts a raw Google Takeout `.zip` or its extracted folder and finds `subscriptions.csv` at any depth.
- `imports/README.md` and `docs/writing-a-direct-feed-source.md`.

### Changed

- Bandcamp moved onto the same shared "generated" route and cache as NTS, Mixcloud, and webpage recipes.

### Security

- Every fetch rejects targets resolving to private, loopback, link-local, or cloud-metadata addresses, closing a DNS-rebinding gap.
- The restricted redirect handler is always installed, so revalidation runs on every hop even without a host allowlist.
- Unexpected binary content types are rejected before being decoded as text, and a size-cap bypass in the Bandcamp pagination calls was closed.
- Following-list fields from the Bandcamp and SoundCloud APIs are validated before being built into a URL, and both pagination loops have a hard backstop cap.
- `fastapi` and `modal` floors bumped past a disclosed RCE CVE.

### Fixed

- Both local refresh commands pace requests and checkpoint progress to disk instead of saving once at the end.
- The registry save is atomic, so an interrupted write cannot corrupt the file.
- `import-bandcamp-following` no longer discards a successful large fetch because a later smaller one failed.

## 0.1.0 — 2026-07-30

First public release: a local-first source registry and OPML workflow for NetNewsWire. Keeps publisher feeds direct, generates RSS only for supported public sources that need it, and leaves the final NetNewsWire import under your control. Ships a double-clickable starter import that validates eight public sources and writes a NetNewsWire-ready OPML without importing anything itself.
