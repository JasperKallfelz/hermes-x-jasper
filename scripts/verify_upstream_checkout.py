#!/usr/bin/env python3
"""Classify an upstream checkout without mutating its Git state.

The comparison clone uses a private, non-hardlinked object database.  Git
commands run against the operator checkout are read-only; expected clean and
patched manifests are built in the disposable clone.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


class CheckoutError(RuntimeError):
    """A checkout violates the install contract."""


def git(root: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
    )


def require_git(root: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess:
    proc = git(root, *args, text=text)
    if proc.returncode:
        raise CheckoutError(f"Git could not inspect {' '.join(args[:2])}")
    return proc


def _paths(root: Path) -> list[str]:
    """Return tracked paths plus every non-runtime filesystem entry.

    Git ignore state is deliberately irrelevant: `.git/info/exclude`, global
    excludes, and repository ignore rules must not hide an extra checkout file.
    The top-level `venv/` directory is the single explicit runtime exception;
    setup validates it separately. A symlink/special object named `venv` is not
    exempt and therefore makes the checkout differ.
    """
    tracked = {
        raw.decode("utf-8", "surrogateescape")
        for raw in require_git(root, "ls-files", "--cached", "-z").stdout.split(b"\0")
        if raw
    }
    observed = set(tracked)

    def walk_error(exc: OSError) -> None:
        raise CheckoutError("checkout contains an unreadable filesystem entry") from exc

    for current, dirs, files in os.walk(
        root, topdown=True, followlinks=False, onerror=walk_error
    ):
        current_path = Path(current)
        if current_path == root:
            dirs[:] = [name for name in dirs if name != ".git"]
            if "venv" in dirs:
                runtime = root / "venv"
                try:
                    metadata = runtime.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                        dirs.remove("venv")
        for name in list(dirs):
            candidate = current_path / name
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                observed.add(candidate.relative_to(root).as_posix())
                dirs.remove(name)
            else:
                observed.add(candidate.relative_to(root).as_posix())
        for name in files:
            observed.add((current_path / name).relative_to(root).as_posix())
    return sorted(observed)


def _entry(root: Path, relative: str) -> tuple[str, int, bytes] | tuple[str]:
    path = root / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return ("symlink", mode, os.fsencode(os.readlink(path)))
    if stat.S_ISREG(metadata.st_mode):
        # Git records only the executable bit, not the full local permission set.
        git_mode = 0o755 if mode & 0o111 else 0o644
        return ("file", git_mode, path.read_bytes())
    if stat.S_ISDIR(metadata.st_mode):
        return ("directory",)
    return ("special", mode, b"")


def manifest(root: Path) -> dict[str, tuple]:
    return {relative: _entry(root, relative) for relative in _paths(root)}


def _index(root: Path) -> bytes:
    return require_git(root, "ls-files", "--stage", "-z").stdout


def _validate_origin(root: Path, allow_test_origin: bool) -> None:
    proc = git(root, "remote", "get-url", "origin", text=True)
    if proc.returncode:
        raise CheckoutError("checkout has no readable origin")
    origin = proc.stdout.strip()
    allowed = {
        "https://github.com/NousResearch/hermes-agent",
        "https://github.com/NousResearch/hermes-agent.git",
        "git" + "@" + "github.com:NousResearch/hermes-agent.git",
        "ssh://git" + "@" + "github.com/NousResearch/hermes-agent.git",
    }
    if origin not in allowed and not allow_test_origin:
        # Never echo a rejected URL: it can contain credentials or query data.
        raise CheckoutError(
            "checkout has an unexpected origin; expected NousResearch/hermes-agent"
        )


def classify(
    root: Path, patch: Path, pin: str, allow_test_origin: bool = False
) -> str:
    if not (root / ".git").is_dir():
        raise CheckoutError("checkout is not a standalone Git repository")
    inside = git(root, "rev-parse", "--is-inside-work-tree", text=True)
    if inside.returncode or inside.stdout.strip() != "true":
        raise CheckoutError("checkout is not a valid Git working tree")
    _validate_origin(root, allow_test_origin)

    head = require_git(root, "rev-parse", "--verify", "HEAD", text=True).stdout.strip()
    if head != pin:
        raise CheckoutError("checkout HEAD is not the pinned commit")

    with tempfile.TemporaryDirectory(prefix="hermes-checkout-proof-") as temporary:
        expected = Path(temporary) / "expected"
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                "--no-checkout",
                str(root),
                str(expected),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            check=False,
        )
        if clone.returncode:
            raise CheckoutError("could not create the disposable comparison clone")
        checkout = git(expected, "checkout", "--quiet", "--detach", pin)
        if checkout.returncode:
            raise CheckoutError("could not check out the pin in the comparison clone")

        clean_manifest = manifest(expected)
        clean_index = _index(expected)
        actual_manifest = manifest(root)
        actual_index = _index(root)
        if actual_index != clean_index:
            raise CheckoutError("checkout index differs from the pinned commit")
        if actual_manifest == clean_manifest:
            return "clean"

        applied = git(expected, "apply", "--whitespace=error-all", str(patch))
        if applied.returncode:
            raise CheckoutError("starter patch does not apply plainly to the pinned commit")
        if actual_manifest == manifest(expected):
            return "patched"

    raise CheckoutError("checkout content differs from both accepted exact states")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--pin", required=True)
    parser.add_argument("--allow-test-origin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        state = classify(
            Path(args.checkout).resolve(),
            Path(args.patch).resolve(),
            args.pin,
            args.allow_test_origin,
        )
    except CheckoutError as exc:
        print(f"checkout rejected: {exc}", file=sys.stderr)
        return 1
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
