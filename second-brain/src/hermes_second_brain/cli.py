"""Command line interface for the public-safe Second Brain module."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

from .manifest import load_manifest
from .scanner import scan_roots
from .sync import sync_manifest

EXAMPLE_MANIFEST = """# Public-safe Second Brain manifest.
# Raw files stay local. Sync exports redacted summaries only.
state_path = ".second-brain/state.sqlite3"

[openviking]
command = ["ov"]
namespace_prefix = "second-brain"

[[approved_roots]]
name = "notes"
path = "./notes"
namespace = "notes"
include = ["**/*.md", "**/*.txt"]
exclude = ["**/.git/**", "**/.venv/**", "**/*.sqlite*", "**/*.jsonl", "**/.env*", "**/*secret*", "**/*token*", "**/*credential*", "**/*private*key*"]
summary_max_chars = 1200
"""


def _cmd_init(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest)
    if manifest.exists() and not args.force:
        print(f"exists: {manifest}")
        return 0
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(EXAMPLE_MANIFEST, encoding="utf-8")
    notes = manifest.parent / "notes"
    notes.mkdir(exist_ok=True)
    sample = notes / "welcome.md"
    if not sample.exists():
        sample.write_text("# Welcome\n\nReplace this with local notes you approve for summary export.\n", encoding="utf-8")
    print(f"created: {manifest}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    resources = scan_roots(manifest.roots)
    for resource in resources:
        print(f"{resource.resource_id} {resource.namespace}/{resource.relative_path}")
    print(f"scanned={len(resources)}")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    plan = sync_manifest(manifest, dry_run=args.dry_run)
    mode = "dry-run" if args.dry_run else "export"
    print(f"{mode}: scanned={plan.scanned} changed={len(plan.changed)} command={shutil.which(plan.export.command[0]) or plan.export.command[0]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-second-brain")
    parser.add_argument("--manifest", default="second-brain.toml", help="Path to the local manifest")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a safe example manifest and notes directory")
    init.add_argument("--manifest", default=argparse.SUPPRESS, help="Path to the local manifest")
    init.add_argument("--force", action="store_true", help="Overwrite the manifest")
    init.set_defaults(func=_cmd_init)

    scan = sub.add_parser("scan", help="List approved local resources")
    scan.add_argument("--manifest", default=argparse.SUPPRESS, help="Path to the local manifest")
    scan.set_defaults(func=_cmd_scan)

    sync = sub.add_parser("sync", help="Sync redacted summaries to an OpenViking CLI")
    sync.add_argument("--manifest", default=argparse.SUPPRESS, help="Path to the local manifest")
    sync.add_argument("--dry-run", action="store_true", default=False, help="Do not invoke OpenViking")
    sync.set_defaults(func=_cmd_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
