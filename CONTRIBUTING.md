# Contributing

NetNewsWire-Feed-Booster is an independent companion tool. It is not a fork of NetNewsWire and does not share its maintainership.

## Good contributions

- A source adapter that converts a public, user-controlled source into a standards-compliant RSS feed.
- A parser fixture plus a test for a documented source shape.
- A privacy, portability, or accessibility improvement.
- Documentation that makes the local-first setup easier to understand.

## Development requirements

Python 3.9 or later. The tool and its tests use the standard library, so there is nothing to install before running:

```bash
make test
```

CI runs that suite on Python 3.9 and 3.13 on Linux and on Python 3.13 on macOS. `pyproject.toml` sets the floor at 3.9, so avoid syntax newer than that — a `match` statement or a PEP 604 `int | None` union evaluated at runtime will pass locally on a current Python and break the 3.9 job. Modules that annotate anything start with `from __future__ import annotations`, which defers annotations and keeps modern typing syntax legal on 3.9.

Two things need more than Python. `scripts/verify_public_template.sh` uses [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) for its privacy checks. The hosted bridge needs the `modal` extra, installed into its own virtual environment as [Hosted Bridge](docs/hosting.md) describes.

## Before opening a pull request

Open an issue first for a new source type or architecture change. Include the source's public URL shape, an example response saved without personal data, the expected feed behavior, and any rate-limit or terms-of-use concerns.

Follow [Writing A Source Adapter](docs/writing-a-source-adapter.md). New adapters must validate their source URL, use a specific upstream host allowlist, and prove through tests that reader requests serve cache only.

Do not submit personal OPML exports, private feed URLs, tokens, cookies, saved account pages, or source lists that reveal someone else's subscriptions. Tests must use synthetic or openly shareable fixtures.

Keep the project narrow: it manages source metadata and produces importable feeds; NetNewsWire and other readers remain the reading UI.
