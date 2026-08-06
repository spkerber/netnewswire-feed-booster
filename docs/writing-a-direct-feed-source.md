# Writing A Direct Feed Source

This is the sibling to [Writing A Source Adapter](writing-a-source-adapter.md). That doc covers **generated** sources — a public page with no useful native feed, where the tool has to fetch, parse, and render RSS itself, then optionally host it. A direct feed source is the opposite case: the platform already serves an official RSS/Atom feed at a stable URL. There is nothing to generate and nothing to host — the stored `feed_url` points straight at the platform's own server, for every device, forever, with no refresh job of ours involved.

YouTube, SoundCloud, Substack, and Podcasts are the four direct feed sources today. Their `feed_url`s look like `https://www.youtube.com/feeds/videos.xml?channel_id=...` or `https://feeds.soundcloud.com/users/soundcloud:users:.../sounds.rss` — publisher-hosted, not `exports/generated/*.rss`.

## Why This Doc Exists

The generated family converged on one formal interface (`GeneratedAdapter` in `generated_adapters.py`) because every member solves the identical problem: fetch upstream, render RSS, optionally serve from a cache. The direct family never got that treatment, because each platform's actual problem is different — YouTube needs a channel ID pulled from an `<link rel="alternate">` tag, SoundCloud needs an account ID from a `resolve` API call, Substack needs nothing but a domain, Podcasts needs an Apple lookup or nothing at all. Forcing those into one shared interface would be a false abstraction. But four independently-invented shapes is also a cost: nothing here tells the next contributor (or agent) what's supposed to stay consistent across them versus what's allowed to differ. This doc is that list.

## What Must Be Consistent

**A host allowlist on every fetch that has a fixed target domain.** If your code fetches a URL whose host you know in advance — a profile page on the platform itself, a lookup API — pass `allowed_hosts` or `allowed_suffixes` to `fetch_text`/`fetch_json` so a redirect can't carry the request somewhere unexpected. This is the same posture `GeneratedAdapter` already requires; there was no reason the direct family should have less of it, and until recently it didn't (see `soundcloud.py`'s `SOUNDCLOUD_PAGE_HOSTS`/`SOUNDCLOUD_API_HOSTS` and the `YOUTUBE_HOSTS`/`SUBSTACK_ALLOWED_SUFFIXES` constants in `cli.py` for the current examples). The one legitimate exception is a fetch whose whole point is an arbitrary, caller-supplied host — Podcasts' final feed-URL fetch is the example; it's commented explaining why it stays open.

**The existing CLI naming split**, which is already consistent and should stay that way:
- `subscribe-<platform>-profile` (or `subscribe-<platform>`) for adding one account/channel directly from its URL — independent of anyone's follow graph. `subscribe-soundcloud-profile`, `subscribe-youtube`.
- `import-<platform>-...` for bulk imports from an exported file or someone else's follow graph. `import-youtube-subscriptions`, `import-soundcloud-following`.

**A registry entry in `ADAPTER_KEY_BY_COMMAND`** (`cli.py`), mapping your new command to a `PLATFORM_GROUPS` key. This is what makes folder-default resolution work automatically — without it, sources land at the OPML root with no warning, which is exactly the scattering problem `_apply_default_group` exists to prevent.

**Documentation** in two places: a row in [Collect Sources](source-collection.md)'s accepted-inputs table, and either a file-drop row or a URL-command row in [`imports/README.md`](../imports/README.md), depending on whether your source needs a dropped file or just a pasted URL.

## What's Allowed To Differ

The actual resolution mechanic — how you turn a URL into a `Source` — is platform-specific by nature and shouldn't be forced into a shared shape. Regex-scraping a meta tag, calling a resolve API, templating a domain, calling a third-party lookup API are all legitimate, and trying to unify them would produce a leaky abstraction that doesn't actually save the next platform any work.

That said, prefer a **pure function with injectable network calls** over fetching inline in the CLI handler, when you can: `fetch_soundcloud_profile_source(url, profile, group, fetcher=..., json_fetcher=...)` in `soundcloud.py` is the reference shape — the defaults are host-restricted for real use, but a test can substitute anything without touching the network. YouTube and Substack currently fetch inline in their `cli.py` handlers instead (their host restriction lives at the call site, not inside a reusable function) — that's the existing pattern, not a required one; move toward the injectable shape when you're touching that code anyway, but it's not worth a standalone refactor on its own.

## Checklist For A New Direct Feed Source

1. Confirm the platform serves a stable, official feed URL per account/channel — if not, this is a generated source instead; see [Writing A Source Adapter](writing-a-source-adapter.md).
2. Write a function that resolves a public URL to a `Source`, with `fetcher`/`json_fetcher` params if any network I/O is involved.
3. Restrict every fixed-host fetch with `allowed_hosts`/`allowed_suffixes`. Comment any fetch that's deliberately left open, and why.
4. Add the CLI subcommand following the `subscribe-<platform>-profile` / `import-<platform>-...` split, and register it in `ADAPTER_KEY_BY_COMMAND`.
5. Add tests: the resolution function with fixture data, and a test proving the real (non-test) default fetcher rejects an off-platform host before any network I/O — mirroring `test_fetch_soundcloud_profile_source_default_fetcher_rejects_non_soundcloud_host` in `tests/test_importers.py`.
6. Update `docs/source-collection.md` and `imports/README.md`.
7. Run `make test` before opening an issue or pull request.
