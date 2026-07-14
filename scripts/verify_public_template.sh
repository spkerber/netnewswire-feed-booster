#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch_dir="$(mktemp -d "${TMPDIR:-/tmp}/netnewswire-feed-booster-public.XXXXXX")"
candidate_dir="${scratch_dir}/candidate"

cleanup() {
  rm -rf "${scratch_dir}"
}
trap cleanup EXIT

"${repo_root}/scripts/prepare_public_clone.sh" "${candidate_dir}" >/dev/null
git -C "${candidate_dir}" init -q
git -C "${candidate_dir}" add --all
git -C "${candidate_dir}" diff --cached --check

(
  cd "${candidate_dir}"
  PYTHONPATH=src python3 -m unittest discover -s tests
)

if find "${candidate_dir}/data" -maxdepth 1 -type f \( \
  -name 'sources.*.json' -o \
  -name 'subscription-history.*.json' -o \
  -name 'profiles.*.json' -o \
  -name 'private*' \
\) -print -quit | grep -q .; then
  echo "Public template contains a private data file." >&2
  exit 1
fi

personal_pattern="spen""cer|spk""erber|personal-""productivity|/Us""ers/"
if rg -n "${personal_pattern}" "${candidate_dir}" \
  --glob '!*.pyc' --glob '!*.git/*'; then
  echo "Public template contains a personal identifier or local path." >&2
  exit 1
fi

echo "Public template passes tests and privacy checks."
