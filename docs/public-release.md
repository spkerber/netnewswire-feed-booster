# Public release checklist

Use this checklist when turning this live workspace into the public `NetNewsWire-Feed-Booster` repository.

## What ships

- Source code, tests, documentation, empty/example data, and the MIT license.
- A local-first workflow: import OPML, manage source metadata, export OPML, and optionally host only generated feeds.
- Clear attribution: NetNewsWire is a separate project and remains the reader UI.

## What must not ship

- Any populated profile registry, including `data/sources.<profile>.json`.
- Private feed URLs, tokens, cookies, `data/private*.env`, or deployment secrets.
- Imported OPML, HTML snapshots, exported OPML/RSS, logs, and local Python environments.
- Screenshots or fixtures that expose someone's follows, listening history, purchases, email address, or account identifiers.

## Create the public copy

From this repository, run:

```bash
./scripts/prepare_public_clone.sh ../NetNewsWire-Feed-Booster
cd ../NetNewsWire-Feed-Booster
git init
git add .
git status --short
```

The script copies only tracked reusable files while excluding personal profiles. It refuses to write into an existing directory. Review every staged file before the first commit.

The public copy's `.gitignore` also ignores profile-specific `sources`, `subscription-history`, and `profiles` files so a new user's live registry does not become a default commit candidate.

## Before publishing

1. Run `make test`.
2. Run `./scripts/verify_public_template.sh`.
3. Confirm `git status --ignored --short` shows no personal data staged for the public copy.
4. Search the public copy for names, hostnames, feed tokens, and personal paths.
5. Confirm the repository URL, screenshots, badges, and community note all resolve from a clean candidate.
6. Publish only the reviewed public-copy directory, then create a signed or annotated version tag and GitHub release.

## Support boundary

Generated feeds should use public pages and respect service terms, rate limits, and robots/anti-abuse controls. Do not position generated feeds as an attempt to bypass paywalls, authentication, or access controls. Direct HTTPS feeds should stay direct; run a hosted bridge only when a locally generated feed needs HTTPS for reader compatibility.
