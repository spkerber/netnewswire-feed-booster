# Writing A Source Adapter

Add an adapter only when a public source has no useful native RSS, Atom, or JSON Feed. Prefer direct feed discovery first: direct feeds are simpler, preserve the publisher's intended format, and do not consume bridge resources. If the source does have a stable native feed URL — even if it takes a resolve step, an ID lookup, or a domain template to find it — see [Writing A Direct Feed Source](writing-a-direct-feed-source.md) instead.

Reddit is usually a direct-feed case rather than an adapter case. Use its public `.rss` URLs where available. A source such as Mixcloud needs an adapter because the public profile does not advertise a standard feed but has a stable public cloudcasts API.

## Adapter Contract

Every generated source belongs in `generated_adapters.py`. An adapter must define:

- A primary `source_label` for persisted source metadata and any deliberate legacy labels.
- An exact public source URL shape, validated before any fetch.
- The approved upstream host or suffix allowlist.
- A deterministic upstream request URL.
- A transformation from the public response to valid RSS.
- `hosted_route = "generated"`. Every adapter shares this one route and cache; there is no per-adapter route to choose.

The Modal bridge only recognizes adapters in this registry. It serves cached or seeded RSS to readers and never fetches upstream during a reader request. The scheduled job performs the upstream fetch, uses conditional HTTP validators when available, and has a bounded source count per run.

Sharing a route does not mean sharing a refresh path. If your source needs more than one fetch, pagination, or per-source caps, the shared `render(source, content) -> str` signature won't fit — Bandcamp is the existing example, with its own scheduled refresh function (`refresh_bandcamp_cache` in `modal_bandcamp_app.py`) dispatched by adapter identity (`adapter is BANDCAMP_ADAPTER`), not by `hosted_route`. Follow that pattern rather than routing your adapter's sources through the generic `refresh_generated_cache` path if a single fetch can't produce the full feed.

## Mixcloud Example

`mixcloud-local-generated` accepts only a public profile URL with one path segment, such as:

```text
https://www.mixcloud.com/example/
```

The adapter converts it to:

```text
https://api.mixcloud.com/example/cloudcasts/?limit=100
```

Its allowlist contains only `api.mixcloud.com`. Profile URLs with an extra path, query string, credentials, a non-HTTPS scheme, or another host are rejected before a fetch or deployment can use them.

## Webpage Recipe Example

Do not create a full provider adapter when the reusable behavior is “turn one difficult public page into RSS.” Add a `WebpageFeedRecipe` in `webpage_recipes.py` instead.

Each recipe defines:

- The exact public site hosts and path prefixes it accepts.
- Whether a query string is part of the supported public page.
- The item and image hosts its parser may emit.
- A parser that returns normalized feed items.
- Date formatting for that page.

`WEBPAGE_ADAPTER` owns the shared fetch and RSS-rendering path. Each recipe declares its exact fetch hosts and builds the upstream URL. The HydeFM recipe fetches its public archive page directly. A future recipe may use another reviewed public representation, but it must name the exact fetch hosts. An arbitrary URL never becomes a fetch target.

HydeFM archives are the first recipe because the page has stable public updates but no useful native feed for this workflow. Add the next difficult website as another recipe. Do not copy the shared rendering, URL validation, or fetch-policy logic into a site-named module.

## Implementation Checklist

1. Confirm that the source has no useful native feed with `discover-feed` and document why a generated feed is justified.
2. Check the source's public terms, rate limits, and access controls. Do not add authenticated, paid, private, or anti-bot-bypass sources.
3. Choose the smallest extension: a webpage recipe for a difficult public page, or a full adapter for a stable provider API/source family.
4. Add a synthetic or openly shareable parser fixture. Never commit an individual's subscriptions, OPML, tokens, cookies, or saved account pages.
5. Test rejected URL shapes, the approved upstream allowlist, RSS output, OPML rewriting, and cache-only reader behavior.
6. Update `docs/source-types.md`, `docs/operations.md`, and this guide with the source's polling and privacy implications.
7. Run `make test` and `./scripts/verify_public_template.sh` before opening an issue or pull request.

Open an issue before adding a new adapter. Include the public URL shape, fixture provenance, expected RSS item identity, provider load estimate, and any likely breakage risk if the source changes its page or API.
