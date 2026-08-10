#!/usr/bin/env bash
# Regression tests for scripts/verify_public_template.sh.
#
# These live in a shell script rather than a unittest module on purpose:
# verify_public_template.sh runs the Python suite inside its candidate copy, so a
# test*.py that invoked it would recurse forever. `unittest discover` only
# collects test*.py, so this file never joins that run.
#
# The probe strings below are split across concatenations for the same reason the
# patterns in verify_public_template.sh are: this file ships inside the public
# template, and a contiguous literal would trip the very scans it exists to test.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/verify-public-template-test.XXXXXX")"
trap 'rm -rf "${work_dir}"' EXIT

passed=0
failed=0

report() { # name expected_exit actual_exit output required_text
  local name=$1 want=$2 got=$3 output=$4 needle=$5
  if [[ "${got}" == "${want}" ]] && printf '%s' "${output}" | grep -qF "${needle}"; then
    echo "PASS  ${name}"
    passed=$((passed + 1))
  else
    echo "FAIL  ${name}: expected exit ${want} with '${needle}', got exit ${got}"
    printf '%s\n' "${output}" | tail -5 | sed 's/^/        /'
    failed=$((failed + 1))
  fi
}

# Copy everything git is not ignoring — tracked files plus not-yet-staged ones —
# so these tests exercise the working copy of the scripts and keep working while
# a branch is mid-flight. That set is also exactly what verify_public_template.sh
# insists you stage before it will run.
materialize_repo() { # destination
  local destination=$1
  mkdir -p "${destination}"
  (
    cd "${repo_root}"
    while IFS= read -r -d '' path; do
      mkdir -p "${destination}/$(dirname "${path}")"
      cp -p "${path}" "${destination}/${path}"
    done < <(git ls-files -z --cached --others --exclude-standard)
  )
  git -C "${destination}" init -q
  git -C "${destination}" add --all
}

# A candidate carrying `content` must be rejected with `needle`.
assert_rejects() { # name content needle
  local name=$1 content=$2 needle=$3
  local candidate="${work_dir}/repo-${name}"
  materialize_repo "${candidate}"
  printf '%s\n' "${content}" > "${candidate}/LEAK_PROBE.md"
  git -C "${candidate}" add --all
  local output exit_code
  output="$("${candidate}/scripts/verify_public_template.sh" 2>&1)"
  exit_code=$?
  report "rejects ${name}" 1 "${exit_code}" "${output}" "${needle}"
}

# One unmodified copy backs every test that does not plant a probe.
base_repo="${work_dir}/repo-base"
materialize_repo "${base_repo}"

# A missing dependency must abort instead of letting the check read as "clean".
# PATH holds nothing but bash so the shebang still resolves; /bin is not usable
# here because on Linux it is a symlink to /usr/bin and holds every tool.
only_bash="${work_dir}/only-bash"
mkdir -p "${only_bash}"
ln -s "$(command -v bash)" "${only_bash}/bash"
output="$(PATH="${only_bash}" "${base_repo}/scripts/verify_public_template.sh" 2>&1)"
report "missing dependency aborts" 1 "$?" "${output}" "which is not on PATH"

# A search tool that runs but fails must abort rather than report a clean result.
grep_shim="${work_dir}/grep-shim"
mkdir -p "${grep_shim}"
printf '#!/bin/sh\nexit 2\n' > "${grep_shim}/grep"
chmod +x "${grep_shim}/grep"
output="$(PATH="${grep_shim}:${PATH}" "${base_repo}/scripts/verify_public_template.sh" 2>&1)"
report "failed search aborts" 1 "$?" "${output}" "Refusing to report a clean template"

assert_rejects "personal-identifier" \
  "Written by Spen""cerKerber." \
  "Public template contains a personal identifier."

assert_rejects "home-directory-path" \
  "Run it from /Us""ers/someone/projects/app." \
  "Public template contains a local home-directory path."

assert_rejects "release-placeholder" \
  "Clone it from <repository-""url> to begin." \
  "Public template contains an unfinished release placeholder."

# Control: without a planted probe the same copy must still pass, so the
# rejections above cannot be an unconditional failure.
output="$("${base_repo}/scripts/verify_public_template.sh" 2>&1)"
report "clean template passes" 0 "$?" "${output}" "passes tests and privacy checks"

echo
echo "${passed} passed, ${failed} failed"
[[ "${failed}" -eq 0 ]]
