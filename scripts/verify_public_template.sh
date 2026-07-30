#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch_dir="$(mktemp -d "${TMPDIR:-/tmp}/netnewswire-feed-booster-public.XXXXXX")"
candidate_dir="${scratch_dir}/candidate"

cleanup() {
  rm -rf "${scratch_dir}"
}
trap cleanup EXIT

untracked="$(git -C "${repo_root}" ls-files --others --exclude-standard)"
if [[ -n "${untracked}" ]]; then
  echo "Public template has untracked, non-ignored files. Review and stage them before verification:" >&2
  printf '%s\n' "${untracked}" >&2
  exit 1
fi

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

personal_pattern="spen""cerkerber|personal-""productivity"
if rg -ni "${personal_pattern}" "${candidate_dir}" \
  --glob '!*.pyc' --glob '!*.git/*'; then
  echo "Public template contains a personal identifier." >&2
  exit 1
fi

local_path_pattern="/Us""ers/|[A-Za-z]:\\\\Us""ers\\\\"
if rg -n "${local_path_pattern}" "${candidate_dir}" \
  --glob '!*.pyc' --glob '!*.git/*'; then
  echo "Public template contains a local home-directory path." >&2
  exit 1
fi

placeholder_pattern="<repository-""url>|<public-repository-""url>|<add public GitHub ""URL>"
if rg -n "${placeholder_pattern}" "${candidate_dir}" \
  --glob '!*.pyc' --glob '!*.git/*'; then
  echo "Public template contains an unfinished release placeholder." >&2
  exit 1
fi

echo "Public template passes tests and privacy checks."
