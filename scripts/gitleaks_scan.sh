#!/usr/bin/env bash
# Deterministic pinned Gitleaks gate: publication tree plus complete history.
set -euo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_DIR/.gitleaks.toml"
GITLEAKS="$REPO_DIR/.tools/gitleaks/8.30.1/gitleaks"
[ -f "$CONFIG" ] || { echo "missing Gitleaks config" >&2; exit 1; }
"$REPO_DIR/scripts/install_gitleaks.sh" --verify-only >/dev/null

if { git -C "$REPO_DIR" ls-files --cached --others --exclude-standard -z; \
     git -C "$REPO_DIR" ls-files --others --ignored --exclude-standard -z; } \
    | while IFS= read -r -d '' path; do
        case "$path" in .gitleaksignore|*/.gitleaksignore) exit 9 ;; esac
      done; then
  :
else
  status=$?
  [ "$status" -eq 9 ] || exit "$status"
  echo "unreviewed .gitleaksignore is forbidden" >&2
  exit 1
fi

SCAN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-gitleaks-tree.XXXXXX")" \
  || { echo "could not create Gitleaks scan directory" >&2; exit 1; }
if [ -z "$SCAN_DIR" ] || [ ! -d "$SCAN_DIR" ]; then
  echo "invalid Gitleaks scan directory" >&2
  exit 1
fi
# Invoked indirectly by the EXIT trap below.
# shellcheck disable=SC2329
cleanup() { rm -rf -- "$SCAN_DIR"; }
trap cleanup EXIT

while IFS= read -r -d '' relative; do
  source_path="$REPO_DIR/$relative"
  [ -f "$source_path" ] && [ ! -L "$source_path" ] || continue
  mkdir -p "$SCAN_DIR/$(dirname "$relative")"
  cp "$source_path" "$SCAN_DIR/$relative"
done < <(git -C "$REPO_DIR" ls-files --cached --others --exclude-standard -z)

status=0
echo "==> gitleaks dir (current publication tree)"
if "$GITLEAKS" dir "$SCAN_DIR" --config "$CONFIG" --redact --no-banner \
    --ignore-gitleaks-allow; then
  echo "  clean"
else
  echo "  FAIL: current-tree secrets found"
  status=1
fi

echo "==> gitleaks git (full history)"
if "$GITLEAKS" git "$REPO_DIR" --config "$CONFIG" --redact --no-banner \
    --ignore-gitleaks-allow; then
  echo "  clean"
else
  echo "  FAIL: historical secrets found"
  status=1
fi

[ "$status" -ne 0 ] || echo "gitleaks: publication tree and full history are clean."
exit "$status"
