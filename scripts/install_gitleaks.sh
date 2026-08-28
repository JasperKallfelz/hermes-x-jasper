#!/usr/bin/env bash
# Install and verify the release-pinned Gitleaks in this workspace only.
set -euo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="8.30.1"
CHECKSUMS="$REPO_DIR/security/gitleaks-8.30.1.sha256"
TOOL_DIR="$REPO_DIR/.tools/gitleaks/$VERSION"
VERIFY_ONLY=0
[ "${1:-}" = "--verify-only" ] && VERIFY_ONLY=1
[ "$#" -le 1 ] || { echo "usage: $0 [--verify-only]" >&2; exit 2; }

case "$(uname -s)" in
  Darwin) os_name=darwin ;;
  Linux) os_name=linux ;;
  *) echo "unsupported Gitleaks platform" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "unsupported Gitleaks architecture" >&2; exit 1 ;;
esac

asset="gitleaks_${VERSION}_${os_name}_${arch}.tar.gz"
archive="$TOOL_DIR/$asset"
binary="$TOOL_DIR/gitleaks"
expected="$(awk -v name="$asset" '$2 == name {print $1}' "$CHECKSUMS")"
[ "${#expected}" -eq 64 ] || { echo "missing checked-in checksum for $asset" >&2; exit 1; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

verify_install() {
  local actual temporary extracted_version
  [ -f "$archive" ] && [ ! -L "$archive" ] || return 1
  [ -x "$binary" ] && [ -f "$binary" ] && [ ! -L "$binary" ] || return 1
  actual="$(sha256_file "$archive")"
  [ "$actual" = "$expected" ] || return 1
  temporary="$(mktemp -d "${TMPDIR:-/tmp}/hermes-gitleaks-verify.XXXXXX")" || return 1
  if [ -z "$temporary" ] || [ ! -d "$temporary" ]; then
    return 1
  fi
  if ! tar -xzf "$archive" -C "$temporary" gitleaks >/dev/null 2>&1 \
      || ! cmp -s "$temporary/gitleaks" "$binary"; then
    rm -rf -- "$temporary"
    return 1
  fi
  rm -rf -- "$temporary"
  extracted_version="$($binary version 2>/dev/null)" || return 1
  [ "$extracted_version" = "$VERSION" ]
}

if verify_install; then
  printf 'gitleaks %s verified: %s\n' "$VERSION" "$binary"
  exit 0
fi
if [ "$VERIFY_ONLY" -eq 1 ]; then
  echo "workspace Gitleaks is missing or failed checksum/binary/version verification" >&2
  exit 1
fi

mkdir -p "$TOOL_DIR"
temporary="$(mktemp -d "$TOOL_DIR/.install.XXXXXX")" \
  || { echo "could not create Gitleaks install temporary directory" >&2; exit 1; }
if [ -z "$temporary" ] || [ ! -d "$temporary" ]; then
  echo "invalid Gitleaks install temporary directory" >&2
  exit 1
fi
cleanup() { rm -rf -- "$temporary"; }
trap cleanup EXIT

download="$temporary/$asset"
base_url="https://github.com/gitleaks/gitleaks/releases/download/v$VERSION"
if [ "${HERMES_GITLEAKS_TESTING:-0}" = "1" ]; then
  base_url="${HERMES_GITLEAKS_TEST_BASE_URL:-$base_url}"
fi
curl --proto '=https,file' --tlsv1.2 --fail --location --silent --show-error \
  --retry 3 --retry-all-errors --connect-timeout 15 --max-time 180 \
  "$base_url/$asset" --output "$download"
actual="$(sha256_file "$download")"
[ "$actual" = "$expected" ] || { echo "downloaded Gitleaks archive checksum mismatch" >&2; exit 1; }

tar -xzf "$download" -C "$temporary" gitleaks
[ -f "$temporary/gitleaks" ] && [ ! -L "$temporary/gitleaks" ] \
  || { echo "verified archive did not contain a safe Gitleaks binary" >&2; exit 1; }
install -m 0644 "$download" "$archive"
install -m 0755 "$temporary/gitleaks" "$binary"
verify_install || { echo "installed Gitleaks failed integrity verification" >&2; exit 1; }
printf 'installed and verified gitleaks %s: %s\n' "$VERSION" "$binary"
