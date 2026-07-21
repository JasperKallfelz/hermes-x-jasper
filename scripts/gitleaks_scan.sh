#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deterministic Gitleaks release gate: current tree + full git history.
#
#   scripts/gitleaks_scan.sh
#
# Runs two scans against the pinned .gitleaks.toml and requires BOTH to be clean:
#   1. `gitleaks dir` over a read-only snapshot of tracked + untracked,
#      non-ignored files (what a fork would publish)
#   2. `gitleaks git` over the entire commit history
#
# Git-ignored caches and local state are excluded by the same publication
# boundary as `git status`; nothing in the working tree is removed. The config
# uses only narrow, rule-bound, commit+path-scoped allowlists for known-immutable
# historical false positives — never a global allowlist. See .gitleaks.toml.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

CONFIG="$REPO_DIR/.gitleaks.toml"
PINNED_VERSION="8.30.1"

command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks not on PATH (need v$PINNED_VERSION)"; exit 127; }
[ -f "$CONFIG" ] || { echo "missing config: $CONFIG"; exit 1; }

have_version="$(gitleaks version 2>/dev/null || echo unknown)"
if [ "$have_version" != "$PINNED_VERSION" ]; then
  echo "note: gitleaks $have_version present; release gate is pinned to $PINNED_VERSION"
fi

# Gitleaks has no dir-mode path exclusion flag. Build a temporary publication
# snapshot from Git's tracked + untracked/non-ignored file list instead of
# deleting ignored caches from the operator's checkout.
SCAN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-gitleaks-tree.XXXXXX")"
trap 'rm -rf "$SCAN_DIR"' EXIT
while IFS= read -r -d '' relative; do
  source_path="$REPO_DIR/$relative"
  [ -f "$source_path" ] && [ ! -L "$source_path" ] || continue
  mkdir -p "$SCAN_DIR/$(dirname "$relative")"
  cp "$source_path" "$SCAN_DIR/$relative"
done < <(git ls-files --cached --others --exclude-standard -z)

status=0

echo "==> gitleaks dir (current working tree)"
if gitleaks dir "$SCAN_DIR" --config "$CONFIG" --redact --no-banner; then
  echo "  clean"
else
  echo "  FAIL: current-tree secrets found"
  status=1
fi

echo "==> gitleaks git (full history)"
if gitleaks git . --config "$CONFIG" --redact --no-banner; then
  echo "  clean"
else
  echo "  FAIL: historical secrets found"
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "gitleaks: current tree and full history are clean."
fi
exit "$status"
