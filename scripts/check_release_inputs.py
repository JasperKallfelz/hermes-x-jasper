#!/usr/bin/env python3
"""Validate the exact tracked publication inventory and local release inputs."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath


FORBIDDEN_BASENAMES = {
    ".hermes-task.md", ".hermes-result.md", ".hermes-repair-task.md",
    ".hermes-repair-result.md", ".hermes-pi-module-task.md",
    ".hermes-pi-module-result.md", ".gitleaksignore", "config.yaml", "config.yml",
}
ARCHIVE_SUFFIXES = (".zip", ".tgz", ".tar", ".tar.gz")


def git(root: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=not binary,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )


def forbidden(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.name in FORBIDDEN_BASENAMES:
        return True
    if any(part in {"build", "dist"} for part in pure.parts):
        return True
    if path.endswith(ARCHIVE_SUFFIXES):
        return True
    return pure.name == ".env" or pure.name.startswith(".env.") and pure.name != ".env.example"


def nul_paths(proc: subprocess.CompletedProcess) -> list[str]:
    if proc.returncode:
        raise RuntimeError("Git could not enumerate release inputs")
    return sorted(item for item in proc.stdout.split("\0") if item)


def load_inventory(path: Path) -> list[str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if lines != sorted(set(lines)):
        raise ValueError("tracked-file inventory must be sorted and duplicate-free")
    return lines


def check(root: Path, candidate: str, inventory_path: Path) -> list[str]:
    errors: list[str] = []
    if git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return ["release input check requires a Git working tree"]
    commit = git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
    if commit.returncode:
        return ["release candidate is not a commit"]
    try:
        inventory = load_inventory(inventory_path)
        tracked = nul_paths(git(root, "ls-files", "-z"))
        candidate_paths = nul_paths(
            git(root, "ls-tree", "-r", "--name-only", "-z", commit.stdout.strip())
        )
        untracked = nul_paths(
            git(root, "ls-files", "--others", "--exclude-standard", "-z")
        )
        ignored = nul_paths(
            git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return [str(exc)]
    if tracked != inventory:
        errors.append("tracked working-tree paths do not exactly match release/tracked-files.txt")
    if candidate_paths != inventory:
        errors.append("candidate commit paths do not exactly match release/tracked-files.txt")
    if untracked:
        errors.append("non-ignored untracked release inputs exist")
    if any(PurePosixPath(path).name == ".gitleaksignore" for path in ignored):
        errors.append("an ignored, unreviewed .gitleaksignore exists")
    bad = sorted(path for path in set(tracked + candidate_paths) if forbidden(path))
    if bad:
        errors.append("forbidden release paths are tracked: " + ", ".join(bad))

    dirty = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty.returncode or dirty.stdout:
        errors.append("release worktree is not clean")

    ignored_contract = (
        ".hermes-task.md", ".hermes-result.md", ".hermes-repair-task.md",
        ".hermes-repair-result.md", ".hermes-pi-module-task.md",
        ".hermes-pi-module-result.md", "build/probe", "dist/probe", "probe.zip",
        "probe.tar", "probe.tar.gz", "probe.tgz",
    )
    for path in ignored_contract:
        ignored = git(root, "check-ignore", "--no-index", "--quiet", path)
        if ignored.returncode != 0:
            errors.append(f"required release exclusion is not ignored: {path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--inventory", default="release/tracked-files.txt")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    inventory = Path(args.inventory)
    if not inventory.is_absolute():
        inventory = root / inventory
    errors = check(root, args.candidate, inventory)
    for error in errors:
        print(f"release-inputs: {error}", file=sys.stderr)
    if errors:
        return 1
    print("release-inputs: candidate, tracked inventory, ignored controls, and worktree are exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
