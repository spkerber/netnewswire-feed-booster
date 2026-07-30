#!/usr/bin/env bash
set -euo pipefail
umask 077

PROFILE="${1:-me}"
MODE="${2:-}"

if [[ "${PROFILE}" == "" ]]; then
  echo "Usage: $0 PROFILE_ID [--force]" >&2
  exit 2
fi

if [[ ! "${PROFILE}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]; then
  echo "Profile IDs must start with a letter or number and contain only letters, numbers, hyphens, or underscores (64 characters maximum)." >&2
  exit 2
fi

if [[ "${MODE}" != "" && "${MODE}" != "--force" ]]; then
  echo "Usage: $0 PROFILE_ID [--force]" >&2
  exit 2
fi

mkdir -p data

sources_path="data/sources.${PROFILE}.json"
history_path="data/subscription-history.${PROFILE}.json"
profiles_path="data/profiles.${PROFILE}.json"

if [[ "${MODE}" != "--force" ]]; then
  for path in "${sources_path}" "${history_path}" "${profiles_path}"; do
    if [[ -e "${path}" ]]; then
      echo "Refusing to overwrite ${path}. Re-run with --force only when intentionally replacing this private profile." >&2
      exit 1
    fi
  done
fi

printf '%s\n' '{
  "schema_version": 1,
  "sources": []
}' > "${sources_path}"

printf '%s\n' '{
  "schema_version": 1,
  "entries": []
}' > "${history_path}"

printf '%s\n' "{
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
}" > "${profiles_path}"

echo "Initialized data files for RSS_PROFILE=${PROFILE}."
echo "Run: export RSS_PROFILE=${PROFILE}"
