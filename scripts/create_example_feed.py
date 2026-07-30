#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from netnewswire_feed_booster.example_bundle import build_example_bundle, open_example_preview
from netnewswire_feed_booster.feed_store import repo_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and validate the public starter feed bundle.")
    parser.add_argument("--profile", default="starter")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--open", action="store_true", dest="open_preview")
    parser.add_argument(
        "--no-network-validation",
        action="store_true",
        help="Skip validation of direct feeds. Bandcamp pages are still fetched to generate their RSS.",
    )
    args = parser.parse_args()

    root = repo_root()
    result = build_example_bundle(
        repo_root=root,
        manifest_path=root / "examples" / "starter-bundle.json",
        profile=args.profile,
        force=args.force,
        validate_network=not args.no_network_validation,
    )
    print(
        f"Example feed ready: {result.source_count} sources "
        f"({result.direct_count} direct, {result.generated_count} generated)."
    )
    print(f"Preview: exports/{result.preview_name}")
    print(f"NetNewsWire import: exports/{result.opml_name}")
    print("No NetNewsWire account or subscription was changed.")
    if args.open_preview:
        open_example_preview(Path(root), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
