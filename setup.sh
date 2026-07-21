#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Hermes CLI Starter — installer
#
# Optionally installs the vendored coding wrappers, clones upstream Hermes
# Agent at a pinned commit, applies this starter's patch, runs the upstream
# installer, and copies EXAMPLE files into place. It never writes secrets and
# never overwrites an existing config without asking.
#
# Safe to re-run: every step checks its own end state first.
#
#   ./setup.sh --dry-run          # print the plan, change nothing
#   ./setup.sh                    # install
#   ./setup.sh --skip-voice       # skip the optional voice/TTS dependencies
#   ./setup.sh --skip-coder-stack # do not install the Claude/Codex wrappers
#   ./setup.sh --coder-bin-dir ~/.local/bin
#   ./setup.sh --replace-coder-stack  # back up and replace wrapper conflicts
#   ./setup.sh --install-dir ~/src/hermes-agent --hermes-home ~/.hermes
# ---------------------------------------------------------------------------
set -euo pipefail

UPSTREAM_REPO="https://github.com/NousResearch/hermes-agent"
# Hermes Agent v0.19.0 (release tag v2026.7.20) — the commit this starter's
# patch is written against. Bump this in setup.sh, verify.sh, ci.yml, README.md
# and tests/test_setup.py together (see CONTRIBUTING.md).
PINNED_COMMIT="3ef6bbd201263d354fd83ec55b3c306ded2eb72a"

STARTER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$STARTER_DIR/patches/voice-and-desktop-features.patch"

INSTALL_DIR="${HERMES_INSTALL_DIR:-$HOME/hermes-agent}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CODER_BIN_DIR="${HERMES_CODER_BIN_DIR:-$HOME/.local/bin}"
DRY_RUN=0
SKIP_VOICE=0
INSTALL_CODER_STACK=1
REPLACE_CODER_STACK=0

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$CYAN" "$NC" "$*"; }
ok()    { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
warn()  { printf '%s  !!%s %s\n' "$YELLOW" "$NC" "$*"; }
die()   { printf '%s error:%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

# Every mutating action goes through run(), so --dry-run is honest by construction.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  would run: %s\n' "$*"
  else
    "$@"
  fi
}

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)             DRY_RUN=1; shift ;;
    --skip-voice)          SKIP_VOICE=1; shift ;;
    --skip-coder-stack)    INSTALL_CODER_STACK=0; shift ;;
    --replace-coder-stack) REPLACE_CODER_STACK=1; shift ;;
    --install-dir)         INSTALL_DIR="${2:?--install-dir needs a path}"; shift 2 ;;
    --hermes-home)         HERMES_HOME="${2:?--hermes-home needs a path}"; shift 2 ;;
    --coder-bin-dir)       CODER_BIN_DIR="${2:?--coder-bin-dir needs a path}"; shift 2 ;;
    -h|--help)             usage ;;
    *)                     die "unknown option: $1 (try --help)" ;;
  esac
done

INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"
HERMES_HOME="${HERMES_HOME/#\~/$HOME}"
CODER_BIN_DIR="${CODER_BIN_DIR/#\~/$HOME}"
HERMES_BIN="$INSTALL_DIR/venv/bin/hermes"

echo
info "Hermes CLI Starter"
echo "  upstream    : $UPSTREAM_REPO"
echo "  commit      : $PINNED_COMMIT"
echo "  install dir : $INSTALL_DIR"
echo "  hermes home : $HERMES_HOME"
echo "  executable  : $HERMES_BIN"
if [ "$INSTALL_CODER_STACK" -eq 1 ]; then
  echo "  coder bin   : $CODER_BIN_DIR"
else
  echo "  coder stack : skipped"
fi
[ "$DRY_RUN" -eq 1 ] && warn "dry run — nothing will be written"
echo

# --- 1. prerequisites ------------------------------------------------------
info "Checking prerequisites"
REQUIRED_COMMANDS=(git python3)
if [ "$INSTALL_CODER_STACK" -eq 1 ]; then
  REQUIRED_COMMANDS+=(cmp install)
fi
for cmd in "${REQUIRED_COMMANDS[@]}"; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required but not on PATH"
done
ok "${REQUIRED_COMMANDS[*]}"

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.11+ is required (found $(python3 -V 2>&1))"
ok "$(python3 -V 2>&1)"

command -v ffmpeg >/dev/null 2>&1 && ok "ffmpeg" \
  || warn "ffmpeg not found — voice replies need it (brew install ffmpeg / apt install ffmpeg)"

[ -f "$PATCH_FILE" ] || die "patch not found: $PATCH_FILE"

# Validate every wrapper target before setup makes any changes. A pre-existing
# different file is user-owned until the operator explicitly opts into a
# timestamped backup and replacement.
if [ "$INSTALL_CODER_STACK" -eq 1 ]; then
  [ -d "$CODER_BIN_DIR" ] || [ ! -e "$CODER_BIN_DIR" ] \
    || die "$CODER_BIN_DIR exists but is not a directory"
  [ ! -d "$CODER_BIN_DIR" ] || [ -w "$CODER_BIN_DIR" ] \
    || die "$CODER_BIN_DIR is not writable"
  for name in hermes-coder hermes-coder-flow; do
    source_file="$STARTER_DIR/coder-stack/bin/$name"
    target_file="$CODER_BIN_DIR/$name"
    [ -f "$source_file" ] && [ ! -L "$source_file" ] \
      || die "vendored coder wrapper is missing or unsafe: $source_file"
    if [ -e "$target_file" ] || [ -L "$target_file" ]; then
      [ -f "$target_file" ] && [ ! -L "$target_file" ] \
        || die "$target_file exists but is not a regular file; move it aside manually"
      if ! cmp -s "$source_file" "$target_file" && [ "$REPLACE_CODER_STACK" -ne 1 ]; then
        die "$target_file differs from the vendored wrapper. Re-run with --replace-coder-stack to back it up first, or use --coder-bin-dir."
      fi
    fi
  done
fi

# --- 2. subscription-backed coding wrappers -------------------------------
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
      backup_file="$target_file.bak-$(date +%Y%m%d%H%M%S)"
      backup_number=0
      while [ -e "$backup_file" ]; do
        backup_number=$((backup_number + 1))
        backup_file="$target_file.bak-$(date +%Y%m%d%H%M%S)-$backup_number"
      done
      run cp -p "$target_file" "$backup_file"
      warn "backed up differing $name to $backup_file"
    fi
    run install -m 755 "$source_file" "$target_file"
    ok "installed $target_file"
  done
fi

info "Checking optional subscription CLI requirements"
if command -v claude >/dev/null 2>&1; then
  ok "Claude Code CLI found"
else
  warn "Claude Code CLI not found — install it separately for Claude subscription runs"
fi
if command -v codex >/dev/null 2>&1; then
  ok "Codex CLI found"
else
  warn "Codex CLI not found — install it separately for Codex subscription runs"
fi
echo "  setup does not install or authenticate either CLI"
echo "  check auth yourself: claude auth status"
echo "                       codex login status"

# --- 3. clone or update upstream at the pinned commit ----------------------
info "Fetching upstream at pinned commit"
if [ -d "$INSTALL_DIR/.git" ]; then
  ok "repo already present: $INSTALL_DIR"
  run git -C "$INSTALL_DIR" fetch --quiet origin
elif [ -e "$INSTALL_DIR" ]; then
  die "$INSTALL_DIR exists but is not a git repository — move it aside or pass --install-dir"
else
  run git clone --quiet "$UPSTREAM_REPO" "$INSTALL_DIR"
fi

if [ "$DRY_RUN" -eq 0 ]; then
  CURRENT="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  if [ "$CURRENT" = "$PINNED_COMMIT" ]; then
    ok "already at pinned commit"
  else
    if ! git -C "$INSTALL_DIR" diff --quiet || ! git -C "$INSTALL_DIR" diff --cached --quiet; then
      die "$INSTALL_DIR has uncommitted changes. Commit/stash them, or re-run with a fresh --install-dir."
    fi
    git -C "$INSTALL_DIR" checkout --quiet --detach "$PINNED_COMMIT"
    ok "checked out $PINNED_COMMIT"
  fi
else
  echo "  would checkout: $PINNED_COMMIT"
fi

# --- 4. apply the feature patch (idempotent) ------------------------------
info "Applying feature patch"
if [ "$DRY_RUN" -eq 1 ]; then
  if git -C "$INSTALL_DIR" apply --check "$PATCH_FILE" 2>/dev/null; then
    echo "  would apply: $(basename "$PATCH_FILE")"
  else
    echo "  patch does not apply cleanly here (already applied, or wrong commit)"
  fi
elif git -C "$INSTALL_DIR" apply --reverse --check "$PATCH_FILE" 2>/dev/null; then
  # It reverse-applies, so it is already in the tree. Re-running is a no-op.
  ok "patch already applied"
elif git -C "$INSTALL_DIR" apply --check "$PATCH_FILE" 2>/dev/null; then
  git -C "$INSTALL_DIR" apply "$PATCH_FILE"
  ok "patch applied"
elif git -C "$INSTALL_DIR" apply --3way "$PATCH_FILE" 2>/dev/null; then
  # Plain apply did not fit, but a 3-way merge against the blobs recorded in
  # the patch resolved the drift cleanly.
  ok "patch applied (3-way merge)"
else
  die "patch does not apply to $INSTALL_DIR, even with a 3-way merge. Is the checkout at $PINNED_COMMIT and clean?
     Inspect with: git -C '$INSTALL_DIR' apply --3way -v '$PATCH_FILE'
     If conflict markers were left behind: git -C '$INSTALL_DIR' checkout -- ."
fi

# --- 5. upstream install ---------------------------------------------------
# setup-hermes.sh is upstream's own installer: it creates venv/bin/hermes
# plus the venv/dependencies and seeds its own template files. We deliberately
# do not reimplement any of it, but a present executable means this step's end
# state already exists and a re-run can leave it alone.
info "Checking upstream installer state"
if [ -x "$HERMES_BIN" ]; then
  ok "Hermes executable already exists: $HERMES_BIN — skipping upstream installer"
elif [ -x "$INSTALL_DIR/setup-hermes.sh" ]; then
  info "Running upstream installer (setup-hermes.sh)"
  run bash "$INSTALL_DIR/setup-hermes.sh"
  ok "upstream installer finished"
else
  warn "setup-hermes.sh not found — falling back to a plain venv + editable install"
  run python3 -m venv "$INSTALL_DIR/venv"
  run "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
  run "$INSTALL_DIR/venv/bin/pip" install --quiet -e "$INSTALL_DIR"
fi

# --- 6. optional voice dependencies ---------------------------------------
if [ "$SKIP_VOICE" -eq 1 ]; then
  info "Skipping voice dependencies (--skip-voice)"
else
  info "Installing optional voice dependencies"
  PIP="$INSTALL_DIR/venv/bin/pip"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  would install: edge-tts, faster-whisper, langid (+ parakeet-mlx on Apple Silicon)"
  elif [ -x "$PIP" ]; then
    # Best effort: a missing wheel here must not fail the whole install.
    "$PIP" install --quiet edge-tts faster-whisper langid \
      && ok "edge-tts, faster-whisper, langid" \
      || warn "voice deps failed to install — TTS/STT scripts may not run"
    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
      "$PIP" install --quiet parakeet-mlx \
        && ok "parakeet-mlx (Apple Silicon local STT for scripts/parakeet_stt_limited.py)" \
        || warn "parakeet-mlx not installed — the local Parakeet STT helper will not run"
    fi
  else
    warn "no venv at $PIP — skipping voice deps"
  fi
fi

# --- 7. example files (never clobber real config) -------------------------
info "Seeding config from examples"
run mkdir -p "$HERMES_HOME"

ENV_TARGET="$HERMES_HOME/.env"
if [ -f "$ENV_TARGET" ]; then
  ok ".env exists — left untouched"
else
  run cp "$STARTER_DIR/.env.example" "$ENV_TARGET"
  run chmod 600 "$ENV_TARGET"
  ok "created $ENV_TARGET (all values empty — fill in your keys)"
fi

CONFIG_TARGET="$HERMES_HOME/config.yaml"
OVERLAY="$STARTER_DIR/config.example.yaml"
if [ ! -f "$CONFIG_TARGET" ]; then
  run cp "$OVERLAY" "$CONFIG_TARGET"
  ok "created $CONFIG_TARGET from the overlay"
else
  # A real config is already here. Merging YAML blind would be destructive, so
  # we only show what would change and hand over the merge command.
  warn "$CONFIG_TARGET already exists — not modifying it"
  echo
  echo "  Review the feature overlay against your config:"
  echo "    python3 $STARTER_DIR/scripts/merge_config.py \\"
  echo "        --base '$CONFIG_TARGET' --overlay '$OVERLAY'"
  echo
  echo "  Apply it (adds only missing keys, keeps a timestamped .bak):"
  echo "    python3 $STARTER_DIR/scripts/merge_config.py \\"
  echo "        --base '$CONFIG_TARGET' --overlay '$OVERLAY' --apply"
  echo
fi

# --- done ------------------------------------------------------------------
echo
info "Done"
cat <<EOF
Next steps:
  1. Put your API keys in $ENV_TARGET  (never commit this file)
  2. Set your Telegram/Discord allowlist in the same file — an agent with no
     allowlist will talk to anyone who finds it.
  3. Start it:  $HERMES_BIN
  4. The optional coding wrappers use your separately installed Claude/Codex
     subscriptions. Check them with: claude auth status; codex login status

Docs: $STARTER_DIR/README.md · $STARTER_DIR/docs/TROUBLESHOOTING.md
EOF
