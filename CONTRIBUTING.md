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

## One surprising piece: how `batch-subscribe` dispatches

`batch-subscribe` does not subscribe to anything itself. For each URL it builds the argument list for the single-URL command that URL belongs to and calls `main()` again, in process, then reads the registry to see what changed.

Calling the entry point recursively is unusual enough to look like a mistake, so: it is deliberate, and the alternative is worse. Each subscribe command carries behavior that is easy to forget and hard to re-derive — its own folder default from `_apply_default_group`, its own duplicate rules, its own upstream host allowlist, its own title resolution. Calling the real command inherits all of that by construction. Reaching past it to the adapter functions would mean reimplementing the parts that live in `main()`, and a mixed batch could not pick a different default folder per URL, because `_apply_default_group` keys on `args.command`.

What this costs, and what to preserve if you change it:

- Every batch target must stay a real subparser name in `BATCH_ADAPTER_COMMANDS`, and must keep accepting `--profile` and `--group`. `BATCH_OUT_DIR_COMMANDS` names the subset that also takes `--out-dir`.
- The child's output is captured, so a batch prints one line per URL rather than nine different success messages. Its last line becomes the failure detail when it exits nonzero.
- Whether a URL was added is judged by diffing source ids around the call, not by parsing what the child printed.
- The registry is loaded and saved once per URL. That is what makes progress survive an interrupted run, and it is why a large batch is not instant.

`_subscribe_batch_line` is the whole of it. If you add a source command that belongs in a batch, add it to the dispatch table and give it a test in `tests/test_batch_subscribe.py`; nothing else should need to change.

## Before opening a pull request

Open an issue first for a new source type or architecture change. Include the source's public URL shape, an example response saved without personal data, the expected feed behavior, and any rate-limit or terms-of-use concerns.

Follow [Writing A Source Adapter](docs/writing-a-source-adapter.md). New adapters must validate their source URL, use a specific upstream host allowlist, and prove through tests that reader requests serve cache only.

Do not submit personal OPML exports, private feed URLs, tokens, cookies, saved account pages, or source lists that reveal someone else's subscriptions. Tests must use synthetic or openly shareable fixtures.

Keep the project narrow: it manages source metadata and produces importable feeds; NetNewsWire and other readers remain the reading UI.
