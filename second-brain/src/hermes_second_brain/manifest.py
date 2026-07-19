"""Manifest loading and approved-root validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import tomllib

DEFAULT_INCLUDE = ["**/*.md", "**/*.txt", "**/*.rst"]
DEFAULT_EXCLUDE = [
    "**/.git/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/node_modules/**",
    "**/dist/**",
    "**/build/**",
    "**/*.sqlite",
    "**/*.sqlite3",
    "**/*.db",
    "**/*.jsonl",
    "**/.env",
    "**/.env.*",
    "**/*secret*",
    "**/*token*",
    "**/*credential*",
    "**/*private*key*",
    "**/id_rsa*",
    "**/id_ed25519*",
]


@dataclass(frozen=True)
class ApprovedRoot:
    name: str
    path: Path
    namespace: str
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    summary_max_chars: int = 1200


@dataclass(frozen=True)
class OpenVikingConfig:
    command: list[str] = field(default_factory=lambda: ["ov"])
    namespace_prefix: str = "second-brain"


@dataclass(frozen=True)
class Manifest:
    path: Path
    state_path: Path
    roots: list[ApprovedRoot]
    openviking: OpenVikingConfig


def _expand_path(raw: str, base_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def load_manifest(path: str | Path) -> Manifest:
    manifest_path = Path(path).expanduser().resolve()
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    base_dir = manifest_path.parent

    state_raw = data.get("state_path", ".second-brain/state.sqlite3")
    state_path = _expand_path(str(state_raw), base_dir)

    roots: list[ApprovedRoot] = []
    for item in data.get("approved_roots", []):
        name = str(item["name"])
        namespace = str(item.get("namespace", name))
        roots.append(
            ApprovedRoot(
                name=name,
                path=_expand_path(str(item["path"]), base_dir),
                namespace=namespace,
                include=list(item.get("include", DEFAULT_INCLUDE)),
                exclude=list(item.get("exclude", DEFAULT_EXCLUDE)),
                summary_max_chars=int(item.get("summary_max_chars", 1200)),
            )
        )
    if not roots:
        raise ValueError("manifest must declare at least one [[approved_roots]] entry")

    ov_data = data.get("openviking", {})
    command = ov_data.get("command", ["ov"])
    if isinstance(command, str):
        command = command.split()
    if not command:
        raise ValueError("openviking.command must not be empty")

    return Manifest(
        path=manifest_path,
        state_path=state_path,
        roots=roots,
        openviking=OpenVikingConfig(
            command=[str(part) for part in command],
            namespace_prefix=str(ov_data.get("namespace_prefix", "second-brain")),
        ),
    )
