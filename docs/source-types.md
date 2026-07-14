# Source Types

## Direct HTTPS Feeds

Keep these direct. Do not proxy them through the hosted bridge.

- Substack publications: usually `https://<publication>/feed`.
- YouTube channels: `https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>`.
- SoundCloud public profiles: `https://feeds.soundcloud.com/users/soundcloud:users:<USER_ID>/sounds.rss`.
- Public podcasts: use the publisher's RSS URL.

YouTube handles are not stable feed identifiers. Resolve and store the channel ID. This project deliberately uses YouTube's official channel feed and does not generate a fallback feed when it fails. Private podcasts and tokenized feeds belong only in ignored `data/private-sources.json`.

## Generated RSS

- Bandcamp artist, label, and fan pages use a per-source local feed. The root page is canonical; do not use an album URL as the source.
- NTS show pages and HydeFM archives use generated RSS because they do not provide a reliable first-party feed for this workflow.

Generated feeds are stored under ignored `exports/` paths and are rewritten to tokenized HTTPS URLs only when exported through the hosted bridge.

## Boundaries

Only use public pages, preserve source attribution, respect rate limits and access controls, and do not use generated feeds to bypass paywalls or authentication. The source registry records reader intent; it never automatically follows or unfollows an upstream account.
