"""Approved-root scanner and public-safe summary generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import fnmatch
import json
import re

from .ids import namespace_resource_id, sha256_text
from .manifest import ApprovedRoot

SECRET_NAME_RE = re.compile(
    r"(^|[-_.])(secret|token|credential|password|passwd|private|key|id_rsa|id_ed25519)([-_.]|$)",
    re.IGNORECASE,
)
HOME_RE = re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9.-]+\.)[A-Za-z]{2,}\b")
TOKENISH_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|xox[abprs]-[A-Za-z0-9-]{12,})\b")


@dataclass(frozen=True)
class Resource:
    resource_id: str
    namespace: str
    root_name: str
    relative_path: str
    content_hash: str
    summary: str
    title: str

    def to_json_record(self, namespace_prefix: str = "second-brain") -> dict[str, object]:
        return {
            "id": self.resource_id,
            "namespace": f"{namespace_prefix}/{self.namespace}",
            "title": self.title,
            "summary": self.summary,
            "metadata": {
                "root": self.root_name,
                "relative_path": self.relative_path,
                "content_hash": self.content_hash,
            },
        }


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatch(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))
        for pattern in patterns
    )


def _is_secret_path(path: Path) -> bool:
    return any(SECRET_NAME_RE.search(part) for part in path.parts)


def _read_text(path: Path, max_bytes: int = 200_000) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    return raw[:max_bytes].decode("utf-8", errors="replace")


def redact(text: str) -> str:
    text = HOME_RE.sub("<home>", text)
    text = EMAIL_RE.sub("<email>", text)
    text = TOKENISH_RE.sub("<token>", text)
    return text


def summarize(text: str, limit: int) -> tuple[str, str]:
    redacted = redact(text)
    lines = [line.strip() for line in redacted.splitlines() if line.strip()]
    title = "Untitled"
    for line in lines:
        if line.startswith("#"):
            title = line.lstrip("#").strip() or title
            break
    if title == "Untitled" and lines:
        title = lines[0][:80]
    body = "\n".join(lines)
    if len(body) > limit:
        body = body[: max(0, limit - 3)].rstrip() + "..."
    return title, body


def scan_root(root: ApprovedRoot) -> list[Resource]:
    if not root.path.exists():
        raise FileNotFoundError(f"approved root does not exist: {root.name}")
    if not root.path.is_dir():
        raise NotADirectoryError(f"approved root is not a directory: {root.name}")

    resources: list[Resource] = []
    resolved_root = root.path.resolve()
    for path in sorted(p for p in root.path.rglob("*") if p.is_file()):
        # An approved-root boundary must not be bypassable via a symlink whose
        # name happens to match the include patterns.
        if path.is_symlink():
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError:
            continue
        rel = path.relative_to(root.path).as_posix()
        if _is_secret_path(Path(rel)):
            continue
        if not _matches_any(rel, root.include):
            continue
        if _matches_any(rel, root.exclude):
            continue
        text = _read_text(path)
        if text is None:
            continue
        title, summary = summarize(text, root.summary_max_chars)
        resources.append(
            Resource(
                resource_id=namespace_resource_id(root.namespace, root.path, path),
                namespace=root.namespace,
                root_name=root.name,
                relative_path=rel,
                content_hash=sha256_text(text),
                summary=summary,
                title=title,
            )
        )
    return resources


def scan_roots(roots: list[ApprovedRoot]) -> list[Resource]:
    resources: list[Resource] = []
    for root in roots:
        resources.extend(scan_root(root))
    return resources


def records_jsonl(resources: list[Resource], namespace_prefix: str) -> str:
    return "\n".join(
        json.dumps(resource.to_json_record(namespace_prefix), sort_keys=True)
        for resource in resources
    ) + ("\n" if resources else "")
