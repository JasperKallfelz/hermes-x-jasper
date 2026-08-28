#!/usr/bin/env python3
"""Create and validate the private, content-keyed setup completion marker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = 1
INPUT_NAMES = (
    "setup-hermes.sh",
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock",
    "setup.py",
    "setup.cfg",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dependency_digest(install_dir: Path) -> tuple[str, list[str]]:
    names = sorted(
        {
            name
            for name in INPUT_NAMES
            if (install_dir / name).is_file() and not (install_dir / name).is_symlink()
        }
        | {
            path.relative_to(install_dir).as_posix()
            for path in install_dir.glob("requirements*.txt")
            if path.is_file() and not path.is_symlink()
        }
    )
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((install_dir / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), names


def interpreter_identity(executable: Path) -> dict[str, str]:
    proc = subprocess.run(
        [str(executable), "-c", "import json,platform,sys; print(json.dumps({"
         "'implementation': platform.python_implementation(), "
         "'version': platform.python_version(), "
         "'cache_tag': sys.implementation.cache_tag or '', "
         "'executable': str(__import__('pathlib').Path(sys.executable).resolve())}))"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("environment Python failed its identity check")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("environment Python returned an invalid identity") from exc
    if not isinstance(value, dict):
        raise RuntimeError("environment Python returned an invalid identity")
    return {str(k): str(v) for k, v in value.items()}


def static_state(args: argparse.Namespace) -> dict[str, Any]:
    install_dir = Path(args.install_dir).resolve()
    inputs_digest, input_names = dependency_digest(install_dir)
    return {
        "schema": SCHEMA,
        "upstream_commit": args.pin,
        "upstream_tag": args.tag,
        "upstream_version": args.version,
        "patch_sha256": sha256_file(Path(args.patch)),
        "dependency_inputs_sha256": inputs_digest,
        "dependency_inputs": input_names,
        "installer": {
            "method": "setup-hermes.sh" if (install_dir / "setup-hermes.sh").is_file() else "editable-pip",
            "install_dir": str(install_dir),
            "hermes_home": str(Path(args.hermes_home).resolve()),
            "driver_python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "cache_tag": sys.implementation.cache_tag or "",
                "executable": str(Path(sys.executable).resolve()),
            },
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "voice_dependencies": [item for item in args.voice_set.split(",") if item],
    }


def verify_runtime(args: argparse.Namespace) -> dict[str, Any]:
    binary = Path(args.binary)
    if binary.is_symlink() or not binary.is_file():
        raise RuntimeError("Hermes executable is missing or unsafe")
    mode = binary.stat().st_mode
    if not mode & stat.S_IXUSR:
        raise RuntimeError("Hermes executable is not executable")
    version = subprocess.run(
        [str(binary), "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if version.returncode:
        raise RuntimeError("Hermes executable failed the version smoke test")
    combined = version.stdout + "\n" + version.stderr
    expected = args.version.lstrip("v")
    if not re.search(rf"(?<!\d){re.escape(expected)}(?!\d)", combined):
        raise RuntimeError("Hermes executable reported an unexpected version")

    venv_dir = binary.parent.parent
    python = venv_dir / "bin" / "python"
    pip = venv_dir / "bin" / "pip"
    if python.is_symlink():
        # venv Python launchers are commonly symlinks by design; integrity is
        # established by executing it and recording its resolved identity.
        pass
    if not python.exists() or not pip.is_file() or pip.is_symlink():
        raise RuntimeError("virtual environment is incomplete")
    checked = subprocess.run(
        [str(pip), "check"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if checked.returncode:
        raise RuntimeError("pip integrity check failed")
    return {
        "binary_sha256": sha256_file(binary),
        "venv_python": interpreter_identity(python),
    }


def expected_state(args: argparse.Namespace, include_runtime: bool) -> dict[str, Any]:
    value = static_state(args)
    if include_runtime:
        value["runtime"] = verify_runtime(args)
    return value


def marker_is_current(args: argparse.Namespace) -> bool:
    marker = Path(args.marker)
    if marker.is_symlink() or not marker.is_file():
        return False
    if stat.S_IMODE(marker.stat().st_mode) != 0o600:
        return False
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
        expected = expected_state(args, include_runtime=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return False
    return recorded == expected


def write_marker(args: argparse.Namespace) -> None:
    marker = Path(args.marker)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.is_symlink():
        raise RuntimeError("completion marker is a symlink")
    content = json.dumps(expected_state(args, include_runtime=True), indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=marker.name + ".", dir=marker.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
        os.chmod(marker, 0o600)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("command", choices=("check", "verify-and-write"))
    result.add_argument("--marker", required=True)
    result.add_argument("--binary", required=True)
    result.add_argument("--install-dir", required=True)
    result.add_argument("--hermes-home", required=True)
    result.add_argument("--patch", required=True)
    result.add_argument("--pin", required=True)
    result.add_argument("--tag", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--voice-set", default="")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "check":
        return 0 if marker_is_current(args) else 1
    try:
        write_marker(args)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"environment verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"environment verified; completion marker written: {args.marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
