# Source Types

For step-by-step collection and accepted input formats, see [Collect Sources](source-collection.md).

## Direct HTTPS Feeds

Keep these direct. Do not proxy them through the hosted bridge.

- Substack publications: usually `https://<publication>/feed`.
- YouTube channels: `https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>`. Use a public `https://www.youtube.com/@handle` URL with `import-youtube-channel-url`, or a Google Takeout `subscriptions.csv` file with `import-youtube-subscriptions`; do not treat a handle as a stable feed ID.
- SoundCloud public profiles: `https://feeds.soundcloud.com/users/soundcloud:users:<USER_ID>/sounds.rss`.
- Public podcasts: use the publisher's RSS URL.

YouTube handles are not stable feed identifiers. Resolve and store the channel ID. This project deliberately uses YouTube's official channel feed and does not generate a fallback feed when it fails. Private podcasts and tokenized feeds belong only in ignored `data/private-sources.json`.

## Generated RSS

- Bandcamp artist, label, and fan pages use a per-source local feed. The root page is canonical; do not use an album URL as the source.
- NTS show pages and public Mixcloud profiles use generated RSS because they do not provide a reliable first-party feed for this workflow.
- A registered webpage recipe can cover a stable public page that has useful updates but no usable native feed or API.

Generated feeds are stored under ignored `exports/` paths and are rewritten to tokenized HTTPS URLs only when exported through the hosted bridge.

## Webpage Recipes

A webpage recipe is a small, reviewed parser for one exact public URL shape. It declares the permitted page, item, image, and fetch hosts before any request runs. The current HydeFM archive support is one recipe. HydeFM is the example source, not the name of the feature.

Use:

```bash
python3 -m netnewswire_feed_booster \
  subscribe-webpage-feed https://hydefm.com/archives/ \
  --profile "$RSS_PROFILE"
```

Unregistered URLs are rejected. This is not an arbitrary webpage scraper or general-purpose fetcher. Add another recipe in `webpage_recipes.py` only after checking for a native feed, confirming that the page is public, and defining strict host and path boundaries.

## Boundaries

Only use public pages, preserve source attribution, respect rate limits and access controls, and do not use generated feeds to bypass paywalls or authentication. The source registry records reader intent; it never automatically follows or unfollows an upstream account.

See [Writing A Source Adapter](writing-a-source-adapter.md) before adding another generated source type.
