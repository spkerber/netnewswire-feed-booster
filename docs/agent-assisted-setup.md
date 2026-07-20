# Agent-Assisted Setup

Use this guide to set up a new personal RSS stack from the public repository with a local coding agent. It is designed for a semi-technical user: the agent handles repeatable local work, while the user retains control of accounts, tokens, deployments, and NetNewsWire imports.

## What This Repository Does

- Keeps a private, portable registry of your feeds.
- Imports and exports OPML for NetNewsWire.
- Generates feeds only for a small number of public sources that do not have useful RSS.
- Optionally serves generated feeds over tokenized HTTPS.

It does not read your NetNewsWire database, manage your accounts, or need a server for normal HTTPS feeds.

## Before You Start

Install or create these yourself:

1. [NetNewsWire](https://netnewswire.com/) on your Mac.
2. Git and Python 3.9 or later.
3. A private working directory or private GitHub repository for your own copy.
4. Modal only if you add generated feeds that need HTTPS hosting.

Choose the NetNewsWire account before importing anything:

- Use `On My Mac` for an isolated trial on one Mac.
- Use `iCloud` only when you are ready to sync subscriptions and reading state between Apple devices.

Start with `On My Mac` if you are migrating a large or unfamiliar source list. Do not import the same OPML into both accounts unless duplication is deliberate.

## Agent Safety Contract

Give an agent access only to your local working copy. Keep private OPML exports, tokenized podcast feeds, cookies, and `.env` files out of chat attachments and public repositories.

An agent may safely do these tasks without modifying external services:

- Create ignored profile files.
- Import your local OPML into the ignored profile registry.
- Normalize categories, inspect duplicate feeds, and validate feeds.
- Generate a candidate OPML file and explain its changes.
- Run tests and the public-template verification script.

Require explicit approval before an agent does any of these tasks:

- Creates or changes a Modal account, app, secret, or volume.
- Deploys code or spends provider credits.
- Imports OPML into NetNewsWire.
- Replaces a NetNewsWire subscription OPML file.
- Deletes sources, rotates a token, or pushes to any Git remote.

Never give an agent a provider password, MFA code, GitHub personal access token, or a paid-feed URL in a public conversation. A local agent can read an ignored `data/private.env` when needed without printing its values.

`list` redacts feed URLs by default. Do not use `list --show-sensitive` in output you plan to paste or share: hosted generated-feed URLs include the access token.

## First-Time Setup

```bash
git clone <public-repository-url> my-rss-stack
cd my-rss-stack
export PYTHONPATH=src
./scripts/bootstrap_profile.sh me
export RSS_PROFILE=me
make test
```

This creates ignored files such as `data/sources.me.json`. Put your real subscriptions only in those profile-specific files. The tracked `data/*.json` files are starter examples for the public template.

Before adding any personal data, verify the cloned project itself:

```bash
./scripts/verify_public_template.sh
```

The check creates a temporary clone from tracked files, runs its tests, and fails if profile data, common personal identifiers, or macOS home-directory paths appear in the candidate.

## Bring Existing Feeds

1. In NetNewsWire, export the account you selected as OPML.
2. Save that export under the ignored `imports/` directory, for example `imports/current.opml`.
3. Ask the agent to import and inspect it locally:

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  import-opml imports/current.opml --profile "$RSS_PROFILE"

PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  list --profile "$RSS_PROFILE"
```

The import is local. It does not modify NetNewsWire or any upstream subscription.

### Rebuild Generated Sources From A Private Reference

For a fresh setup, do not copy the old source registry into the new profile. Use it only as a local, read-only reference to replace legacy Modal and `file://` URLs in the OPML import.

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  migrate-generated-sources /private/path/to/sources.old.json \
  --profile "$RSS_PROFILE"
```

This is a dry run. It reports the old generated sources it recognizes, stale imported feeds it would replace, and conflicts it will refuse to overwrite. If it reports zero conflicts, write the new source metadata:

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  migrate-generated-sources /private/path/to/sources.old.json \
  --profile "$RSS_PROFILE" --apply
```

Then regenerate the actual RSS files. These commands make public network requests, so review their output before exporting OPML or deploying a host:

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  refresh-bandcamp-local-feeds --profile "$RSS_PROFILE"
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  refresh-generated-local-feeds --profile "$RSS_PROFILE"
```

### Reuse An Existing Local Registry Instead

If you are rebuilding from another local checkout, copy the profile files directly instead of publishing or attaching them. In the new private checkout, initialize the profile once, then replace its empty files with your existing local copies:

```bash
./scripts/bootstrap_profile.sh me
cp /path/to/old-checkout/data/sources.me.json data/sources.me.json
cp /path/to/old-checkout/data/subscription-history.me.json data/subscription-history.me.json
cp /path/to/old-checkout/data/profiles.me.json data/profiles.me.json
export RSS_PROFILE=me
```

The registry preserves all source metadata, but `file://` feeds point to the old checkout. Regenerate generated feeds before exporting new OPML:

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  refresh-bandcamp-local-feeds --profile "$RSS_PROFILE"
```

Re-add any NTS or HydeFM generated sources with their original public page URLs, then export fresh OPML. Do not copy old `exports/` files or add profile registries to the public repository.

For a plain list of extra source URLs, save the list under `imports/` and ask the agent to classify it. Direct HTTPS RSS, Atom, and JSON Feeds should stay direct. Public Bandcamp, NTS, and HydeFM sources may need their source-specific commands. Private or paid feeds belong in ignored `data/private-sources.json`; do not send their URLs to a hosted agent.

## Review Before Importing Back

Ask the agent to review the registry before it exports anything. At minimum, it should report duplicate URLs, sources without a category, inactive/unsubscribed sources, and feed-validation failures.

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  audit-sources --profile "$RSS_PROFILE" --limit 25

PYTHONPATH=src python3 -m netnewswire_feed_booster \
  --data "data/sources.${RSS_PROFILE}.json" \
  export-opml --profile "$RSS_PROFILE" \
  --out "exports/${RSS_PROFILE}-netnewswire.opml"
```

Open the candidate OPML in NetNewsWire only after reviewing the agent's summary. Test it in `On My Mac` first when possible. Importing OPML adds subscriptions; it does not erase your old account by itself. Do not use the automated repair command until you have intentionally chosen the target account and made a backup.

## Optional HTTPS Hosting

Skip hosting if every feed is already HTTPS. You need hosting only for generated feeds that NetNewsWire cannot refresh from `file://`.

**Supported path today: Modal.** The included runtime, scheduled refresh, cache volume, and deployment script are tested for Modal. Railway, DigitalOcean, and Cloudflare are not turnkey deployment targets in this repository yet; use them only after adding and testing a provider-specific adapter.

For a fresh Modal setup:

1. Create your Modal account and log in yourself.
2. Copy `examples/private.env.example` to ignored `data/private.env`.
3. Set a unique app, secret, and volume name; set `RSS_PROFILE=me`; point `RSS_SOURCES_FILE` and `RSS_HISTORY_FILE` at your ignored profile files.
4. Generate a long random `RSS_FEED_TOKEN`; do not reuse it as any other password.
5. Deploy only after the agent shows the exact deployment command and you approve it.
6. Set `RSS_FEED_BASE` to the deployed HTTPS base URL, then export hosted OPML.

See [Hosted Bridge](hosting.md) for the environment variables and route model. Tokenized URLs are intentionally not account-grade authentication: do not publish them, and rotate the token after accidental disclosure.

## Agent Handoff Prompt

Give a local agent this prompt after cloning the public repository and placing your files under ignored `imports/`:

```text
Act as a cautious local setup assistant for this NetNewsWire Feed Booster repository.

First, read README.md, docs/setup.md, docs/source-collection.md, and docs/agent-assisted-setup.md. Treat all files in imports/, exports/, data/private*, and data/*.<profile>.json as private. Do not print, commit, upload, or copy their contents outside this workspace.

Work in dry-run mode first:
1. Run tests and inspect the existing profile registry.
2. Import my local OPML only into the local ignored profile registry.
3. Classify new inputs using docs/source-collection.md. Keep native RSS, Atom, and JSON Feed URLs direct; use a generated source only when the documented public source type requires it.
4. Report duplicates, category inconsistencies, invalid feeds, generated-feed candidates, and any source requiring manual review.
5. Produce a candidate OPML and summarize exactly what would change in NetNewsWire.

Do not deploy, create provider resources, import OPML into NetNewsWire, replace any NetNewsWire file, delete sources, rotate tokens, commit, or push without asking me first and showing the exact command and expected effect.

If generated HTTPS feeds are required, use the bundled Modal path only. Read ignored data/private.env locally when I authorize deployment, but never display secret values.
```

## Friend Trial Checklist

1. Give your friend a fresh public clone, not your working repository.
2. Ask them to run `./scripts/verify_public_template.sh` before entering data.
3. Have them use a temporary `On My Mac` account first.
4. Let their agent run only the dry-run portion of the handoff prompt.
5. Review whether the agent asks for approval at every external or destructive step.
6. Record unclear commands, missing explanations, and any time private data is exposed in output. Treat those as release blockers.
