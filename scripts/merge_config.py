#!/usr/bin/env python3
"""Safely merge a YAML overlay into an existing Hermes config.

Hermes keeps everything in ``$HERMES_HOME/config.yaml``. This starter ships a
small feature overlay (``config.example.yaml``) that has to be folded into a
config that may already contain the user's own settings — so the merge is
non-destructive by default and never runs without being asked.

Safety properties:

* ``yaml.safe_load`` only — no arbitrary object construction from YAML.
* Dry-run by default: prints a unified diff and changes nothing.
* ``--apply`` writes atomically (temp file + ``os.replace`` on the same
  filesystem) and keeps a timestamped ``.bak`` of the previous file.
* Default strategy ``keep-existing`` only fills in keys the base is missing;
  values you already set are never touched. ``overlay-wins`` is opt-in.

Usage:
    merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml
    merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml --apply
    merge_config.py --base ... --overlay ... --strategy overlay-wins --apply
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

KEEP_EXISTING = "keep-existing"
OVERLAY_WINS = "overlay-wins"


def deep_merge(base: Any, overlay: Any, strategy: str = KEEP_EXISTING) -> Any:
    """Recursively merge *overlay* into *base* and return a new structure.

    Mappings merge key by key. Everything else (scalars, lists) is a leaf:
    lists are replaced wholesale rather than concatenated, because Hermes list
    settings (toolsets, phrases, allowlists) are semantically "the whole set".
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value, strategy)
            else:
                merged[key] = value
        return merged
    if base is None:
        return overlay
    return overlay if strategy == OVERLAY_WINS else base


def load_yaml(path: Path) -> dict:
    """Parse a YAML mapping with safe_load. Missing/empty file -> {}."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level, got {type(data).__name__}")
    return data


def dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _source_mode(path: Path) -> int:
    """Return the source mode, or a private default for a new config."""
    if not path.exists():
        return 0o600
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{path}: expected a regular file, not a link or special file")
    return stat.S_IMODE(metadata.st_mode)


def write_atomic(path: Path, content: str, mode: int | None = None) -> None:
    """Write *content* atomically while preserving or narrowing permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    selected_mode = _source_mode(path) if mode is None else mode
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, selected_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, selected_mode)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def backup(path: Path) -> Path | None:
    """Create an exclusive, collision-safe backup with the source mode."""
    if not path.exists():
        return None
    mode = _source_mode(path)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    nonce = time.time_ns()
    counter = 0
    while True:
        suffix = f".bak-{stamp}-{nonce}"
        if counter:
            suffix += f"-{counter}"
        target = path.with_name(path.name + suffix)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            counter += 1
            continue
        try:
            os.fchmod(fd, mode)
            with path.open("rb") as source, os.fdopen(fd, "wb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(target, mode)
            return target
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            target.unlink(missing_ok=True)
            raise


def diff(before: str, after: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{name} (current)",
        tofile=f"{name} (merged)",
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="config.yaml to merge into")
    parser.add_argument("--overlay", required=True, help="YAML overlay to apply")
    parser.add_argument("--strategy", choices=[KEEP_EXISTING, OVERLAY_WINS], default=KEEP_EXISTING,
                        help="keep-existing (default): only add missing keys; "
                             "overlay-wins: overlay replaces conflicting values")
    parser.add_argument("--apply", action="store_true",
                        help="actually write the merged config (default: dry-run diff only)")
    args = parser.parse_args(argv)

    base_path = Path(args.base).expanduser()
    overlay_path = Path(args.overlay).expanduser()

    if not overlay_path.exists():
        print(f"overlay not found: {overlay_path}", file=sys.stderr)
        return 2

    try:
        base_data = load_yaml(base_path)
        overlay_data = load_yaml(overlay_path)
    except (yaml.YAMLError, ValueError) as exc:
        print(f"failed to parse YAML: {exc}", file=sys.stderr)
        return 2

    merged = deep_merge(base_data, overlay_data, args.strategy)
    before = base_path.read_text(encoding="utf-8") if base_path.exists() else ""
    after = dump_yaml(merged)

    if dump_yaml(base_data) == after:
        print(f"no changes needed: {base_path} already contains the overlay")
        return 0

    patch = diff(before, after, str(base_path))
    if not args.apply:
        print(patch or "(no textual diff)")
        print(f"\nDry run — nothing written. Re-run with --apply to update {base_path}.")
        return 0

    try:
        mode = _source_mode(base_path)
        saved = backup(base_path)
        write_atomic(base_path, after, mode=mode)
    except (OSError, ValueError) as exc:
        print(f"failed to write config safely: {exc}", file=sys.stderr)
        return 2
    if saved:
        print(f"backup written: {saved}")
    print(f"merged ({args.strategy}): {base_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
