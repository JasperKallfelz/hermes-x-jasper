#!/bin/sh
set -eu

module_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
python_cmd=""
for candidate in python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    python_cmd=$(command -v "$candidate")
    break
  fi
done
if [ -z "$python_cmd" ]; then
  echo "Pi runtime verification requires Python 3.11 or newer" >&2
  exit 1
fi
exec "$python_cmd" "$module_dir/pi_module.py" verify "$@"
