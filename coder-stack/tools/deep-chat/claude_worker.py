#!/usr/bin/env python3
"""Private, CAS-backed Claude session worker for explicit Deep Chat use."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

from secure_runtime import (
    EXIT_HARNESS,
    EXIT_TIMEOUT,
    SecurityError,
    SignalState,
    atomic_write_json,
    canonical_roots,
    descriptor_lock,
    ensure_private_directory,
    minimal_environment,
    read_secure_json,
    resolve_executable,
    run_bounded,
    validate_absolute_override,
)


ROLE_MODELS = {"deep": "claude-opus-5", "marathon": "claude-fable-5"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LIMIT_PATTERNS = (
    "usage limit",
    "rate limit",
    "limit reached",
    "extra usage",
    "out of extra usage",
    "upgrade to",
)
REGISTRY_LIMIT = 4 * 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit_error(
    code: int,
    reason_id: str,
    message: str,
    *,
    launched: bool = False,
    acceptance_unknown: bool = False,
    **extra: Any,
) -> None:
    print(
        json.dumps(
            {
                "ok": False,
                "reason_id": reason_id,
                "error": message,
                "launched": launched,
                "acceptance_unknown": acceptance_unknown,
                **extra,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    raise SystemExit(code)


def positive_timeout(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("timeout must be a positive integer")
    return parsed


def absolute_deadline(args: argparse.Namespace) -> float:
    if args.deadline_monotonic is not None:
        if not args.deadline_monotonic > time.monotonic():
            emit_error(EXIT_TIMEOUT, "operation_timeout", "operation deadline has expired")
        return args.deadline_monotonic
    return time.monotonic() + float(args.timeout or 3600)


def canonical_cwd(value: str) -> str:
    if not value or not os.path.isabs(value):
        emit_error(64, "cwd_path_unsafe", "worker cwd must be absolute")
    try:
        resolved = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        emit_error(2, "cwd_missing", "worker cwd is unavailable")
    if not resolved.is_dir():
        emit_error(2, "cwd_missing", "worker cwd is unavailable")
    return str(resolved)


def registry_document(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": 2, "revision": 0, "workers": {}}
    value = read_secure_json(path, REGISTRY_LIMIT)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or not isinstance(value.get("revision"), int)
        or value["revision"] < 0
        or not isinstance(value.get("workers"), dict)
    ):
        emit_error(2, "registry_invalid", "Claude worker registry schema is invalid")
    return value


def entry_valid(name: str, entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    role = entry.get("role")
    return (
        entry.get("schema_version") == 2
        and entry.get("worker") == name
        and role in ROLE_MODELS
        and entry.get("model") == ROLE_MODELS.get(role)
        and isinstance(entry.get("session_id"), str)
        and bool(entry.get("session_id"))
        and isinstance(entry.get("cwd"), str)
        and os.path.isabs(entry.get("cwd"))
        and isinstance(entry.get("revision"), int)
        and entry.get("revision") >= 1
    )


def paths(args: argparse.Namespace) -> Tuple[Path, Path, Path, Path]:
    home, hermes = canonical_roots(os.environ)
    ensure_private_directory(hermes)
    registry = validate_absolute_override(
        args.registry or os.environ.get("HERMES_DEEP_CHAT_REGISTRY"),
        hermes / "claude-sessions.json",
        "registry",
    )
    wrapper_value = args.wrapper or os.environ.get("HERMES_DEEP_CHAT_CLAUDE")
    wrapper = validate_absolute_override(
        wrapper_value,
        home / ".local" / "bin" / "claude-subscription",
        "claude_wrapper",
    )
    lock_root = registry.parent / (registry.name + ".locks")
    ensure_private_directory(registry.parent)
    ensure_private_directory(lock_root)
    return home, registry, wrapper, lock_root


def worker_lock_path(lock_root: Path, name: str) -> Path:
    digest = hashlib.sha256(name.encode("ascii")).hexdigest()
    return lock_root / (digest + ".lock")


def global_lock_path(registry: Path) -> Path:
    return registry.parent / (registry.name + ".global.lock")


def validate_binding(
    name: str,
    entry: Any,
    *,
    expected_cwd: Optional[str],
    expected_role: Optional[str],
    expected_model: Optional[str],
    expected_session_id: Optional[str],
) -> Dict[str, Any]:
    if not entry_valid(name, entry):
        emit_error(2, "worker_entry_invalid", "worker registry entry is invalid")
    assert isinstance(entry, dict)
    expected = {
        "cwd": expected_cwd,
        "role": expected_role,
        "model": expected_model,
        "session_id": expected_session_id,
    }
    for field, value in expected.items():
        if value is not None and entry.get(field) != value:
            emit_error(
                2,
                "worker_binding_mismatch",
                "worker registry {} does not match the bridge binding".format(field),
            )
    return dict(entry)


def permission_mode(args: argparse.Namespace) -> str:
    mode = args.permission_mode or os.environ.get("CLAUDE_WORKER_PERMISSION_MODE", "default")
    if mode == "bypassPermissions":
        allowed = args.allow_bypass or os.environ.get("HERMES_DEEP_CHAT_ALLOW_BYPASS") == "1"
        if not allowed:
            emit_error(
                64,
                "permission_bypass_not_acknowledged",
                "bypassPermissions requires the named HERMES_DEEP_CHAT_ALLOW_BYPASS=1 opt-in",
            )
    return mode


def run_claude(
    *,
    wrapper: Path,
    home: Path,
    cwd: str,
    arguments: list[str],
    task: str,
    mode: str,
    deadline: float,
    signals: SignalState,
) -> Dict[str, Any]:
    executable = resolve_executable(str(wrapper), path_env=os.environ.get("PATH"))
    command = [
        str(executable),
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        mode,
        *arguments,
        task,
    ]
    result = run_bounded(
        command,
        cwd=Path(cwd),
        environment=minimal_environment(home=home),
        deadline=deadline,
        signal_state=signals,
    )
    combined = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace")
    unknown = result.launched
    if not result.cleanup_verified:
        emit_error(
            EXIT_HARNESS,
            "worker_cleanup_failed",
            "Claude process-tree cleanup could not be verified",
            launched=result.launched,
            acceptance_unknown=unknown,
        )
    if result.interrupted:
        emit_error(
            result.returncode,
            "worker_interrupted",
            "Claude worker was interrupted after launch",
            launched=result.launched,
            acceptance_unknown=unknown,
            signal=result.signal_number,
        )
    if result.timed_out:
        emit_error(
            EXIT_TIMEOUT,
            "worker_timeout",
            "Claude worker timed out after launch",
            launched=result.launched,
            acceptance_unknown=unknown,
        )
    if result.returncode != 0:
        reason = (
            "subscription_limit"
            if any(pattern in combined.lower() for pattern in LIMIT_PATTERNS)
            else "claude_cli_failed"
        )
        emit_error(
            4 if reason == "subscription_limit" else 3,
            reason,
            "Claude CLI returned an error after launch",
            launched=result.launched,
            acceptance_unknown=unknown,
            exit_code=result.returncode,
        )
    if not result.output_complete:
        emit_error(
            3,
            "claude_output_incomplete",
            "Claude CLI output was incomplete after launch",
            launched=True,
            acceptance_unknown=True,
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        emit_error(
            3,
            "claude_output_invalid",
            "Claude CLI returned invalid JSON after launch",
            launched=True,
            acceptance_unknown=True,
        )
    if not isinstance(payload, dict) or payload.get("is_error"):
        emit_error(
            3,
            "claude_reported_error",
            "Claude reported an invalid or failed turn after launch",
            launched=True,
            acceptance_unknown=True,
        )
    payload["_duration_s"] = round(result.duration_seconds, 3)
    return payload


def report(name: str, entry: Dict[str, Any], payload: Dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "ok": True,
                "worker": name,
                "role": entry["role"],
                "model": entry["model"],
                "session_id": entry["session_id"],
                "duration_s": payload.get("_duration_s"),
                "num_turns": payload.get("num_turns"),
                "result": payload.get("result", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def cmd_new(args: argparse.Namespace, signals: SignalState) -> None:
    if NAME_RE.fullmatch(args.name) is None:
        emit_error(64, "worker_name_invalid", "worker name is invalid")
    deadline = absolute_deadline(args)
    home, registry, wrapper, lock_root = paths(args)
    cwd = canonical_cwd(args.cwd)
    model = ROLE_MODELS[args.role]
    mode = permission_mode(args)
    with descriptor_lock(worker_lock_path(lock_root, args.name), deadline):
        with descriptor_lock(global_lock_path(registry), deadline):
            document = registry_document(registry)
            if args.name in document["workers"]:
                emit_error(2, "worker_exists", "worker already exists")
        payload = run_claude(
            wrapper=wrapper,
            home=home,
            cwd=cwd,
            arguments=["--model", model],
            task=args.task,
            mode=mode,
            deadline=deadline,
            signals=signals,
        )
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            emit_error(
                3,
                "session_id_missing",
                "Claude returned no resumable session ID after launch",
                launched=True,
                acceptance_unknown=True,
            )
        timestamp = now()
        entry = {
            "schema_version": 2,
            "worker": args.name,
            "session_id": session_id,
            "role": args.role,
            "model": model,
            "cwd": cwd,
            "revision": 1,
            "created_at": timestamp,
            "last_used_at": timestamp,
        }
        with descriptor_lock(global_lock_path(registry), deadline):
            document = registry_document(registry)
            # Unrelated workers are allowed to commit while this model call is
            # in flight.  The long-held per-worker lock makes name absence the
            # relevant compare-and-swap predicate for creation.
            if args.name in document["workers"]:
                emit_error(
                    3,
                    "registry_cas_conflict",
                    "registry changed after Claude launch",
                    launched=True,
                    acceptance_unknown=True,
                )
            document["workers"][args.name] = entry
            document["revision"] += 1
            atomic_write_json(registry, document)
        report(args.name, entry, payload)


def cmd_continue(args: argparse.Namespace, signals: SignalState) -> None:
    if NAME_RE.fullmatch(args.name) is None:
        emit_error(64, "worker_name_invalid", "worker name is invalid")
    deadline = absolute_deadline(args)
    home, registry, wrapper, lock_root = paths(args)
    expected_cwd = canonical_cwd(args.expected_cwd) if args.expected_cwd else None
    mode = permission_mode(args)
    with descriptor_lock(worker_lock_path(lock_root, args.name), deadline):
        with descriptor_lock(global_lock_path(registry), deadline):
            document = registry_document(registry)
            entry = validate_binding(
                args.name,
                document["workers"].get(args.name),
                expected_cwd=expected_cwd,
                expected_role=args.expected_role,
                expected_model=args.expected_model,
                expected_session_id=args.expected_session_id,
            )
            entry_revision = entry["revision"]
        payload = run_claude(
            wrapper=wrapper,
            home=home,
            cwd=entry["cwd"],
            arguments=["--model", entry["model"], "--resume", entry["session_id"]],
            task=args.task,
            mode=mode,
            deadline=deadline,
            signals=signals,
        )
        new_session_id = payload.get("session_id")
        if not isinstance(new_session_id, str) or not new_session_id:
            emit_error(
                3,
                "session_id_missing",
                "Claude returned no resumable session ID after launch",
                launched=True,
                acceptance_unknown=True,
            )
        with descriptor_lock(global_lock_path(registry), deadline):
            document = registry_document(registry)
            current = document["workers"].get(args.name)
            if (
                not entry_valid(args.name, current)
                or current.get("revision") != entry_revision
                or current.get("session_id") != entry.get("session_id")
            ):
                emit_error(
                    3,
                    "registry_cas_conflict",
                    "worker binding changed after Claude launch",
                    launched=True,
                    acceptance_unknown=True,
                )
            updated = dict(current)
            updated["session_id"] = new_session_id
            updated["last_used_at"] = now()
            updated["revision"] += 1
            document["workers"][args.name] = updated
            document["revision"] += 1
            atomic_write_json(registry, document)
        report(args.name, updated, payload)


def cmd_list(args: argparse.Namespace, _signals: SignalState) -> None:
    deadline = absolute_deadline(args)
    _home, registry, _wrapper, _lock_root = paths(args)
    with descriptor_lock(global_lock_path(registry), deadline):
        print(json.dumps({"ok": True, **registry_document(registry)}, indent=2, sort_keys=True))


def cmd_forget(args: argparse.Namespace, _signals: SignalState) -> None:
    deadline = absolute_deadline(args)
    _home, registry, _wrapper, lock_root = paths(args)
    with descriptor_lock(worker_lock_path(lock_root, args.name), deadline):
        with descriptor_lock(global_lock_path(registry), deadline):
            document = registry_document(registry)
            removed = document["workers"].pop(args.name, None)
            if removed is None:
                emit_error(2, "worker_missing", "worker does not exist")
            document["revision"] += 1
            atomic_write_json(registry, document)
    print(json.dumps({"ok": True, "forgotten": args.name}, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="claude_worker")
    result.add_argument("--registry")
    result.add_argument("--wrapper")
    result.add_argument("--permission-mode")
    result.add_argument("--allow-bypass", action="store_true")
    subcommands = result.add_subparsers(dest="command", required=True)
    new = subcommands.add_parser("new")
    new.add_argument("name")
    new.add_argument("role", choices=sorted(ROLE_MODELS))
    new.add_argument("--cwd", required=True)
    new.add_argument("--timeout", type=positive_timeout, default=3600)
    new.add_argument("--deadline-monotonic", type=float)
    new.add_argument("task")
    new.set_defaults(function=cmd_new)
    continuation = subcommands.add_parser("continue")
    continuation.add_argument("name")
    continuation.add_argument("--timeout", type=positive_timeout, default=3600)
    continuation.add_argument("--deadline-monotonic", type=float)
    continuation.add_argument("--expected-cwd")
    continuation.add_argument("--expected-role", choices=sorted(ROLE_MODELS))
    continuation.add_argument("--expected-model")
    continuation.add_argument("--expected-session-id")
    continuation.add_argument("task")
    continuation.set_defaults(function=cmd_continue)
    listing = subcommands.add_parser("list")
    listing.add_argument("--timeout", type=positive_timeout, default=30)
    listing.add_argument("--deadline-monotonic", type=float)
    listing.set_defaults(function=cmd_list)
    forget = subcommands.add_parser("forget")
    forget.add_argument("name")
    forget.add_argument("--timeout", type=positive_timeout, default=30)
    forget.add_argument("--deadline-monotonic", type=float)
    forget.set_defaults(function=cmd_forget)
    return result


def main() -> None:
    args = parser().parse_args()
    signals = SignalState()
    signals.install()
    try:
        args.function(args, signals)
    except SecurityError as exc:
        emit_error(exc.exit_code, exc.reason_id, exc.message)
    finally:
        signals.restore()


if __name__ == "__main__":
    main()
