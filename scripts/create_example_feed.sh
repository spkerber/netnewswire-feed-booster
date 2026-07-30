#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
profile="${1:-starter}"
shift || true

if [[ ! "${profile}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]; then
  echo "Profile IDs must start with a letter or number and contain only letters, numbers, hyphens, or underscores." >&2
  exit 2
fi

cd "${repo_root}"
PYTHONPATH=src python3 scripts/create_example_feed.py --profile "${profile}" "$@"
