#!/usr/bin/env bash
# Run local development checks. Release publication uses scripts/release_audit.sh.
set -uo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR" || exit 1
UPSTREAM_REPO="${HERMES_VERIFY_UPSTREAM_REPO:-https://github.com/NousResearch/hermes-agent}"
PINNED_COMMIT="5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
PINNED_TAG="v2026.8.27"
PATCH_FILE="$REPO_DIR/patches/voice-and-desktop-features.patch"
OFFLINE=0
[ "${1:-}" = "--offline" ] && OFFLINE=1
[ "$#" -le 1 ] || { echo "usage: $0 [--offline]" >&2; exit 2; }
FAILURES=0
TMP_DIR=""

# Invoked indirectly by the EXIT trap below.
# shellcheck disable=SC2329
cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf -- "$TMP_DIR"
  fi
}
trap cleanup EXIT

step() { printf '\n==> %s\n' "$*"; }
pass() { printf '  PASS %s\n' "$*"; }
fail() { printf '  FAIL %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
skip() { printf '  SKIP %s\n' "$*"; }

bounded_clone() {
  python3 - 180 git clone --quiet --branch "$PINNED_TAG" --single-branch \
    "$UPSTREAM_REPO" "$TMP_DIR/hermes" <<'PY'
import subprocess
import sys
try:
    result = subprocess.run(
        sys.argv[2:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=int(sys.argv[1]), check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
raise SystemExit(result.returncode)
PY
}

step "Shell syntax and ShellCheck"
if bash -n setup.sh verify.sh scripts/*.sh messaging/*.sh modules/pi-runtime/*.sh; then pass "bash -n"; else fail "bash -n"; fi
if ! command -v shellcheck >/dev/null 2>&1; then
  fail "shellcheck missing — install it first"
elif shellcheck -S warning setup.sh verify.sh scripts/*.sh messaging/*.sh modules/pi-runtime/*.sh; then
  pass "shellcheck"
else
  fail "shellcheck"
fi

step "Python syntax"
if PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/hermes-coder-pycache" \
    python3 -m compileall -q scripts tests second-brain/src second-brain/tests \
      coder-stack/bin coder-stack/tests modules/pi-runtime/pi_module.py; then
  pass "compileall"
else
  fail "compileall"
fi

step "Tests"
if ! python3 -c 'import pytest, yaml' 2>/dev/null; then
  fail "pytest or PyYAML missing — install them first: python3 -m pip install pytest pyyaml"
else
  root_ok=0
  coder_ok=0
  python3 -m pytest tests second-brain/tests -q && root_ok=1
  coder_python=python3
  coder_path=$PATH
  if [ "$(uname -s)" = "Darwin" ] && [ -x /usr/bin/python3 ]; then
    coder_python=/usr/bin/python3
    coder_path="$REPO_DIR/tests/fixtures/darwin-git-bin:/usr/bin:/bin:/usr/sbin:/sbin"
  fi
  (cd coder-stack && PATH="$coder_path" \
    PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/hermes-coder-pycache" \
    "$coder_python" -m unittest discover -s tests -q) && coder_ok=1
  if [ "$root_ok" -eq 1 ] && [ "$coder_ok" -eq 1 ]; then pass "all model-free suites"; else fail "tests"; fi
fi

step "Custom tree/full-history/metadata audit"
if python3 scripts/audit_public.py "$REPO_DIR" --history; then pass "audit_public"; else fail "audit_public"; fi

step "Checksum-pinned Gitleaks"
if ! scripts/install_gitleaks.sh --verify-only; then
  fail "workspace Gitleaks missing/invalid — run scripts/install_gitleaks.sh"
elif scripts/gitleaks_scan.sh; then
  pass "Gitleaks tree + full history"
else
  fail "Gitleaks"
fi

step "Patch applies to exact upstream $PINNED_TAG / $PINNED_COMMIT"
if [ "$OFFLINE" -eq 1 ]; then
  skip "--offline (dependency/network patch proof explicitly skipped)"
else
  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-verify.XXXXXX")" \
    || { fail "mktemp failed"; TMP_DIR=""; }
  if [ -z "$TMP_DIR" ] || [ ! -d "$TMP_DIR" ]; then
    fail "mktemp returned an empty or invalid directory"
  else
    cloned=0
    for attempt in 1 2 3; do
      if bounded_clone; then cloned=1; break; fi
      rm -rf -- "$TMP_DIR/hermes"
      [ "$attempt" -lt 3 ] || break
    done
    if [ "$cloned" -ne 1 ]; then
      fail "bounded exact-tag clone failed; use --offline only when intentionally skipping it"
    elif [ "$(git -C "$TMP_DIR/hermes" rev-parse "refs/tags/$PINNED_TAG^{}" 2>/dev/null)" != "$PINNED_COMMIT" ]; then
    fail "tag does not peel to the pinned commit"
    elif [ "$(git -C "$TMP_DIR/hermes" rev-parse HEAD 2>/dev/null)" != "$PINNED_COMMIT" ]; then
      fail "cloned HEAD is not the pinned commit"
    elif git -C "$TMP_DIR/hermes" apply --check --whitespace=error-all "$PATCH_FILE"; then
      pass "plain git apply --check --whitespace=error-all"
    else
      fail "patch proof"
    fi
  fi
fi

step "Diff hygiene"
if [ -n "${VERIFY_DIFF_BASE:-}" ]; then
  if git diff --check "$VERIFY_DIFF_BASE...HEAD"; then
    pass "actual base range"
  else
    fail "actual base range"
  fi
fi
if git diff --check; then
  pass "working tree"
else
  fail "working tree"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "$FAILURES check(s) failed."
exit 1
