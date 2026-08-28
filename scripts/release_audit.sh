#!/usr/bin/env bash
# Authoritative, fail-closed public release audit and artifact proof.
set -uo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1
CANDIDATE="${RELEASE_CANDIDATE:-HEAD}"
FAILURES=0

gate() {
  local label=$1
  shift
  printf '\n==> %s\n' "$label"
  if "$@"; then
    echo "  PASS"
    return 0
  else
    echo "  FAIL" >&2
    FAILURES=$((FAILURES + 1))
    return 1
  fi
}

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-release-audit.XXXXXX")" \
  || { echo "mktemp failed for release audit" >&2; exit 1; }
if [ -z "$TMP_DIR" ] || [ ! -d "$TMP_DIR" ]; then
  echo "mktemp returned an empty or invalid release-audit directory" >&2
  exit 1
fi
TMP_DIR="$(cd "$TMP_DIR" && pwd -P)" \
  || { echo "could not canonicalize release-audit directory" >&2; exit 1; }
cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT

candidate_commit="$(git rev-parse --verify "$CANDIDATE^{commit}" 2>/dev/null || true)"
if [ -z "$candidate_commit" ]; then
  echo "release candidate is not a commit" >&2
  exit 1
fi

gate "Current tree, ignored controls, and tracked inventory" \
  python3 scripts/check_release_inputs.py --repo "$REPO_DIR" --candidate "$candidate_commit"
gate "Custom public audit: tree + full history + metadata" \
  python3 scripts/audit_public.py "$REPO_DIR" --history
gate "Install/verify workspace-local Gitleaks 8.30.1" \
  "$REPO_DIR/scripts/install_gitleaks.sh"
gate "Pinned Gitleaks: tree + full history" \
  "$REPO_DIR/scripts/gitleaks_scan.sh"

if [ -n "${RELEASE_DIFF_BASE:-}" ]; then
  diff_base="$RELEASE_DIFF_BASE"
elif git rev-parse --verify "$candidate_commit^" >/dev/null 2>&1; then
  diff_base="$candidate_commit^"
else
  diff_base="$(git hash-object -t tree /dev/null)"
fi
gate "Actual base/candidate diff hygiene" git diff --check "$diff_base...$candidate_commit"
gate "Index/worktree diff hygiene" git diff --check
gate "Exact-pin plain patch proof" "$REPO_DIR/scripts/prove_patch.sh"
gate "Pi manifest/hash/path dry-run integrity" \
  "$REPO_DIR/modules/pi-runtime/setup.sh" --dry-run "$TMP_DIR/pi-runtime-install"

ARTIFACT_DIR="$TMP_DIR/artifact"
if gate "Build/unpack/hash artifact from candidate Git object" \
    python3 scripts/build_release_artifact.py --repo "$REPO_DIR" \
      --candidate "$candidate_commit" --output "$ARTIFACT_DIR"; then
  unpacked="$ARTIFACT_DIR/unpacked/hermes-x-jasper-v0.3.0"
  gate "Custom scan of final unpacked artifact" \
    python3 scripts/audit_public.py "$unpacked" --all-files
  gate "Custom scan of final release-run manifest" \
    python3 scripts/audit_public.py "$ARTIFACT_DIR/release-run.json" --all-files
  gate "Gitleaks scan of final archive manifest and unpacked artifact" \
    "$REPO_DIR/.tools/gitleaks/8.30.1/gitleaks" dir "$ARTIFACT_DIR" \
      --config "$REPO_DIR/.gitleaks.toml" --redact --no-banner --ignore-gitleaks-allow
  gate "Final release-run hash binding" python3 - "$ARTIFACT_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = json.loads((root / "release-run.json").read_text())
archive = root / manifest["archive"]
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
raise SystemExit(0 if actual == manifest["archive_sha256"] else 1)
PY
fi

if [ "$FAILURES" -ne 0 ]; then
  echo "release audit failed: $FAILURES gate(s) failed" >&2
  exit 1
fi
echo "release audit passed for $candidate_commit"
