#!/usr/bin/env bash
# Prove the plain patch against the exact reviewed upstream tag and commit.
set -euo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_REPO="${HERMES_VERIFY_UPSTREAM_REPO:-https://github.com/NousResearch/hermes-agent}"
PINNED_COMMIT="5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
PINNED_TAG="v2026.8.27"
PATCH_FILE="$REPO_DIR/patches/voice-and-desktop-features.patch"
RUN_TESTS=0
[ "${1:-}" = "--run-tests" ] && RUN_TESTS=1
[ "$#" -le 1 ] || { echo "usage: $0 [--run-tests]" >&2; exit 2; }

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-patch-proof.XXXXXX")" \
  || { echo "mktemp failed for patch proof" >&2; exit 1; }
if [ -z "$TMP_DIR" ] || [ ! -d "$TMP_DIR" ]; then
  echo "mktemp returned an empty or invalid patch-proof directory" >&2
  exit 1
fi
cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT

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

cloned=0
for attempt in 1 2 3; do
  if bounded_clone; then
    cloned=1
    break
  fi
  rm -rf -- "$TMP_DIR/hermes"
  [ "$attempt" -lt 3 ] || break
done
[ "$cloned" -eq 1 ] || { echo "bounded exact-tag clone failed after three attempts" >&2; exit 1; }

peeled="$(git -C "$TMP_DIR/hermes" rev-parse --verify "refs/tags/$PINNED_TAG^{}")"
head="$(git -C "$TMP_DIR/hermes" rev-parse --verify HEAD)"
[ "$peeled" = "$PINNED_COMMIT" ] && [ "$head" = "$PINNED_COMMIT" ] \
  || { echo "upstream tag/HEAD did not resolve to the reviewed pin" >&2; exit 1; }
git -C "$TMP_DIR/hermes" apply --check --whitespace=error-all "$PATCH_FILE"
git -C "$TMP_DIR/hermes" apply --whitespace=error-all "$PATCH_FILE"
echo "exact-pin patch proof: plain apply passed"

if [ "$RUN_TESTS" -eq 1 ]; then
  test_python="${HERMES_PATCH_TEST_PYTHON:-}"
  if [ -n "$test_python" ]; then
    [ -x "$test_python" ] \
      || { echo "HERMES_PATCH_TEST_PYTHON is not executable" >&2; exit 1; }
    "$test_python" -c 'import pytest' 2>/dev/null \
      || { echo "HERMES_PATCH_TEST_PYTHON lacks pytest/dependencies" >&2; exit 1; }
  else
    test_python="$TMP_DIR/test-venv/bin/python"
    python3 -m venv "$TMP_DIR/test-venv"
    "$TMP_DIR/test-venv/bin/pip" install --quiet pytest -e "$TMP_DIR/hermes" \
      || { echo "could not install patched-test dependencies in the temporary venv" >&2; exit 1; }
  fi
  output="$(HERMES_HOME="$TMP_DIR/hermes-home" \
    PYTHONPYCACHEPREFIX="$TMP_DIR/pycache" "$test_python" -m pytest -q \
    "$TMP_DIR/hermes/tests/cli/test_cli_browser_connect.py" \
    "$TMP_DIR/hermes/tests/gateway/test_telegram_location_keyboard_cleanup.py" \
    "$TMP_DIR/hermes/tests/hermes_cli/test_browser_connect_loopback_binding.py" \
    "$TMP_DIR/hermes/tests/tools/test_browser_auto_cdp.py" \
    "$TMP_DIR/hermes/tests/tools/test_tts_runtime_overrides.py")" || {
      echo "patched upstream tests failed; verify editable upstream dependencies" >&2
      exit 1
    }
  printf '%s\n' "$output"
  case "$output" in
    *"31 passed"*) echo "exact-pin patched test proof: 31 passed" ;;
    *) echo "patched test count was not exactly 31" >&2; exit 1 ;;
  esac
fi
