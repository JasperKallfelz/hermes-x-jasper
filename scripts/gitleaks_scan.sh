#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deterministic Gitleaks release gate: current tree + full git history.
#
#   scripts/gitleaks_scan.sh
#
# Runs two scans against the pinned .gitleaks.toml and requires BOTH to be clean:
#   1. `gitleaks dir` over the working tree (what a fork would publish)
#   2. `gitleaks git` over the entire commit history
#
# Generated Python caches are removed first so they are never included in the
# scan (they are git-ignored build artifacts, not release content). The config
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

# Never scan generated caches — .gitignore already excludes them from git.
find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find . -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true

status=0

echo "==> gitleaks dir (current working tree)"
if gitleaks dir . --config "$CONFIG" --redact --no-banner; then
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
