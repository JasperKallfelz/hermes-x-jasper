from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .ids import source_id
from .manifest import BINARY_EXTENSIONS, DEFAULT_EXCLUDES, SECRET_NAME_HINTS, Source


@dataclass(frozen=True)
class ScannedResource:
    source_root_id: str
    source_id: str
    namespace: str
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    mtime_ns: int


def _is_secretish(path: Path) -> bool:
    for part in path.parts:
        if _secretish_name(part):
            return True
        if _secretish_name(Path(part).stem):
            return True
    return False


def _secretish_name(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(".env"):
        return True
    normalized = re.sub(r"[\s_.-]+", "-", lowered).strip("-")
    compact = normalized.replace("-", "")
    if normalized in SECRET_NAME_HINTS or compact in SECRET_NAME_HINTS:
        return True
    pieces = [piece for piece in normalized.split("-") if piece]
    if any(piece in SECRET_NAME_HINTS for piece in pieces):
        return True
    return any(pair in zip(pieces, pieces[1:]) for pair in {("api", "key"), ("private", "key"), ("auth", "token"), ("access", "key")})


def _excluded(rel: str, path: Path, source: Source) -> bool:
    if any(part in DEFAULT_EXCLUDES for part in path.parts):
        return True
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    if _is_secretish(path):
        return True
    return any(fnmatch.fnmatch(rel, pattern) for pattern in source.exclude_globs)


def _safe_child(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def scan_source(source: Source) -> list[ScannedResource]:
    root = source.root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"source root does not exist: {root}")
    resources: list[ScannedResource] = []
    # iCloud-backed folders occasionally interrupt a recursive directory read
    # while their metadata is being hydrated.  Retry once, then treat only this
    # source as temporarily unavailable: an unrelated source must never make the
    # whole Second-Brain sync fail. The next event/reconciliation pass retries it.
    paths: list[Path] = []
    for attempt in range(2):
        try:
            paths = sorted(root.rglob("*"))
            break
        except InterruptedError:
            if attempt == 1:
                return resources
    for path in paths:
        if path.is_symlink() or not path.is_file() or not _safe_child(root, path):
            continue
        rel = path.resolve().relative_to(root).as_posix()
        if _excluded(rel, path, source):
            continue
        if path.suffix.lower() not in source.include_extensions:
            continue
        stat = path.stat()
        if stat.st_size > source.max_bytes:
            continue
        resources.append(
            ScannedResource(
                source_root_id=source.id,
                source_id=source_id(source.namespace, path, root),
                namespace=source.namespace,
                path=path.resolve(),
                relative_path=rel,
                sha256=sha256_file(path),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )
    return resources
