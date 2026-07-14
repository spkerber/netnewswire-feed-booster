# Setup

## 1. Install NetNewsWire

Download [NetNewsWire](https://netnewswire.com/) for Mac, iPhone, or iPad. This repository manages subscription metadata and OPML; NetNewsWire is the reader.

- `On My Mac` keeps subscriptions on one Mac. Use it for local-only reading or initial testing.
- `iCloud` syncs subscriptions and reading state across Apple devices. Import into the iCloud account if that is the account you want to manage.

For a large first import, let NetNewsWire finish syncing before making additional changes. See the [NetNewsWire FAQ](https://netnewswire.com/frequently-asked-questions.html) and [iCloud optimization guide](https://netnewswire.com/help/optimize-icloud.html).

## 2. Create A Private Working Copy

```bash
git clone <repo-url>
cd netnewswire-feed-booster
export PYTHONPATH=src
./scripts/bootstrap_profile.sh me --force
export RSS_PROFILE=me
make test
```

The bootstrap script creates profile-specific `sources`, `subscription-history`, and `profiles` files such as `data/sources.me.json`. These files are ignored by default, and the CLI discovers them automatically when `RSS_PROFILE=me`; the committed generic files remain safe starter examples.

Commit your personal copy privately at first. A populated source registry and OPML export can reveal interests, habits, and private feed URLs.

## 3. Import And Export

Export the NetNewsWire account you want to manage as OPML, save it to `imports/netnewswire.opml`, then run:

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster import-opml imports/netnewswire.opml --profile "$RSS_PROFILE"
PYTHONPATH=src python3 -m netnewswire_feed_booster export-opml --profile "$RSS_PROFILE" --out "exports/${RSS_PROFILE}-netnewswire.opml"
```

Import `exports/${RSS_PROFILE}-netnewswire.opml` into the same NetNewsWire account. Do not combine an `On My Mac` export with an iCloud import unless that move is intentional.

## 4. Optional Hosted Generated Feeds

Most source feeds are already HTTPS and need no host. Set up a bridge only when a generated local feed needs HTTPS for NetNewsWire reliability. Follow [Hosted Bridge](hosting.md) when that applies.
