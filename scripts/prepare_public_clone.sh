#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <empty-public-copy-directory>" >&2
  exit 64
fi

destination=$1
if [[ -e "$destination" ]]; then
  echo "Refusing to write into existing path: $destination" >&2
  exit 65
fi

mkdir -p "$destination"

while IFS= read -r -d '' path; do
  case "$path" in
    data/sources.*.json|data/subscription-history.*.json|data/profiles.*.json|data/private*|exports/*|imports/*|logs/*)
      continue
      ;;
  esac
  mkdir -p "$destination/$(dirname "$path")"
  cp -p "$path" "$destination/$path"
# Include non-ignored working files so verification catches new files before staging.
done < <(git ls-files --cached --others --exclude-standard -z)

cat <<'EOF'
Prepared a public-copy candidate. Review it before publishing:
  cd <destination>
  git init && git add . && git status --short
EOF
