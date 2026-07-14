#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-me}"
MODE="${2:-}"

if [[ "${PROFILE}" == "" ]]; then
  echo "Usage: $0 PROFILE_ID [--force]" >&2
  exit 2
fi

if [[ "${MODE}" != "" && "${MODE}" != "--force" ]]; then
  echo "Usage: $0 PROFILE_ID [--force]" >&2
  exit 2
fi

mkdir -p data

write_file() {
  local path="$1"
  local content="$2"
  if [[ -e "${path}" && "${MODE}" != "--force" ]]; then
    echo "Refusing to overwrite ${path}. Re-run with --force if this is a fresh clone or private duplicate." >&2
    exit 1
  fi
  printf "%s\n" "${content}" > "${path}"
}

write_file "data/sources.${PROFILE}.json" '{
  "schema_version": 1,
  "sources": []
}'

write_file "data/subscription-history.${PROFILE}.json" '{
  "schema_version": 1,
  "entries": []
}'

write_file "data/profiles.${PROFILE}.json" "{
  \"schema_version\": 1,
  \"profiles\": [
    {
      \"id\": \"${PROFILE}\",
      \"display_name\": \"${PROFILE}\",
      \"default_reader\": \"NetNewsWire\",
      \"devices\": [
        {
          \"id\": \"mac\",
          \"label\": \"Mac\",
          \"reader\": \"NetNewsWire\"
        }
      ]
    }
  ]
}"

echo "Initialized data files for RSS_PROFILE=${PROFILE}."
echo "Run: export RSS_PROFILE=${PROFILE}"
