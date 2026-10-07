#!/usr/bin/env bash
# Install the sanitized server snapshot into a fresh, isolated directory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$ROOT/scripts/setup_server.py" "$@"
