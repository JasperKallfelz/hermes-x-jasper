#!/bin/sh
set -eu

module_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
if command -v python3 >/dev/null 2>&1; then
  python_cmd=python3
elif command -v python >/dev/null 2>&1; then
  python_cmd=python
else
  echo "Pi runtime setup requires Python 3.11" >&2
  exit 1
fi
exec "$python_cmd" "$module_dir/pi_module.py" setup "$@"
