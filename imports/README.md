# What Goes Here

This is the one place to drop files you export from other apps. If a source below doesn't need a file, don't hunt for one — paste its URL straight into a command instead.

Run every command below from the repo root, with your profile set:

```bash
export PYTHONPATH=src
export RSS_PROFILE=me   # or whatever you named it in bootstrap_profile.sh
```

Nothing in this folder is committed to git except this file — everything you drop here (OPML, CSVs, saved HTML pages, Takeout zips) is private and stays on your Mac. See [Privacy Boundary](../README.md#privacy-boundary).

## Drop a file here

| Source | How to get it | Save it as | Then run |
| --- | --- | --- | --- |
| YouTube (all your subscriptions) | [Google Takeout](https://takeout.google.com/) → deselect everything except **YouTube and YouTube Music** → **All data included** → subscriptions only → **Create export** → download | The `.zip` Takeout gives you, unzipped, or `subscriptions.csv` — any of the three works, don't unzip it or hunt for the CSV yourself | `import-youtube-subscriptions imports/<whatever-you-saved> --profile "$RSS_PROFILE"` |
| Existing reader (NetNewsWire or another app) | In NetNewsWire: **File → Export Subscriptions...** | `imports/netnewswire.opml` | `import-opml imports/netnewswire.opml --profile "$RSS_PROFILE"` |
| Substack (your own subscriber library, saved page) | Log into Substack → go to your **library** page → browser menu **File → Save Page As...** → choose **Webpage, HTML only** | `imports/substack-library.html` | `import-substack-library imports/substack-library.html --profile "$RSS_PROFILE"` |

If the file you have isn't listed above (an OPML from a different reader, a plain text list of channel URLs), it likely still works — see [Collect Sources](../docs/source-collection.md) for the full format list, or just drop it here and ask.

## No file needed — paste a URL into a command instead

These sources don't export a file at all. Copy the public page's URL and run the matching command — nothing to save in this folder.

| Source | What URL to copy | Command |
| --- | --- | --- |
| Bandcamp (one artist, label, or fan page) | The root page, e.g. `https://artist.bandcamp.com/` — not a specific album or track | `subscribe-bandcamp-source <url> --profile "$RSS_PROFILE"` |
| SoundCloud (one account) | Their profile, e.g. `https://soundcloud.com/example` | `subscribe-soundcloud-profile <url> --profile "$RSS_PROFILE"` |
| SoundCloud (everyone a profile follows) | The profile whose *following list* you want, not the accounts themselves | `import-soundcloud-following <url> --profile "$RSS_PROFILE"` |
| Substack (one publication) | The publication's URL | `subscribe-substack <url> --profile "$RSS_PROFILE"` |
| Substack (your public profile's subscriptions, live) | Your own public Substack profile URL | `import-substack-profile <url> --profile "$RSS_PROFILE"` |
| YouTube (one channel) | The channel's `/@handle` URL | `import-youtube-channel-url <url> --profile "$RSS_PROFILE"` |
| Mixcloud (one profile) | Their profile root, e.g. `https://www.mixcloud.com/example/` | `subscribe-mixcloud-profile <url> --profile "$RSS_PROFILE"` |
| NTS (one show) | The show page, e.g. `https://www.nts.live/shows/example` | `subscribe-nts-show <url> --profile "$RSS_PROFILE"` |
| Podcast (any RSS feed, or an Apple Podcasts link) | The feed URL, or the podcast's `podcasts.apple.com` page | `subscribe-podcast <url> --title "Show Name" --profile "$RSS_PROFILE"` |

## Patreon — the one that needs an extra step

Patreon has no bulk subscriptions export — there's no file to drop here and no single command that finds everything you support. Each creator has to be added one at a time:

1. While logged into Patreon, open the page of a creator you support.
2. Look for their feed link — creators who allow it show an **RSS** link on their page, or you can find it under your own account's membership page for them. It looks like `https://www.patreon.com/rss/<creator>?auth=<token>`.
3. Copy that full URL (it includes a private token — treat it like a password, don't paste it anywhere public) and run:

```bash
subscribe-podcast "<that-url>" --title "Creator Name" --profile "$RSS_PROFILE"
```

Repeat per creator. If a creator doesn't expose an RSS link, they haven't enabled it for their tier — there's no workaround.
