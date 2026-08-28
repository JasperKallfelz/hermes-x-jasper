#!/usr/bin/env bash
# Regenerate the feature patch from exactly the reviewed patch-owned path set.
set -euo pipefail

STARTER_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKOUT="${1:?usage: $0 /absolute/patched/hermes-checkout}"
OUTPUT="${2:-$STARTER_DIR/patches/voice-and-desktop-features.patch}"
PINNED_COMMIT="5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
ALLOWLIST="$STARTER_DIR/patches/voice-and-desktop-features.paths"

case "$CHECKOUT" in /*) ;; *) echo "checkout must be absolute" >&2; exit 2 ;; esac
[ -d "$CHECKOUT/.git" ] && [ ! -L "$CHECKOUT" ] \
  || { echo "checkout must be a non-symlink standalone Git repository" >&2; exit 1; }
[ "$(git -C "$CHECKOUT" rev-parse --verify HEAD)" = "$PINNED_COMMIT" ] \
  || { echo "checkout HEAD is not the patch pin" >&2; exit 1; }
git -C "$CHECKOUT" diff --cached --quiet -- \
  || { echo "checkout index must be empty before patch regeneration" >&2; exit 1; }

paths=()
while IFS= read -r path; do
  [ -n "$path" ] && paths+=("$path")
done < "$ALLOWLIST"
[ "${#paths[@]}" -gt 0 ] || { echo "patch path allowlist is empty" >&2; exit 1; }
cleanup() { git -C "$CHECKOUT" restore --staged -- "${paths[@]}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

git -C "$CHECKOUT" add -- "${paths[@]}"
actual="$(git -C "$CHECKOUT" diff --cached --name-only | LC_ALL=C sort)"
expected="$(printf '%s\n' "${paths[@]}" | LC_ALL=C sort)"
[ "$actual" = "$expected" ] \
  || { echo "cached path set does not exactly match the patch-owned allowlist" >&2; exit 1; }

temporary="$(mktemp "$(dirname "$OUTPUT")/.hermes-feature-patch.XXXXXX")" \
  || { echo "could not create patch temporary file" >&2; exit 1; }
[ -n "$temporary" ] && [ -f "$temporary" ] \
  || { echo "invalid patch temporary file" >&2; exit 1; }
cleanup_all() {
  rm -f -- "$temporary"
  cleanup
}
trap cleanup_all EXIT
git -C "$CHECKOUT" diff --cached --binary --full-index -- > "$temporary"
git -C "$CHECKOUT" apply --reverse --check --whitespace=error-all "$temporary"
chmod 0644 "$temporary"
mv "$temporary" "$OUTPUT"
echo "regenerated patch from exactly ${#paths[@]} reviewed paths: $OUTPUT"
