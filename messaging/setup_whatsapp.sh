#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Hermes messaging — WhatsApp bridge installer (macOS / launchd)
#
# Idempotent. Points a per-user LaunchAgent at the UPSTREAM Hermes WhatsApp
# (Baileys) bridge — scripts/whatsapp-bridge in the pinned upstream checkout.
# This starter does NOT vendor the bridge; it only renders and loads a launchd
# job that runs it on loopback in --mode self-chat.
#
#   ./setup_whatsapp.sh
#
# Environment overrides:
#   HERMES_INSTALL_DIR      upstream checkout (default: $HOME/hermes-agent)
#   HERMES_WA_BRIDGE_DIR    bridge dir (default: $HERMES_INSTALL_DIR/scripts/whatsapp-bridge)
#   HERMES_WA_BRIDGE_ENTRY  bridge entry file, relative to the bridge dir or absolute
#   HERMES_WA_SKIP_NPM      set to 1 to skip `npm install` in the bridge dir
# ---------------------------------------------------------------------------
set -euo pipefail

LABEL="com.example.hermes-messaging.whatsapp"
PORT=3000
HEALTH_URL="http://127.0.0.1:${PORT}/health"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/launchd/${LABEL}.plist.template"

INSTALL_DIR="${HERMES_INSTALL_DIR:-$HOME/hermes-agent}"
BRIDGE_DIR="${HERMES_WA_BRIDGE_DIR:-$INSTALL_DIR/scripts/whatsapp-bridge}"
SESSION_DIR="$HOME/.hermes/platforms/whatsapp/session"
LOG_DIR="$HOME/.hermes/second-brain/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS_DIR/${LABEL}.plist"

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYAN" "$NC" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%s  !!%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
die()  { printf '%s error:%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

# --- 1. tool checks --------------------------------------------------------
info "Checking prerequisites"
[ "$(uname -s)" = "Darwin" ] || die "this installer is macOS-first (uses launchd). See README.md for other platforms."
command -v node >/dev/null 2>&1 || die "node not found. Install it first: brew install node"
command -v npm  >/dev/null 2>&1 || die "npm not found. Install Node.js (brew install node)"
command -v launchctl >/dev/null 2>&1 || die "launchctl not found — this must run on macOS"
command -v curl >/dev/null 2>&1 || die "curl not found"
[ -f "$TEMPLATE" ] || die "missing launchd template: $TEMPLATE"

NODE_BIN="$(command -v node)"
ok "node: $NODE_BIN ($(node --version 2>/dev/null || echo '?'))"

# --- 2. locate the upstream bridge (do not vendor) -------------------------
info "Locating the upstream WhatsApp bridge"
if [ ! -d "$BRIDGE_DIR" ]; then
  warn "bridge directory not found: $BRIDGE_DIR"
  cat >&2 <<EOF
  The WhatsApp Baileys bridge is part of the upstream Hermes Agent checkout and
  is intentionally not vendored into this repo. Get it first, then re-run:

    1. Run this starter's ./setup.sh (clones NousResearch/hermes-agent into
       \$HOME/hermes-agent at the pinned commit), or
    2. Point this installer at your checkout:
         HERMES_WA_BRIDGE_DIR=/path/to/hermes-agent/scripts/whatsapp-bridge \\
           ./setup_whatsapp.sh
EOF
  die "bridge not available yet (see guidance above)"
fi
ok "bridge dir: $BRIDGE_DIR"

# Resolve the bridge entry file. Prefer an explicit override, then package.json
# "main", then common conventions. Keep it absolute for launchd.
resolve_entry() {
  local override="${HERMES_WA_BRIDGE_ENTRY:-}"
  if [ -n "$override" ]; then
    case "$override" in
      /*) printf '%s\n' "$override" ;;
      *)  printf '%s\n' "$BRIDGE_DIR/$override" ;;
    esac
    return 0
  fi
  local pkg="$BRIDGE_DIR/package.json" main=""
  if [ -f "$pkg" ]; then
    # Cheap, dependency-free read of the top-level "main" field.
    main="$(sed -n 's/.*"main"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$pkg" | head -n1)"
  fi
  if [ -n "$main" ] && [ -f "$BRIDGE_DIR/$main" ]; then
    printf '%s\n' "$BRIDGE_DIR/$main"
    return 0
  fi
  local candidate
  for candidate in index.js index.mjs server.js dist/index.js src/index.js; do
    if [ -f "$BRIDGE_DIR/$candidate" ]; then
      printf '%s\n' "$BRIDGE_DIR/$candidate"
      return 0
    fi
  done
  return 1
}

if BRIDGE_ENTRY="$(resolve_entry)"; then
  ok "bridge entry: $BRIDGE_ENTRY"
else
  BRIDGE_ENTRY="$BRIDGE_DIR/index.js"
  warn "could not find a bridge entry file under $BRIDGE_DIR"
  warn "defaulting to $BRIDGE_ENTRY — set HERMES_WA_BRIDGE_ENTRY if that is wrong"
fi

# --- 3. install bridge dependencies (idempotent) ---------------------------
if [ -f "$BRIDGE_DIR/package.json" ] && [ ! -d "$BRIDGE_DIR/node_modules" ]; then
  if [ "${HERMES_WA_SKIP_NPM:-0}" = "1" ]; then
    warn "node_modules missing but HERMES_WA_SKIP_NPM=1 — the bridge may not start"
  else
    info "Installing bridge dependencies (npm install)"
    ( cd "$BRIDGE_DIR" && npm install --no-audit --no-fund ) || die "npm install failed in $BRIDGE_DIR"
    ok "dependencies installed"
  fi
else
  ok "bridge dependencies present"
fi

# --- 4. directories --------------------------------------------------------
info "Preparing directories"
mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"
mkdir -p "$SESSION_DIR"
# The session dir holds WhatsApp linked-device credentials — keep it private.
chmod 700 "$HOME/.hermes/platforms/whatsapp" "$SESSION_DIR" 2>/dev/null || true
ok "session dir: $SESSION_DIR (0700)"

# --- 5. render the launchd plist -------------------------------------------
info "Rendering launchd agent -> $PLIST"
render_template() {
  local content
  content="$(cat "$TEMPLATE")"
  content="${content//__NODE__/$NODE_BIN}"
  content="${content//__BRIDGE_DIR__\/index.js/$BRIDGE_ENTRY}"
  content="${content//__BRIDGE_DIR__/$BRIDGE_DIR}"
  content="${content//__HOME__/$HOME}"
  printf '%s\n' "$content"
}
render_template > "$PLIST"
chmod 644 "$PLIST"
if grep -q '__[A-Z_]*__' "$PLIST"; then
  die "unsubstituted placeholder left in $PLIST — refusing to load"
fi
ok "rendered $PLIST"

# --- 6. (re)load the agent -------------------------------------------------
info "Loading the LaunchAgent"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if ! launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load -w "$PLIST" || die "launchctl could not load $PLIST"
fi
ok "loaded $LABEL"

# --- 7. verify health ------------------------------------------------------
info "Checking bridge health at $HEALTH_URL"
BODY=""
attempt=0
while [ "$attempt" -lt 10 ]; do
  if BODY="$(curl -sS --max-time 3 "$HEALTH_URL" 2>/dev/null)" && [ -n "$BODY" ]; then
    break
  fi
  BODY=""
  attempt=$((attempt + 1))
  sleep 2
done

case "$BODY" in
  *'"status":"connected"'*)
    ok "WhatsApp bridge is connected and listening on 127.0.0.1:${PORT}."
    printf '\n%sNext:%s the agent is triggered from your own WhatsApp chat (self-chat mode).\n' "$GREEN" "$NC"
    ;;
  "")
    warn "no response from $HEALTH_URL after ~20s"
    cat >&2 <<EOF
  The agent is loaded but the bridge did not answer yet. Check the logs:

    tail -n 40 "$LOG_DIR/hermes-messaging.whatsapp.err.log"
    tail -n 40 "$LOG_DIR/hermes-messaging.whatsapp.out.log"

  Then re-check with:  curl -sS $HEALTH_URL
EOF
    die "bridge did not become healthy (see guidance above)"
    ;;
  *)
    info "bridge is up but not paired yet."
    cat <<EOF

  Pair this device with WhatsApp:
    1. Watch the bridge log for its pairing QR code:
         tail -f "$LOG_DIR/hermes-messaging.whatsapp.err.log"
    2. On your phone: WhatsApp -> Settings -> Linked Devices -> Link a Device.
    3. Scan the QR. Then confirm:
         curl -sS $HEALTH_URL      # expect "status":"connected"

  The agent is triggered from your own WhatsApp chat (self-chat mode).
EOF
    ;;
esac

printf '\n%sManage the agent:%s\n' "$CYAN" "$NC"
printf '  launchctl kickstart -k %s/%s   # restart\n' "$DOMAIN" "$LABEL"
printf '  launchctl bootout %s/%s        # stop and unload\n' "$DOMAIN" "$LABEL"
