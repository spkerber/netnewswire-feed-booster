# Changelog

Notable changes per release. The [GitHub releases](https://github.com/spkerber/netnewswire-feed-booster/releases) carry the same information with more detail; this file exists so a clone tells you what changed without a network round trip.

> **Version metadata before 0.3.0.** `pyproject.toml` read `0.1.0` through the v0.2.0 release, so no published artifact ever declared `0.2.0`. Use the git tag, not the package metadata, to identify anything older than 0.3.0.

## Unreleased

### Fixed

- Retitling an existing generated source no longer orphans its feed. `subscribe-bandcamp-source --title` derives the source ID from the title, but a source already in the registry keeps its stored ID when it merges, so the RSS was written under a name the registry never pointed at. The hosted bridge seeds and serves strictly by ID, which made this quiet rather than loud: the served feed went on returning the old title, and the cache prune deleted the newly written file for not matching any active source ID. The ID is now settled against the registry before anything is written, so the generated file and `feed_url` stay pinned to `<source_id>.rss`. The same ordering fixes a new source whose slug collides with an existing one, where the file was written under the base name before the merge appended the `-2` suffix.

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
