#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run every check this repo has, in the order they are cheapest to fix.
#
#   ./verify.sh              # everything (the patch check needs network)
#   ./verify.sh --offline    # skip the upstream clone + patch check
#
# The patch check clones upstream at the pinned commit into a temp dir and runs
# `git apply --check`. That is the one test that proves the starter still works
# against the real repo, so it is on by default and only skipped explicitly.
# ---------------------------------------------------------------------------
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR" || exit 1

UPSTREAM_REPO="https://github.com/NousResearch/hermes-agent"
PINNED_COMMIT="b56aafc2ef6befd96ecf00bf4788031cf4be169b"
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
for script in setup.sh verify.sh; do
  if bash -n "$script" 2>/dev/null; then pass "bash -n $script"; else
    bash -n "$script"; fail "bash -n $script"
  fi
done
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning setup.sh verify.sh; then pass "shellcheck"; else fail "shellcheck"; fi
else
  skip "shellcheck not installed (bash -n already ran)"
fi

# --- 2. python compiles ----------------------------------------------------
step "Python syntax"
if python3 -m compileall -q scripts tests second-brain/src second-brain/tests >/dev/null; then
  pass "compileall scripts tests second-brain"
else
  fail "compileall found a syntax error"
fi

# --- 3. tests --------------------------------------------------------------
step "Tests"
if python3 -c 'import yaml' 2>/dev/null; then
  if python3 -m pytest tests second-brain/tests -q 2>/dev/null || python3 -m unittest discover -s tests -q; then
    pass "test suite"
  else
    fail "test suite"
  fi
else
  fail "PyYAML missing — install it first: pip install pyyaml (and pytest)"
fi

# --- 4. leak audit ---------------------------------------------------------
step "Public audit"
if python3 scripts/audit_public.py "$REPO_DIR" --history; then
  pass "no secrets/PII/local paths"
else
  fail "audit_public found something — do not publish"
fi

# --- 5. patch applies to a fresh pinned upstream ---------------------------
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
  elif git -C "$TMP_DIR/hermes" apply --check "$PATCH_FILE"; then
    pass "git apply --check"
  else
    git -C "$TMP_DIR/hermes" apply --check -v "$PATCH_FILE" 2>&1 | tail -20
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
