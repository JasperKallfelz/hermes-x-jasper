#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Hermes CLI Starter — pinned, idempotent installer
#
#   ./setup.sh --dry-run
#   ./setup.sh [--skip-voice] [--skip-coder-stack]
#   ./setup.sh --install-dir ~/src/hermes-agent --hermes-home ~/.hermes
# ---------------------------------------------------------------------------
set -euo pipefail

UPSTREAM_REPO="https://github.com/NousResearch/hermes-agent"
PINNED_TAG="v2026.8.27"
PINNED_COMMIT="5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
UPSTREAM_VERSION="v0.20.6"

# Tests exercise the complete installer against locally-created real Git
# repositories. Production runs cannot override the release tuple or origin.
if [ "${HERMES_SETUP_TESTING:-0}" = "1" ]; then
  UPSTREAM_REPO="${HERMES_SETUP_TEST_REPO:-$UPSTREAM_REPO}"
  PINNED_TAG="${HERMES_SETUP_TEST_TAG:-$PINNED_TAG}"
  PINNED_COMMIT="${HERMES_SETUP_TEST_PIN:-$PINNED_COMMIT}"
  UPSTREAM_VERSION="${HERMES_SETUP_TEST_VERSION:-$UPSTREAM_VERSION}"
fi

STARTER_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$STARTER_DIR/patches/voice-and-desktop-features.patch"
CHECKOUT_PROOF="$STARTER_DIR/scripts/verify_upstream_checkout.py"
INSTALL_STATE="$STARTER_DIR/scripts/install_state.py"
OVERLAY="$STARTER_DIR/config.example.yaml"

INSTALL_DIR_INPUT="${HERMES_INSTALL_DIR:-$HOME/hermes-agent}"
HERMES_HOME_INPUT="${HERMES_HOME:-$HOME/.hermes}"
CODER_BIN_DIR_INPUT="${HERMES_CODER_BIN_DIR:-$HOME/.local/bin}"
DRY_RUN=0
SKIP_VOICE=0
INSTALL_CODER_STACK=1
REPLACE_CODER_STACK=0

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYAN" "$NC" "$*"; }
ok() { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%s  !!%s %s\n' "$YELLOW" "$NC" "$*"; }
die() { printf '%s error:%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  would run:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --skip-voice) SKIP_VOICE=1; shift ;;
    --skip-coder-stack) INSTALL_CODER_STACK=0; shift ;;
    --replace-coder-stack) REPLACE_CODER_STACK=1; shift ;;
    --install-dir) INSTALL_DIR_INPUT="${2:?--install-dir needs a path}"; shift 2 ;;
    --hermes-home) HERMES_HOME_INPUT="${2:?--hermes-home needs a path}"; shift 2 ;;
    --coder-bin-dir) CODER_BIN_DIR_INPUT="${2:?--coder-bin-dir needs a path}"; shift 2 ;;
    -h|--help) usage ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

for required in git python3 mktemp; do
  command -v "$required" >/dev/null 2>&1 || die "$required is required but not on PATH"
done
if [ "$INSTALL_CODER_STACK" -eq 1 ]; then
  for required in cmp install; do
    command -v "$required" >/dev/null 2>&1 || die "$required is required but not on PATH"
  done
fi
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
  || die "Python 3.11+ is required (found $(python3 -V 2>&1))"

for required_file in "$PATCH_FILE" "$CHECKOUT_PROOF" "$INSTALL_STATE" "$OVERLAY"; do
  [ -f "$required_file" ] && [ ! -L "$required_file" ] \
    || die "required starter input is missing or unsafe: $(basename "$required_file")"
done

# Print an absolute physical path only after proving that no user-supplied
# component is a symlink. Missing suffixes are allowed below the nearest safe,
# writable existing directory so normal first installs can create their leaf.
canonical_target() {
  local input=$1 label=$2 result
  case "$input" in
    "") die "$label path is empty" ;;
    /) die "$label cannot be the filesystem root" ;;
    */) die "$label has a trailing-slash alias; pass the directory without it" ;;
  esac
  result="$(python3 - "$input" "$label" <<'PY'
import os
import stat
import sys
from pathlib import Path

raw, label = sys.argv[1:]
if raw == "~":
    raw = os.environ["HOME"]
elif raw.startswith("~/"):
    raw = os.path.join(os.environ["HOME"], raw[2:])
elif raw.startswith("~"):
    print(f"{label} uses an unsupported home alias", file=sys.stderr)
    raise SystemExit(1)

absolute = Path(os.path.abspath(raw))
parts = absolute.parts
cursor = Path(parts[0])
existing = cursor
missing = []
for part in parts[1:]:
    cursor /= part
    try:
        metadata = cursor.lstat()
    except FileNotFoundError:
        missing.append(part)
        continue
    if missing:
        print(f"{label} has an existing descendant below a missing parent", file=sys.stderr)
        raise SystemExit(1)
    if stat.S_ISLNK(metadata.st_mode):
        print(f"{label} has a symlinked ancestor or leaf", file=sys.stderr)
        raise SystemExit(1)
    if not stat.S_ISDIR(metadata.st_mode):
        print(f"{label} has a non-directory ancestor or leaf", file=sys.stderr)
        raise SystemExit(1)
    existing = cursor

metadata = existing.stat()
if not (metadata.st_mode & 0o222) or not os.access(existing, os.W_OK | os.X_OK):
    print(f"{label} has no writable safe parent", file=sys.stderr)
    raise SystemExit(1)
physical = existing.resolve(strict=True)
for part in missing:
    physical /= part
print(physical)
PY
)" || die "could not validate $label"
  [ -n "$result" ] || die "could not canonicalize $label"
  printf '%s\n' "$result"
}

INSTALL_DIR="$(canonical_target "$INSTALL_DIR_INPUT" "install directory")"
HERMES_HOME="$(canonical_target "$HERMES_HOME_INPUT" "HERMES_HOME")"
CODER_BIN_DIR="$(canonical_target "$CODER_BIN_DIR_INPUT" "coder bin directory")"

overlap() {
  [ "$1" = "$2" ] || [[ "$1" == "$2"/* ]] || [[ "$2" == "$1"/* ]]
}
for pair in \
  "$INSTALL_DIR|$HERMES_HOME|install directory and HERMES_HOME" \
  "$INSTALL_DIR|$CODER_BIN_DIR|install directory and coder bin directory" \
  "$HERMES_HOME|$CODER_BIN_DIR|HERMES_HOME and coder bin directory" \
  "$STARTER_DIR|$INSTALL_DIR|starter and install directory" \
  "$STARTER_DIR|$HERMES_HOME|starter and HERMES_HOME" \
  "$STARTER_DIR|$CODER_BIN_DIR|starter and coder bin directory"; do
  first=${pair%%|*}; rest=${pair#*|}; second=${rest%%|*}; label=${rest#*|}
  overlap "$first" "$second" && die "$label overlap unsafely"
done

HERMES_BIN="$INSTALL_DIR/venv/bin/hermes"
PIP="$INSTALL_DIR/venv/bin/pip"
ENV_MARKER="$INSTALL_DIR/venv/.hermes-starter-complete.json"
ENV_TARGET="$HERMES_HOME/.env"
CONFIG_TARGET="$HERMES_HOME/config.yaml"

for target in "$ENV_TARGET" "$CONFIG_TARGET"; do
  if [ -e "$target" ] || [ -L "$target" ]; then
    [ -f "$target" ] && [ ! -L "$target" ] \
      || die "$target exists but is not a regular file; move it aside manually"
  fi
done
CONFIG_PREEXISTED=0
[ -f "$CONFIG_TARGET" ] && CONFIG_PREEXISTED=1

# Validate wrapper conflicts now, but install them only after the pinned source
# and patch have been proved.
if [ "$INSTALL_CODER_STACK" -eq 1 ]; then
  [ ! -e "$CODER_BIN_DIR" ] || [ -d "$CODER_BIN_DIR" ] \
    || die "$CODER_BIN_DIR exists but is not a directory"
  [ ! -d "$CODER_BIN_DIR" ] || [ -w "$CODER_BIN_DIR" ] \
    || die "$CODER_BIN_DIR is not writable"
  for name in hermes-coder hermes-coder-flow; do
    source_file="$STARTER_DIR/coder-stack/bin/$name"
    target_file="$CODER_BIN_DIR/$name"
    [ -f "$source_file" ] && [ ! -L "$source_file" ] \
      || die "vendored coder wrapper is missing or unsafe: $name"
    if [ -e "$target_file" ] || [ -L "$target_file" ]; then
      [ -f "$target_file" ] && [ ! -L "$target_file" ] \
        || die "$target_file exists but is not a regular file; move it aside manually"
      if ! cmp -s "$source_file" "$target_file" && [ "$REPLACE_CODER_STACK" -ne 1 ]; then
        die "$target_file differs from the vendored wrapper; use --replace-coder-stack or another --coder-bin-dir"
      fi
    fi
  done
fi

echo
info "Hermes CLI Starter"
echo "  upstream    : NousResearch/hermes-agent"
echo "  tag/commit  : $PINNED_TAG / $PINNED_COMMIT"
echo "  install dir : $INSTALL_DIR"
echo "  hermes home : $HERMES_HOME"
echo "  executable  : $HERMES_BIN"
[ "$INSTALL_CODER_STACK" -eq 1 ] && echo "  coder bin   : $CODER_BIN_DIR"
[ "$DRY_RUN" -eq 1 ] && warn "dry run — nothing will be written"
echo

ALLOW_TEST_ORIGIN=()
[ "${HERMES_SETUP_TESTING:-0}" = "1" ] && ALLOW_TEST_ORIGIN=(--allow-test-origin)
CHECKOUT_STATE="absent"
classify_checkout() {
  local result
  if ! result="$(python3 "$CHECKOUT_PROOF" \
      --checkout "$INSTALL_DIR" --patch "$PATCH_FILE" --pin "$PINNED_COMMIT" \
      "${ALLOW_TEST_ORIGIN[@]}")"; then
    die "$INSTALL_DIR failed exact checkout verification"
  fi
  case "$result" in clean|patched) CHECKOUT_STATE=$result ;; *) die "checkout verifier returned an invalid state" ;; esac
}

bounded_command() {
  local seconds=$1
  shift
  python3 - "$seconds" "$@" <<'PY'
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

clone_exact_tag() {
  local parent temporary attempt repo
  parent=$(dirname "$INSTALL_DIR")
  temporary="$(mktemp -d "$parent/.hermes-clone.XXXXXX")" \
    || die "could not create a temporary clone directory"
  [ -n "$temporary" ] && [ -d "$temporary" ] \
    || die "temporary clone directory is empty or invalid"
  repo="$temporary/repo"
  for attempt in 1 2 3; do
    if bounded_command 120 git clone --quiet --branch "$PINNED_TAG" --single-branch \
        "$UPSTREAM_REPO" "$repo"; then
      break
    fi
    rm -rf -- "$repo"
    [ "$attempt" -lt 3 ] || { rm -rf -- "$temporary"; die "could not fetch the pinned upstream tag after bounded retries"; }
  done
  peeled="$(git -C "$repo" rev-parse --verify "refs/tags/$PINNED_TAG^{}" 2>/dev/null || true)"
  head="$(git -C "$repo" rev-parse --verify HEAD 2>/dev/null || true)"
  if [ "$peeled" != "$PINNED_COMMIT" ] || [ "$head" != "$PINNED_COMMIT" ]; then
    rm -rf -- "$temporary"
    die "the fetched tag or HEAD does not match the reviewed pinned commit"
  fi
  if ! python3 - "$repo" "$INSTALL_DIR" <<'PY'
import os
import sys
os.rename(sys.argv[1], sys.argv[2])
PY
  then
    rm -rf -- "$temporary"
    die "could not atomically install the verified clone"
  fi
  rmdir "$temporary"
}

# The first mutation can occur only after all local path/config/wrapper inputs
# have passed validation. Existing exact states never fetch, so verified reruns
# work offline.
info "Proving pinned upstream checkout"
if [ -e "$INSTALL_DIR" ]; then
  classify_checkout
  ok "exact local checkout state: $CHECKOUT_STATE (offline-safe)"
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "  would fetch exact tag $PINNED_TAG with bounded retries and verify its peeled commit"
else
  clone_exact_tag
  classify_checkout
  [ "$CHECKOUT_STATE" = "clean" ] || die "fresh clone did not produce the clean pinned tree"
  ok "fetched and verified exact tag $PINNED_TAG"
fi

info "Applying feature patch"
case "$CHECKOUT_STATE" in
  patched) ok "patch already applied" ;;
  clean)
    if [ "$DRY_RUN" -eq 1 ]; then
      git -C "$INSTALL_DIR" apply --check --whitespace=error-all "$PATCH_FILE" \
        || die "patch does not apply plainly to the clean pinned checkout"
      echo "  would apply: $(basename "$PATCH_FILE")"
    else
      git -C "$INSTALL_DIR" apply --check --whitespace=error-all "$PATCH_FILE" \
        || die "patch does not apply plainly to the clean pinned checkout"
      git -C "$INSTALL_DIR" apply --whitespace=error-all "$PATCH_FILE"
      classify_checkout
      [ "$CHECKOUT_STATE" = "patched" ] || die "post-apply checkout proof failed"
      ok "patch applied and exact patched content verified"
    fi
    ;;
  absent) echo "  would apply after the verified clone" ;;
esac

# Unrelated wrapper installation deliberately follows source/pin/patch proof.
if [ "$INSTALL_CODER_STACK" -eq 0 ]; then
  info "Skipping coder stack (--skip-coder-stack)"
else
  info "Installing subscription-backed coding wrappers"
  run mkdir -p "$CODER_BIN_DIR"
  for name in hermes-coder hermes-coder-flow; do
    source_file="$STARTER_DIR/coder-stack/bin/$name"
    target_file="$CODER_BIN_DIR/$name"
    if [ -f "$target_file" ] && cmp -s "$source_file" "$target_file"; then
      if [ -x "$target_file" ]; then
        ok "$name already installed and current"
      else
        run chmod 755 "$target_file"
        ok "$name content is current; executable mode restored"
      fi
      continue
    fi
    if [ -e "$target_file" ]; then
      stamp=$(date -u +%Y%m%dT%H%M%S)
      number=0
      backup_file="$target_file.bak-$stamp"
      while [ -e "$backup_file" ]; do
        number=$((number + 1))
        backup_file="$target_file.bak-$stamp-$number"
      done
      run cp -p "$target_file" "$backup_file"
      warn "backed up differing $name to $backup_file"
    fi
    run install -m 755 "$source_file" "$target_file"
    ok "installed $target_file"
  done
fi

VOICE_SET=""
if [ "$SKIP_VOICE" -eq 0 ]; then
  VOICE_SET="edge-tts,faster-whisper,langid"
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    VOICE_SET="$VOICE_SET,parakeet-mlx"
  fi
fi
STATE_ARGS=(
  --marker "$ENV_MARKER" --binary "$HERMES_BIN" --install-dir "$INSTALL_DIR"
  --hermes-home "$HERMES_HOME"
  --patch "$PATCH_FILE" --pin "$PINNED_COMMIT" --tag "$PINNED_TAG"
  --version "$UPSTREAM_VERSION" --voice-set "$VOICE_SET"
)

info "Checking content-keyed environment state"
ENV_CURRENT=0
if [ "$CHECKOUT_STATE" = "patched" ] && \
    env HERMES_HOME="$HERMES_HOME" python3 "$INSTALL_STATE" check "${STATE_ARGS[@]}"; then
  ENV_CURRENT=1
  ok "verified environment marker, executable, version, and dependency integrity"
fi

if [ "$ENV_CURRENT" -eq 0 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  would repair the environment, verify it, then write a private completion marker"
  else
    info "Repairing/synchronizing upstream environment"
    if [ -x "$INSTALL_DIR/setup-hermes.sh" ]; then
      env HERMES_HOME="$HERMES_HOME" bash "$INSTALL_DIR/setup-hermes.sh"
    else
      warn "setup-hermes.sh not found — using a plain venv + editable install"
      python3 -m venv "$INSTALL_DIR/venv"
      "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
      "$INSTALL_DIR/venv/bin/pip" install --quiet -e "$INSTALL_DIR"
    fi
    [ -x "$HERMES_BIN" ] || die "upstream installation did not create an executable Hermes CLI"
    if [ "$SKIP_VOICE" -eq 1 ]; then
      info "Skipping voice dependencies (--skip-voice)"
    else
      [ -x "$PIP" ] || die "voice dependencies requested but the environment pip is missing"
      "$PIP" install --quiet edge-tts faster-whisper langid \
        || die "voice dependency installation failed; completion marker was not written"
      if [[ ",$VOICE_SET," == *,parakeet-mlx,* ]]; then
        "$PIP" install --quiet parakeet-mlx \
          || die "parakeet-mlx installation failed; completion marker was not written"
      fi
    fi
    env HERMES_HOME="$HERMES_HOME" python3 "$INSTALL_STATE" verify-and-write "${STATE_ARGS[@]}" \
      || die "installed environment failed verification; completion marker was not written"
  fi
else
  [ "$SKIP_VOICE" -eq 1 ] && info "Skipping voice dependencies (--skip-voice)"
fi

info "Seeding local configuration"
run mkdir -p "$HERMES_HOME"
if [ -f "$ENV_TARGET" ]; then
  ok ".env exists — left untouched"
else
  run cp "$STARTER_DIR/.env.example" "$ENV_TARGET"
  run chmod 600 "$ENV_TARGET"
  ok "created $ENV_TARGET with empty values"
fi

if [ "$CONFIG_PREEXISTED" -eq 1 ]; then
  warn "$CONFIG_TARGET pre-existed setup — left untouched"
  echo "  Review missing starter keys with:"
  echo "    python3 '$STARTER_DIR/scripts/merge_config.py' --base '$CONFIG_TARGET' --overlay '$OVERLAY'"
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "  would preserve wizard-selected values and merge all missing starter keys"
else
  MERGE_PYTHON="$INSTALL_DIR/venv/bin/python"
  [ -x "$MERGE_PYTHON" ] || MERGE_PYTHON=python3
  "$MERGE_PYTHON" "$STARTER_DIR/scripts/merge_config.py" \
    --base "$CONFIG_TARGET" --overlay "$OVERLAY" --apply
  chmod 600 "$CONFIG_TARGET"
  ok "wizard values preserved; missing starter keys installed"
fi

info "Checking optional subscription CLI requirements"
if command -v claude >/dev/null 2>&1; then
  ok "Claude Code CLI found"
else
  warn "Claude Code CLI not found — install it separately"
fi
if command -v codex >/dev/null 2>&1; then
  ok "Codex CLI found"
else
  warn "Codex CLI not found — install it separately"
fi
echo "  setup never invokes or authenticates either subscription CLI"

echo
info "Done"
cat <<EOF
Next steps:
  1. Put API keys in $ENV_TARGET (never commit this file).
  2. Set messaging allowlists before exposing the agent.
  3. Start it: HERMES_HOME='$HERMES_HOME' $HERMES_BIN

Docs: $STARTER_DIR/README.md · $STARTER_DIR/docs/TROUBLESHOOTING.md
EOF
