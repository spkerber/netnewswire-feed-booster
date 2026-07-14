# NetNewsWire Feed Booster

> Planned public release name: **NetNewsWire-Feed-Booster**. This is an independent companion tool, not a NetNewsWire fork or official integration.

Local-first source management for NetNewsWire. Keep a portable subscription registry, generate RSS only where a source does not offer a useful feed, and export clean OPML back to your reader.

NetNewsWire remains the reader. This project does not store articles, scrape private accounts, or change upstream subscriptions.

## Start Here

```bash
git clone <repo-url>
cd netnewswire-feed-booster
export PYTHONPATH=src
./scripts/bootstrap_profile.sh me --force
export RSS_PROFILE=me
make test
```

Import an existing NetNewsWire OPML, then export a cleaned copy:

```bash
PYTHONPATH=src python3 -m netnewswire_feed_booster import-opml imports/netnewswire.opml --profile "$RSS_PROFILE"
PYTHONPATH=src python3 -m netnewswire_feed_booster export-opml --profile "$RSS_PROFILE" --out "exports/${RSS_PROFILE}-netnewswire.opml"
```

Import the output into the same NetNewsWire account you exported from. Use `On My Mac` for a local-only setup or early testing; use `iCloud` when you want subscriptions and reading state synced across Apple devices.

## Daily Workflow

```bash
make test
make export PROFILE="$RSS_PROFILE"
PYTHONPATH=src python3 -m netnewswire_feed_booster list --profile "$RSS_PROFILE"
PYTHONPATH=src python3 -m netnewswire_feed_booster unsubscribe source-id --reason "Too noisy" --profile "$RSS_PROFILE"
```

Direct HTTPS feeds should go straight to NetNewsWire. Run an HTTPS bridge only for locally generated feeds, such as Bandcamp, when your reader cannot reliably refresh `file://` URLs.

## Layout

- `data/`: committed starter data plus private, profile-specific working registries.
- `src/`: source registry, OPML export, the CLI, and small modules for HTTP, Bandcamp, Substack, YouTube, SoundCloud, and podcasts.
- `scripts/`: bootstrap, hosted export, and explicit NetNewsWire repair workflows.
- `docs/`: focused setup, operations, source-type, hosting, and public-release references.
- `imports/` and `exports/`: ignored local input and generated output.

## Documentation

- [Setup](docs/setup.md): NetNewsWire accounts, profiles, imports, and first export.
- [Agent-Assisted Setup](docs/agent-assisted-setup.md): safe first-time migration, source review, and Modal deployment boundaries.
- [Operations](docs/operations.md): everyday commands, auditing, reconciliation, and drift repair.
- [Source Types](docs/source-types.md): which sources stay direct and which need generated RSS.
- [Hosted Bridge](docs/hosting.md): tokenized generated feeds and supported host options.
- [Public Release](docs/public-release.md): the safe cloning and publishing checklist.

## Privacy Boundary

The tracked starter files are safe examples. Keep real registries in profile-specific files such as `data/sources.me.json` and `data/subscription-history.me.json`; `.gitignore` excludes them by default. Keep tokens and subscriber-only feeds in ignored private files. OPML exports, generated RSS, imported pages, and logs are local artifacts, not source files. `bootstrap_profile.sh` creates the ignored profile files you actually edit.

To prepare a candidate for the separate public repository:

```bash
./scripts/prepare_public_clone.sh ../NetNewsWire-Feed-Booster
```

The script copies reusable tracked files while excluding every profile-specific registry and local artifact. Review the copy before publishing.

## License And Community

This project is [MIT licensed](LICENSE). NetNewsWire is a separate project and remains the reading UI. See [CONTRIBUTING.md](CONTRIBUTING.md) for privacy and public-source requirements, and [docs/netnewswire-community-note.md](docs/netnewswire-community-note.md) for an accurate community post.
