#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")" && pwd)"
cd "${repo_root}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it, then double-click this file again."
  read -r -p "Press Return to close."
  exit 1
fi

if ! ./scripts/build_starter_import.sh starter --open; then
  echo
  echo "The starter import was not changed. Read the message above for the exact reason."
  read -r -p "Press Return to close."
  exit 1
fi

echo
echo "The import report opened in your browser. Nothing was imported into NetNewsWire."
read -r -p "Press Return to close."
