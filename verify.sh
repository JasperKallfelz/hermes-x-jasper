#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run every check this repo has, in the order they are cheapest to fix.
#
#   ./verify.sh              # everything (the patch check needs network)
#   ./verify.sh --offline    # skip the upstream clone + patch check
#   HERMES_VERIFY_UPSTREAM_REPO=/path/to/mirror ./verify.sh
#
# The patch check clones upstream at the pinned commit into a temp dir and runs
# `git apply --check --whitespace=error-all`. That is the one test that proves the starter still works
# against the real repo, so it is on by default and only skipped explicitly.
# ---------------------------------------------------------------------------
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR" || exit 1

UPSTREAM_REPO="${HERMES_VERIFY_UPSTREAM_REPO:-https://github.com/NousResearch/hermes-agent}"
PINNED_COMMIT="3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
PATCH_FILE="$REPO_DIR/patches/voice-and-desktop-features.patch"

OFFLINE=0
[ "${1:-}" = "--offline" ] && OFFLINE=1

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
FAILURES=0

step() { printf '\n%s==> %s%s\n' "$CYAN" "$*" "$NC"; }
pass() { printf '%s  PASS%s %s\n' "$GREEN" "$NC" "$*"; }
fail() { printf '%s  FAIL%s %s\n' "$RED" "$NC" "$*"; FAILURES=$((FAILURES + 1)); }
skip() { printf '%s  SKIP%s %s\n' "$YELLOW" "$NC" "$*"; }

# --- 1. shell syntax -------------------------------------------------------
step "Shell syntax"
for script in setup.sh verify.sh scripts/gitleaks_scan.sh; do
  if bash -n "$script" 2>/dev/null; then pass "bash -n $script"; else
    bash -n "$script"; fail "bash -n $script"
  fi
done
if ! command -v shellcheck >/dev/null 2>&1; then
  fail "shellcheck missing — install it first"
elif shellcheck -S warning setup.sh verify.sh scripts/gitleaks_scan.sh; then
  pass "shellcheck"
else
  fail "shellcheck"
fi

# --- 2. python compiles ----------------------------------------------------
step "Python syntax"
if PYTHONPYCACHEPREFIX=/tmp/hermes-coder-pycache \
    python3 -m compileall -q scripts tests second-brain/src second-brain/tests \
      coder-stack/bin coder-stack/tests >/dev/null; then
  pass "compileall scripts tests second-brain coder-stack"
else
  fail "compileall found a syntax error"
fi

# --- 3. tests --------------------------------------------------------------
step "Tests"
if python3 -c 'import pytest, yaml' 2>/dev/null; then
  ROOT_TESTS=0
  CODER_TESTS=0
  if python3 -m pytest tests second-brain/tests -q; then
    ROOT_TESTS=1
  fi
  CODER_PYTHON=python3
  CODER_TEST_PATH=$PATH
  if [ "$(uname -s)" = "Darwin" ] && [ -x /usr/bin/python3 ]; then
    # The authoritative coder-stack CI target on macOS is Apple Python 3.9.
    # The fixture narrowly holds only bounded-output Git group leaders long
    # enough for Darwin process-identity capture; ordinary Git is undelayed.
    CODER_PYTHON=/usr/bin/python3
    CODER_TEST_PATH="$REPO_DIR/tests/fixtures/darwin-git-bin:/usr/bin:/bin:/usr/sbin:/sbin"
  fi
  if (cd coder-stack && PATH="$CODER_TEST_PATH" \
      PYTHONPYCACHEPREFIX=/tmp/hermes-coder-pycache \
      "$CODER_PYTHON" -m unittest discover -s tests -q); then
    CODER_TESTS=1
  fi
  if [ "$ROOT_TESTS" -eq 1 ] && [ "$CODER_TESTS" -eq 1 ]; then
    pass "root, Second Brain, and vendored coder-stack test suites"
  else
    fail "test suite"
  fi
else
  fail "pytest or PyYAML missing — install them first: pip install pytest pyyaml"
fi

# --- 4. leak audit ---------------------------------------------------------
step "Public audit"
if python3 scripts/audit_public.py "$REPO_DIR" --history; then
  pass "no secrets/PII/local paths"
else
  fail "audit_public found something — do not publish"
fi

# --- 5. gitleaks secret-scan gate (current tree + full history) ------------
step "Gitleaks secret-scan gate"
if command -v gitleaks >/dev/null 2>&1; then
  if bash "$REPO_DIR/scripts/gitleaks_scan.sh"; then
    pass "gitleaks: current tree and history clean"
  else
    fail "gitleaks found secrets"
  fi
else
  fail "gitleaks missing — install pinned v8.30.1"
fi

# --- 6. patch applies to a fresh pinned upstream ---------------------------
step "Patch applies to upstream @ $PINNED_COMMIT"
if [ "$OFFLINE" -eq 1 ]; then
  skip "--offline"
elif ! command -v git >/dev/null 2>&1; then
  fail "git not on PATH"
else
  TMP_DIR="$(mktemp -d)"
  # shellcheck disable=SC2064  # expand TMP_DIR now, not at trap time
  trap "rm -rf '$TMP_DIR'" EXIT
  if ! git clone --quiet "$UPSTREAM_REPO" "$TMP_DIR/hermes" 2>/dev/null; then
    fail "could not clone $UPSTREAM_REPO (network required; re-run with --offline to skip)"
  elif ! git -C "$TMP_DIR/hermes" checkout --quiet --detach "$PINNED_COMMIT" 2>/dev/null; then
    fail "pinned commit $PINNED_COMMIT not found upstream"
  elif git -C "$TMP_DIR/hermes" apply --check --whitespace=error-all "$PATCH_FILE"; then
    pass "git apply --check --whitespace=error-all"
  else
    git -C "$TMP_DIR/hermes" apply --check --whitespace=error-all -v "$PATCH_FILE" 2>&1 | tail -20
    fail "patch no longer applies to the pinned commit"
  fi
fi

# --- summary ---------------------------------------------------------------
echo
if [ "$FAILURES" -eq 0 ]; then
  printf '%sAll checks passed.%s\n' "$GREEN" "$NC"
  exit 0
fi
printf '%s%d check(s) failed.%s\n' "$RED" "$FAILURES" "$NC"
exit 1
