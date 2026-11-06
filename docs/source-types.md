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

- Bandcamp artist, label, and fan pages use a per-source local feed. The root page is canonical; do not use an album URL as the source. An artist or label `/music` page lists releases without dates, so each refresh that fetches a changed page also reads the artist's discography from `bandcamp.com/api/mobile/24/band_details`: one request per artist, never one per release. Those dates become the items' `pubDate`, and the item cap keeps the newest releases. If the lookup fails, the feed still renders, undated and in page order. A hosted refresh that gets a 304 for an unchanged page makes no lookup.
- Some Bandcamp storefronts on a custom domain send this tool into a redirect loop: the custom domain redirects to the artist's `bandcamp.com` address, which redirects straight back. For those, `refresh-bandcamp-local-feeds` builds the feed from the artist's discography on `bandcamp.com` and never requests the storefront. It needs the source's Bandcamp band id. `import-bandcamp-following` records it for the sources it imports, and `import-bandcamp-following <your fan page URL> --band-ids-only` adds it to sources already in the registry without changing anything else. Releases the feed already links keep their links, so nothing reappears as unread. Each new release costs one `tralbum_details` lookup, at most 10 per source per refresh. The rest wait for the next refresh, and so does everything after a lookup Bandcamp drops.
- NTS show pages and public Mixcloud profiles use generated RSS because they do not provide a reliable first-party feed for this workflow.
- A registered webpage recipe can cover a stable public page that has useful updates but no usable native feed or API.
- Planned: email sources, which would turn a newsletter with no feed into one, with mail sent to the source's own private address becoming its items. The bridge's side exists, but the piece that delivers mail to it does not, so they can't be used yet. See [Email Sources](email-sources.md).

Generated feeds are stored under ignored `exports/` paths and are rewritten to tokenized HTTPS URLs only when exported through the hosted bridge. They change only when something rebuilds them. On one Mac that's `refresh-local`. With the hosted bridge, the bridge refreshes everything except Bandcamp, which `catch-up` rebuilds from your Mac. See [Two Setups For Generated Feeds](../README.md#two-setups-for-generated-feeds).

## Webpage Recipes

A webpage recipe is a small, reviewed parser for one exact public URL shape. It declares the permitted page, item, image, and fetch hosts before any request runs. The HydeFM archive, the OSGH NEON SHRINES show page, the Mailchimp Engineering blog, and the Langfuse blog are the current recipes. HydeFM is the example source, not the name of the feature.

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
