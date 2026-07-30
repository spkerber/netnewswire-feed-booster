#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from netnewswire_feed_booster.starter_import import build_starter_import, open_import_report
from netnewswire_feed_booster.feed_store import repo_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate the public starter import.")
    parser.add_argument("--profile", default="starter")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--open", action="store_true", dest="open_report")
    parser.add_argument(
        "--no-network-validation",
        action="store_true",
        help="Skip validation of direct feeds. Bandcamp pages are still fetched to generate their RSS.",
    )
    args = parser.parse_args()

    root = repo_root()
    result = build_starter_import(
        repo_root=root,
        manifest_path=root / "examples" / "starter-import.json",
        profile=args.profile,
        force=args.force,
        validate_network=not args.no_network_validation,
    )
    print(
        f"Starter import built: {result.source_count} feeds "
        f"({result.direct_count} direct, {result.generated_count} generated)."
    )
    print(f"Import report: exports/{result.report_name}")
    print(f"NetNewsWire import: exports/{result.opml_name}")
    print("No NetNewsWire account or subscription was changed.")
    if args.open_report:
        open_import_report(Path(root), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
