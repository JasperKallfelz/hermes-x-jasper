#!/usr/bin/env bash
# Explicit local deployment. Nothing invokes this installer automatically.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
[[ -n "${HOME:-}" && "$HOME" == /* ]] || { echo "HOME must be absolute." >&2; exit 1; }
HERMES_ROOT="${HERMES_HOME:-$HOME/.hermes}"
[[ "$HERMES_ROOT" == /* ]] || { echo "HERMES_HOME must be absolute." >&2; exit 1; }
BRIDGE_DEST="${HERMES_DEEP_CHAT_BRIDGE_DEST:-$HOME/.local/bin/hermes-deep-chat}"
RUNTIME_DIR="${HERMES_DEEP_CHAT_RUNTIME_DIR:-$HERMES_ROOT/bin}"

[[ $# -eq 0 ]] || { echo "Usage: tools/deep-chat/install-local.sh" >&2; exit 2; }
[[ -f "$SOURCE_DIR/hermes-deep-chat" && -f "$SOURCE_DIR/claude_worker.py" \
   && -f "$SOURCE_DIR/deep_chat_bridge.py" && -f "$SOURCE_DIR/secure_runtime.py" ]] \
  || { echo "Canonical Deep Chat sources are missing." >&2; exit 1; }
[[ "$BRIDGE_DEST" == /* && "$RUNTIME_DIR" == /* ]] \
  || { echo "Deep Chat install destinations must be absolute paths." >&2; exit 1; }

mkdir -p "$(dirname "$BRIDGE_DEST")" "$RUNTIME_DIR"
chmod 0700 "$RUNTIME_DIR"
install -m 0755 "$SOURCE_DIR/hermes-deep-chat" "$BRIDGE_DEST"
install -m 0755 "$SOURCE_DIR/claude_worker.py" "$RUNTIME_DIR/claude_worker.py"
install -m 0755 "$SOURCE_DIR/deep_chat_bridge.py" "$RUNTIME_DIR/deep_chat_bridge.py"
install -m 0755 "$SOURCE_DIR/secure_runtime.py" "$RUNTIME_DIR/secure_runtime.py"
printf 'Installed Deep Chat bridge to %s\n' "$BRIDGE_DEST"
printf 'Installed Deep Chat runtime to %s\n' "$RUNTIME_DIR"
