#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Hermes messaging — Signal daemon installer (macOS / launchd)
#
# Idempotent. Links this machine as a Signal linked device via signal-cli, then
# installs a per-user LaunchAgent that runs the signal-cli JSON-RPC daemon on
# loopback (127.0.0.1:8080) for the Hermes gateway.
#
#   ./setup_signal.sh
#
# The linking flow is headless: it captures the sgnl:// linking URI to a
# chmod-600 temp file, renders a QR PNG with qrencode, opens it, and polls
# `signal-cli listAccounts` until the device is linked. Temp files are removed
# on exit.
#
# Environment overrides:
#   HERMES_SIGNAL_DEVICE_NAME   linked-device name (default: Hermes)
#   HERMES_SIGNAL_LINK_TRIES    listAccounts poll attempts (default: 60, ~2s each)
# ---------------------------------------------------------------------------
set -euo pipefail

LABEL="com.example.hermes-messaging.signal"
PORT=8080
HTTP_URL="http://127.0.0.1:${PORT}"
CHECK_URL="${HTTP_URL}/api/v1/check"
DEVICE_NAME="${HERMES_SIGNAL_DEVICE_NAME:-Hermes}"
LINK_TRIES="${HERMES_SIGNAL_LINK_TRIES:-60}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/launchd/${LABEL}.plist.template"

LOG_DIR="$HOME/.hermes/second-brain/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS_DIR/${LABEL}.plist"

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYAN" "$NC" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%s  !!%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
die()  { printf '%s error:%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

WORK_DIR=""
LINK_PID=""
cleanup() {
  if [ -n "$LINK_PID" ] && kill -0 "$LINK_PID" 2>/dev/null; then
    kill "$LINK_PID" 2>/dev/null || true
  fi
  # Remove the QR PNG and sgnl:// URI temp files.
  if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

# --- 1. tool checks --------------------------------------------------------
info "Checking prerequisites"
[ "$(uname -s)" = "Darwin" ] || die "this installer is macOS-first (uses launchd). See README.md for other platforms."
command -v signal-cli >/dev/null 2>&1 || die "signal-cli not found. Install it: brew install signal-cli"
command -v launchctl >/dev/null 2>&1 || die "launchctl not found — this must run on macOS"
command -v curl >/dev/null 2>&1 || die "curl not found"
[ -f "$TEMPLATE" ] || die "missing launchd template: $TEMPLATE"

SIGNAL_CLI_BIN="$(command -v signal-cli)"

# signal-cli needs a Java 17+ runtime. Homebrew's formula pulls its own JDK, so
# a working `signal-cli --version` is the real gate; the java probe is advisory.
if command -v java >/dev/null 2>&1; then
  JAVA_RAW="$(java -version 2>&1 | head -n1)"
  JAVA_MAJOR=""
  if [[ "$JAVA_RAW" =~ version\ \"([0-9]+)\.?([0-9]+)? ]]; then
    JAVA_MAJOR="${BASH_REMATCH[1]}"
    if [ "$JAVA_MAJOR" = "1" ]; then JAVA_MAJOR="${BASH_REMATCH[2]}"; fi
  fi
  if [ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -lt 17 ]; then
    warn "the java on PATH is $JAVA_MAJOR (signal-cli needs 17+); relying on signal-cli's own runtime"
  fi
fi
if ! signal-cli --version >/dev/null 2>&1; then
  die "signal-cli is installed but did not run. It needs a Java 17+ runtime: brew install openjdk@17"
fi
ok "signal-cli: $SIGNAL_CLI_BIN ($(signal-cli --version 2>/dev/null || echo '?'))"

# --- 2. link a device if we are not already linked -------------------------
account_from_list() {
  # Print the first E.164 number signal-cli reports, or nothing.
  signal-cli listAccounts 2>/dev/null | grep -oE '\+[0-9]{7,15}' | head -n1 || true
}

ACCOUNT="$(account_from_list)"
if [ -n "$ACCOUNT" ]; then
  ok "already linked as $ACCOUNT — skipping the pairing flow"
else
  command -v qrencode >/dev/null 2>&1 || die "qrencode not found (needed to render the link QR): brew install qrencode"

  info "Linking this machine to Signal as \"$DEVICE_NAME\""
  WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-signal.XXXXXX")"
  chmod 700 "$WORK_DIR"
  URI_FILE="$WORK_DIR/link-uri.txt"
  QR_PNG="$WORK_DIR/link-qr.png"
  : > "$URI_FILE"
  chmod 600 "$URI_FILE"

  # `signal-cli link` prints the sgnl:// URI, then blocks until the device is
  # linked. Run it in the background and capture the URI.
  signal-cli link -n "$DEVICE_NAME" >"$URI_FILE" 2>"$WORK_DIR/link.err" &
  LINK_PID=$!

  info "Waiting for the linking URI"
  URI=""
  wait_attempt=0
  while [ "$wait_attempt" -lt 50 ]; do
    URI="$(grep -Eo '^(sgnl|tsdevice)://[^[:space:]]+' "$URI_FILE" 2>/dev/null | head -n1 || true)"
    [ -n "$URI" ] && break
    if ! kill -0 "$LINK_PID" 2>/dev/null; then
      warn "signal-cli link exited early:"
      cat "$WORK_DIR/link.err" >&2 || true
      die "could not obtain a linking URI"
    fi
    wait_attempt=$((wait_attempt + 1))
    sleep 0.2
  done
  [ -n "$URI" ] || die "timed out waiting for the linking URI from signal-cli"

  info "Rendering the link QR"
  qrencode -o "$QR_PNG" "$URI" || die "qrencode failed to render the QR"
  chmod 600 "$QR_PNG"
  if command -v open >/dev/null 2>&1; then
    open "$QR_PNG" || true
  fi
  cat <<EOF

  Scan the QR that just opened with your phone:
    Signal -> Settings -> Linked Devices -> Link New Device.
  (If it did not open: $QR_PNG)
EOF

  info "Waiting for the device to appear (up to $LINK_TRIES checks)"
  poll=0
  while [ "$poll" -lt "$LINK_TRIES" ]; do
    ACCOUNT="$(account_from_list)"
    [ -n "$ACCOUNT" ] && break
    poll=$((poll + 1))
    sleep 2
  done
  # The `link` process persists local state and then exits on its own; give it
  # a bounded grace period to do so, then stop it. The account is already saved
  # (listAccounts reported it), so stopping a lingering process is safe.
  reap=0
  while [ "$reap" -lt 10 ] && kill -0 "$LINK_PID" 2>/dev/null; do
    reap=$((reap + 1))
    sleep 1
  done
  if kill -0 "$LINK_PID" 2>/dev/null; then
    kill "$LINK_PID" 2>/dev/null || true
  fi
  LINK_PID=""
  [ -n "$ACCOUNT" ] || die "device did not link in time. Re-run ./setup_signal.sh to try again."
  ok "linked as $ACCOUNT"
fi

# --- 3. directories --------------------------------------------------------
info "Preparing directories"
mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"
ok "logs: $LOG_DIR"

# --- 4. render the launchd plist -------------------------------------------
info "Rendering launchd agent -> $PLIST"
render_template() {
  local content
  content="$(cat "$TEMPLATE")"
  content="${content//__SIGNAL_CLI__/$SIGNAL_CLI_BIN}"
  content="${content//__SIGNAL_E164_ACCOUNT__/$ACCOUNT}"
  content="${content//__HOME__/$HOME}"
  printf '%s\n' "$content"
}
render_template > "$PLIST"
chmod 600 "$PLIST"
if grep -q '__[A-Z_]*__' "$PLIST"; then
  die "unsubstituted placeholder left in $PLIST — refusing to load"
fi
ok "rendered $PLIST"

# --- 5. (re)load the agent -------------------------------------------------
info "Loading the LaunchAgent"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if ! launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load -w "$PLIST" || die "launchctl could not load $PLIST"
fi
ok "loaded $LABEL"

# --- 6. verify the daemon is listening -------------------------------------
info "Checking the Signal daemon at $CHECK_URL"
CODE="000"
attempt=0
while [ "$attempt" -lt 10 ]; do
  CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$CHECK_URL" 2>/dev/null || true)"
  if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done

if [ "$CODE" = "000" ] || [ -z "$CODE" ]; then
  warn "the Signal daemon is not answering on $HTTP_URL yet"
  cat >&2 <<EOF
  Check the logs, then re-check:
    tail -n 40 "$LOG_DIR/hermes-messaging.signal.err.log"
    curl -sS $CHECK_URL
EOF
  die "daemon did not come up (see guidance above)"
fi
ok "Signal daemon is listening on 127.0.0.1:${PORT} (HTTP $CODE)"

# --- 7. print the gateway env lines ----------------------------------------
cat <<EOF

$(printf '%sAdd these to ~/.hermes/.env%s (the gateway reads them):' "$GREEN" "$NC")

  SIGNAL_ACCOUNT=$ACCOUNT
  SIGNAL_HTTP_URL=$HTTP_URL

$(printf '%sManage the agent:%s' "$CYAN" "$NC")
  launchctl kickstart -k $DOMAIN/$LABEL   # restart
  launchctl bootout $DOMAIN/$LABEL        # stop and unload

Note-to-Self is the Signal trigger: message yourself to reach the agent.
EOF
