#!/usr/bin/env bash
set -euo pipefail

# Every check below shells out to one of these. Without an explicit preflight a
# missing tool makes its check exit non-zero, an `if` condition reads that as
# "found nothing", and the script reports a template it never actually
# inspected.
for required_command in git python3 grep find mktemp; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "verify_public_template.sh requires ${required_command}, which is not on PATH." >&2
    exit 1
  fi
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# prepare_public_clone.sh reads `git ls-files` from the working directory, so
# without this the script would build its candidate from whatever repo the
# caller happened to be standing in and pronounce that one clean.
cd "${repo_root}"

scratch_dir="$(mktemp -d "${TMPDIR:-/tmp}/netnewswire-feed-booster-public.XXXXXX")"
candidate_dir="${scratch_dir}/candidate"

cleanup() {
  rm -rf "${scratch_dir}"
}
trap cleanup EXIT

# grep exits 0 on a match, 1 on no match, and 2 or higher when the search itself
# failed. Only a clean 1 clears the candidate; every other status has to fail the
# verification so a broken search is never read as a clean template.
scan_candidate() {
  local subject=$1
  local pattern=$2
  shift 2

  local matches=""
  local status=0
  matches="$(grep -rIEn "$@" \
    --exclude='*.pyc' \
    --exclude-dir='.git' \
    -e "${pattern}" \
    "${candidate_dir}")" || status=$?

  case "${status}" in
    0)
      printf '%s\n' "${matches}" >&2
      echo "Public template contains ${subject}." >&2
      exit 1
      ;;
    1)
      : # No matches: this check passed.
      ;;
    *)
      echo "Could not scan the public template for ${subject} (grep exited ${status})." >&2
      echo "Refusing to report a clean template from a search that did not run." >&2
      exit 1
      ;;
  esac
}

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

# Assigning the result rather than piping into `grep -q` keeps `set -e` in play:
# a find that fails to run now aborts the script instead of looking like an
# empty result set.
private_data_file="$(find "${candidate_dir}/data" -maxdepth 1 -type f \( \
  -name 'sources.*.json' -o \
  -name 'subscription-history.*.json' -o \
  -name 'profiles.*.json' -o \
  -name 'private*' \
\) -print -quit)"
if [[ -n "${private_data_file}" ]]; then
  echo "Public template contains a private data file: ${private_data_file}" >&2
  exit 1
fi

personal_pattern="spen""cerkerber|personal-""productivity"
scan_candidate "a personal identifier" "${personal_pattern}" -i

local_path_pattern="/Us""ers/|[A-Za-z]:\\\\Us""ers\\\\"
scan_candidate "a local home-directory path" "${local_path_pattern}"

placeholder_pattern="<repository-""url>|<public-repository-""url>|<add public GitHub ""URL>"
scan_candidate "an unfinished release placeholder" "${placeholder_pattern}"

echo "Public template passes tests and privacy checks."
