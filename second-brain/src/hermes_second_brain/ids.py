"""Deterministic resource identifiers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


def slug(value: str) -> str:
    cleaned = _SAFE.sub("-", value.strip()).strip("-").lower()
    return cleaned or "default"


def namespace_resource_id(namespace: str, root: Path, path: Path) -> str:
    """Return a stable, non-revealing id for a file inside an approved root."""
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:24]
    return f"urn:hermes-second-brain:{slug(namespace)}:{digest}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
