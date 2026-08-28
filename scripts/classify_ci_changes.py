#!/usr/bin/env python3
"""Fail-closed Pi path classification and required-job aggregation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import PurePosixPath
import sys
from typing import Any, Mapping


PI_EXACT_PATHS = {
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    "docs/FEATURES.md",
    "docs/RELEASING.md",
    "release/tracked-files.txt",
    "scripts/audit_public.py",
    "scripts/check_release_inputs.py",
    "scripts/classify_ci_changes.py",
    "scripts/gitleaks_scan.sh",
    "scripts/release_audit.sh",
}
PI_PREFIXES = (
    ".github/workflows/",
    "modules/pi-runtime/",
    "tests/",
)


def normalized_path(raw: str) -> str:
    """Accept only canonical repository-relative Git path names."""
    if not raw or "\\" in raw or raw.startswith("/") or "\0" in raw:
        raise ValueError(f"non-canonical changed path: {raw!r}")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"non-canonical changed path: {raw!r}")
    canonical = path.as_posix()
    if canonical != raw:
        raise ValueError(f"non-canonical changed path: {raw!r}")
    return canonical


def pi_runtime_required(paths: list[str], *, force: bool = False) -> bool:
    if force:
        return True
    for raw in paths:
        path = normalized_path(raw)
        if path in PI_EXACT_PATHS or path.startswith(PI_PREFIXES):
            return True
    return False


def unexpected_required_results(
    needs: Mapping[str, Any], *, pi_required: bool
) -> list[str]:
    """Return every required job whose result is absent, failed, or skipped."""
    unexpected: list[str] = []
    for name in ("changes", "release-gates"):
        raw = needs.get(name)
        result = str(raw.get("result") or "") if isinstance(raw, Mapping) else ""
        if result != "success":
            unexpected.append(name)
    raw_pi = needs.get("pi-runtime")
    pi_result = str(raw_pi.get("result") or "") if isinstance(raw_pi, Mapping) else ""
    if pi_required:
        if pi_result != "success":
            unexpected.append("pi-runtime")
    elif pi_result not in {"success", "skipped"}:
        unexpected.append("pi-runtime")
    return sorted(set(unexpected))


def _read_paths(nul: bool) -> list[str]:
    payload = sys.stdin.buffer.read()
    separator = b"\0" if nul else b"\n"
    return [item.decode("utf-8") for item in payload.split(separator) if item]


def _write_output(required: bool, destination: str | None) -> None:
    line = f"pi_runtime={'true' if required else 'false'}\n"
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        sys.stdout.write(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nul", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--github-output")
    parser.add_argument("--check-needs", action="store_true")
    args = parser.parse_args(argv)
    if args.check_needs:
        raw = os.environ.get("NEEDS_JSON", "")
        try:
            needs = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"invalid NEEDS_JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(needs, dict):
            print("NEEDS_JSON must be an object", file=sys.stderr)
            return 1
        required = os.environ.get("PI_RUNTIME_REQUIRED", "").lower() == "true"
        failures = unexpected_required_results(needs, pi_required=required)
        if failures:
            print("required CI jobs did not succeed: " + ", ".join(failures), file=sys.stderr)
            return 1
        print("all required CI jobs succeeded")
        return 0
    try:
        required = pi_runtime_required(_read_paths(args.nul), force=args.force)
    except (UnicodeDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _write_output(required, args.github_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
