#!/usr/bin/env python3
"""Build, validate, unpack, and hash a release artifact from one Git commit."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile

from check_release_inputs import forbidden


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_members(bundle: tarfile.TarFile, prefix: str, inventory: list[str]) -> None:
    files: list[str] = []
    prefix_path = PurePosixPath(prefix)
    for member in bundle.getmembers():
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts or "\\" in member.name:
            raise ValueError("archive contains a traversal path")
        if not name.parts or name.parts[0] != prefix_path.parts[0]:
            raise ValueError("archive member escapes the release prefix")
        relative = PurePosixPath(*name.parts[1:]).as_posix()
        if member.isdev() or member.isfifo() or member.islnk() or member.issym():
            raise ValueError("archive contains a link or unsafe special member")
        if member.isfile():
            if forbidden(relative):
                raise ValueError(f"archive contains forbidden release path: {relative}")
            files.append(relative)
    if sorted(files) != inventory:
        raise ValueError("archive paths do not exactly match the tracked release inventory")


def safe_unpack(archive: Path, destination: Path, prefix: str, inventory: list[str]) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        validate_members(bundle, prefix, inventory)
        bundle.extractall(destination)


def git_output(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if proc.returncode:
        raise RuntimeError("Git could not resolve the release candidate")
    return proc.stdout.strip()


def build(root: Path, candidate: str, output: Path, name: str) -> Path:
    commit = git_output(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
    inventory_text = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:release/tracked-files.txt"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if inventory_text.returncode:
        raise RuntimeError("candidate has no readable tracked-file inventory")
    inventory = [line.strip() for line in inventory_text.stdout.splitlines() if line.strip()]
    if inventory != sorted(set(inventory)):
        raise ValueError("candidate tracked-file inventory is invalid")
    prefix = f"{name}/"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{name}.tar.gz"
    unpacked = output / "unpacked"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    unpacked.mkdir()

    with tempfile.TemporaryDirectory(prefix="hermes-release-build-") as temporary:
        tar_path = Path(temporary) / "candidate.tar"
        with tar_path.open("wb") as handle:
            proc = subprocess.run(
                ["git", "-C", str(root), "archive", "--format=tar", f"--prefix={prefix}", commit],
                stdout=handle, stderr=subprocess.PIPE, check=False,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            )
        if proc.returncode:
            raise RuntimeError("git archive failed")
        temporary_archive = Path(temporary) / archive.name
        with tar_path.open("rb") as source, temporary_archive.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        os.replace(temporary_archive, archive)

    safe_unpack(archive, unpacked, prefix, inventory)
    manifest = {
        "schema": 1,
        "candidate_commit": commit,
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "tracked_inventory_sha256": hashlib.sha256(
            inventory_text.stdout.encode("utf-8")
        ).hexdigest(),
        "file_count": len(inventory),
        "unpacked_root": prefix.rstrip("/"),
    }
    run_manifest = output / "release-run.json"
    run_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--output", required=True)
    parser.add_argument("--name", default="hermes-x-jasper-v0.3.0")
    args = parser.parse_args(argv)
    try:
        build(Path(args.repo).resolve(), args.candidate, Path(args.output).resolve(), args.name)
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        print(f"release artifact rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
