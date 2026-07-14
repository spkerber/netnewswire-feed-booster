# Hosted Bridge

Use an HTTPS bridge only for generated local RSS that NetNewsWire cannot refresh reliably through `file://`. Direct HTTPS feeds stay direct.

The bridge serves tokenized routes such as:

```text
/feeds/<token>/bandcamp/<source-id>.rss
/feeds/<token>/generated/<source-id>.rss
```

Tokenless routes return `404`. This makes stale imports visible in logs rather than accidentally exposing feeds.

## Host Options

[Modal web functions](https://modal.com/docs/guide/webhooks) is the only bundled and tested deployment path. It includes the runtime, scheduled refreshes, and a volume cache.

[Cloudflare Workers](https://developers.cloudflare.com/workers/), [Railway](https://docs.railway.com/deployments), and [DigitalOcean](https://docs.digitalocean.com/) are reasonable future host targets, but this repository does not yet include a Worker handler, generic ASGI service, or container deployment for them. Do not treat those providers as turnkey instructions.

## Modal Setup

```bash
cp examples/private.env.example data/private.env
```

Set `RSS_PROFILE`, `RSS_SOURCES_FILE`, `RSS_HISTORY_FILE`, `RSS_FEED_TOKEN`, and `RSS_FEED_BASE`. The `MODAL_*` values are only for the Modal deployment path; other hosts should use equivalent provider configuration while preserving `RSS_FEED_BASE` and `RSS_FEED_TOKEN`.

```bash
./scripts/netnewswire_workflow.sh deploy-modal
./scripts/netnewswire_workflow.sh export
```

The workflow loads only ignored private environment files, deploys the current source bundle, runs tests, exports hosted OPML, and validates XML. The token prevents casual enumeration; it is not account-grade authentication, so do not publish tokenized URLs.
