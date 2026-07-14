# Contributing

NetNewsWire-Feed-Booster is an independent companion tool. It is not a fork of NetNewsWire and does not share its maintainership.

## Good contributions

- A source adapter that converts a public, user-controlled source into a standards-compliant RSS feed.
- A parser fixture plus a test for a documented source shape.
- A privacy, portability, or accessibility improvement.
- Documentation that makes the local-first setup easier to understand.

## Before opening a pull request

Open an issue first for a new source type or architecture change. Include the source's public URL shape, an example response saved without personal data, the expected feed behavior, and any rate-limit or terms-of-use concerns.

Do not submit personal OPML exports, private feed URLs, tokens, cookies, saved account pages, or source lists that reveal someone else's subscriptions. Tests must use synthetic or openly shareable fixtures.

Keep the project narrow: it manages source metadata and produces importable feeds; NetNewsWire and other readers remain the reading UI.
