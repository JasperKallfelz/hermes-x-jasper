#!/usr/bin/env python3
"""Install the public server snapshot without importing an operator's state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
PLUGINS = ("hermes-lcm", "hermes-context-inbox", "process-observatory", "hermes-auto-titler")


def verify_snapshot() -> dict:
    lock = json.loads((SERVER / "source-lock.json").read_text())
    records = json.loads((SERVER / "checksums.json").read_text())
    expected = {item["path"]: item["sha256"] for item in records}
    if len(expected) != len(records):
        raise ValueError("Duplicate snapshot checksum path")
    actual = {}
    for path in SERVER.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Snapshot contains a symlink: {path.relative_to(SERVER)}")
        if not path.is_file() or path.name == "checksums.json":
            continue
        if (any(part in {"__pycache__", ".pytest_cache"} or part.endswith(".egg-info") for part in path.parts)
                or path.suffix == ".pyc"):
            continue
        actual[path.relative_to(SERVER).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("Snapshot integrity mismatch; use a clean checkout or review and regenerate checksums.")
    if len(lock["upstream_commit"]) != 40 or any(c not in "0123456789abcdef" for c in lock["upstream_commit"]):
        raise ValueError("Invalid upstream commit in source lock")
    return lock


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(mode)


def launcher(root: Path, command: list[str]) -> str:
    home = root / "home"
    exports = {
        "HERMES_HOME": str(home),
        "HERMES_CONTEXT_INBOX_DB": str(home / "second-brain/context-inbox.sqlite3"),
        "HERMES_CONTEXT_INBOX_SPOOL": str(home / "second-brain/context-inbox-spool.jsonl"),
        "HERMES_PROCESS_OBSERVATORY_SPOOL": str(home / "second-brain/process-observatory-spool.d"),
    }
    return "#!/usr/bin/env bash\nset -euo pipefail\n" + "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in exports.items()
    ) + "exec " + shlex.join(command) + ' "$@"\n'


def configuration(selected: list[str]) -> str:
    config = (SERVER / "config.example.yaml").read_text()
    if "hermes-lcm" in selected:
        config = config.replace("engine: compressor", "engine: lcm")
    # Native discovery is opt-in: copying a plugin alone does not enable it.
    return config + "\nplugins:\n  enabled: " + json.dumps(selected) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "hermes-server")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan, write nothing")
    parser.add_argument("--source-only", action="store_true", help="prepare and verify sources; skip Python installation")
    parser.add_argument("--with-lcm", action="store_true", help="activate the bundled LCM context engine")
    parser.add_argument("--with-context-inbox", action="store_true", help="activate local conversation intake")
    parser.add_argument("--with-observatory", action="store_true", help="activate metadata-only process observation")
    parser.add_argument("--with-auto-titler", action="store_true", help="activate optional third-party automatic session titles")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required; Python 3.11 is the tested version.")
    root = args.root.expanduser().absolute()
    if root.exists() or root.is_symlink():
        parser.error("--root already exists. Choose a fresh directory; existing installs are never overwritten.")
    for parent in root.parents:
        if parent.is_symlink():
            parser.error("--root must not have a symlink ancestor")
    if shutil.which("git") is None:
        parser.error("git is required")
    lock = verify_snapshot()
    selected = [name for name, enabled in zip(PLUGINS, (
        args.with_lcm, args.with_context_inbox, args.with_observatory, args.with_auto_titler
    )) if enabled]
    print(f"Hermes server snapshot {lock['snapshot_date']} · upstream {lock['upstream_commit']}")
    print(f"Fresh destination: {root}\nPrivate state: {root / 'home'}")
    print("Active optional plugins: " + (", ".join(selected) or "none"))
    print("Copies runtime fixes, Second Brain, skills, voice helpers and coding wrappers.")
    print("No accounts, private memories, services, schedules or external connections are imported.")
    if args.dry_run:
        print("Dry run: no files changed, no network requests, no packages installed.")
        return 0
    os.umask(0o077)
    root.mkdir(parents=True, mode=0o700)
    core = root / "core"
    subprocess.run(["git", "init", "--quiet", str(core)], check=True)
    subprocess.run(["git", "-C", str(core), "remote", "add", "origin", lock["upstream_repository"]], check=True)
    subprocess.run(["git", "-C", str(core), "fetch", "--quiet", "--depth=1", "origin", lock["upstream_commit"]], check=True)
    subprocess.run(["git", "-C", str(core), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True)
    subprocess.run(["git", "-C", str(core), "apply", "--check", "--whitespace=error-all", str(SERVER / "runtime.patch")], check=True)
    subprocess.run(["git", "-C", str(core), "apply", str(SERVER / "runtime.patch")], check=True)
    bundle = root / "bundle"
    shutil.copytree(SERVER, bundle, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.egg-info"))
    home = root / "home"
    write_private(home / ".env", (REPO / ".env.example").read_text())
    write_private(home / "config.yaml", configuration(selected))
    shutil.copytree(bundle / "skills", home / "skills")
    for name in selected:
        shutil.copytree(bundle / "plugins" / name, home / "plugins" / name)
    manifest = {
        "state_db": str(home / "second-brain/state.sqlite3"),
        "context_inbox_db": str(home / "second-brain/context-inbox.sqlite3"),
        "ov_binary": "ov", "sources": [], "dreaming": {"enabled": False},
    }
    write_private(home / "second-brain/manifest.json", json.dumps(manifest, indent=2) + "\n")
    for name in ("hermes-coder", "hermes-coder-flow"):
        write_private(root / "bin" / name, (REPO / "coder-stack/bin" / name).read_text(), 0o700)
    python = core / "venv/bin/python"
    write_private(root / "bin/hermes", launcher(root, [str(core / "venv/bin/hermes")]), 0o700)
    write_private(root / "bin/hermes-second-brain", launcher(root, [str(python), "-m", "hermes_second_brain"]), 0o700)
    write_private(root / "INSTALL.json", json.dumps({**lock, "plugins": selected, "source_only": args.source_only}, indent=2) + "\n")
    if args.source_only:
        print("Sources prepared; launchers need a Python environment before use. See server/README.md.")
        return 0
    subprocess.run([sys.executable, "-m", "venv", str(core / "venv")], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-e", str(core) + "[messaging]", "-e", str(bundle / "second-brain")], check=True)
    subprocess.run([str(root / "bin/hermes"), "--help"], check=True, stdout=subprocess.DEVNULL)
    print(f"Installed. Configure your own provider: {shlex.quote(str(root / 'bin/hermes'))} setup")
    print(f"Start: {shlex.quote(str(root / 'bin/hermes'))}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}. The partial destination is kept for inspection.", file=sys.stderr)
        raise SystemExit(1)
