#!/usr/bin/env python3
"""Crash-safe CLI bridge for explicit, named Deep Chat sessions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from secure_runtime import (
    EXIT_HARNESS,
    EXIT_TIMEOUT,
    EXIT_UNAVAILABLE,
    SecurityError,
    SignalState,
    atomic_write_json,
    canonical_roots,
    descriptor_lock,
    ensure_private_directory,
    lock_is_held,
    minimal_environment,
    read_secure_json,
    resolve_executable,
    run_bounded,
    validate_absolute_override,
)


ROLE_MODELS = {"deep": "claude-opus-5", "marathon": "claude-fable-5"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STATE_LIMIT = 4 * 1024 * 1024
REGISTRY_LIMIT = 4 * 1024 * 1024
DEFAULT_TIMEOUT = 3600
AUTH_TIMEOUT = 5
GIT_TIMEOUT = 120
GIT_BASE_CONFIG = (
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "credential.helper=",
    "-c",
    "diff.external=",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(reason_id: str, message: str, exit_code: int = 1) -> None:
    print(
        "hermes-deep-chat: {} ({})".format(message, reason_id),
        file=sys.stderr,
    )
    raise SystemExit(exit_code)


def validate_name(name: str) -> None:
    if NAME_RE.fullmatch(name) is None:
        fail("chat_name_invalid", "chat name must use lowercase letters, digits, and dashes", 64)


def message_text(words: Sequence[str], label: str) -> str:
    values = list(words)
    if values and values[0] == "--":
        values = values[1:]
    value = " ".join(values)
    if not value.strip():
        fail("message_empty", "{} requires a non-empty message after --".format(label), 64)
    return value


class Context:
    def __init__(self, args: argparse.Namespace, signals: SignalState) -> None:
        self.args = args
        self.signals = signals
        self.started = time.monotonic()
        timeout = float(getattr(args, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        self.deadline = self.started + timeout
        self.home, self.hermes = canonical_roots(os.environ)
        self.state_dir = self.hermes / "state" / "deep-chat"
        self.worktrees_dir = self.hermes / "worktrees"
        self.lock_dir = self.hermes / "locks" / "deep-chat"
        self.registry = validate_absolute_override(
            os.environ.get("HERMES_DEEP_CHAT_REGISTRY"),
            self.hermes / "claude-sessions.json",
            "registry",
        )
        default_worker = Path(__file__).resolve().parent / "claude_worker.py"
        self.worker_script = validate_absolute_override(
            os.environ.get("HERMES_DEEP_CHAT_WORKER"),
            default_worker,
            "worker",
        )
        self.wrapper = validate_absolute_override(
            os.environ.get("HERMES_DEEP_CHAT_CLAUDE"),
            self.home / ".local" / "bin" / "claude-subscription",
            "claude_wrapper",
        )
        python_value = os.environ.get("HERMES_DEEP_CHAT_PYTHON", sys.executable)
        if not os.path.isabs(python_value):
            fail("python_path_unsafe", "Python override must be absolute", 64)
        self.python = resolve_executable(python_value)
        self.git = resolve_executable("git", path_env=os.environ.get("PATH"))
        self.permission_mode = os.environ.get("CLAUDE_WORKER_PERMISSION_MODE", "default")
        self.allow_bypass = os.environ.get("HERMES_DEEP_CHAT_ALLOW_BYPASS") == "1"
        if self.permission_mode == "bypassPermissions" and not self.allow_bypass:
            fail(
                "permission_bypass_not_acknowledged",
                "bypassPermissions requires HERMES_DEEP_CHAT_ALLOW_BYPASS=1",
                64,
            )
        self._filter_cache = {}  # type: Dict[Tuple[Any, ...], Tuple[str, ...]]

    def state_file(self, name: str) -> Path:
        return self.state_dir / (name + ".json")

    def state_lock(self, name: str) -> Path:
        return self.lock_dir / (name + ".lock")

    def git_environment(self) -> Dict[str, str]:
        return minimal_environment(
            home=self.home,
            additions={
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
        )

    def _git_config_key(self, cwd: Path) -> Tuple[Any, ...]:
        marker = cwd / ".git"
        try:
            if marker.is_dir():
                common = marker
            elif marker.is_file():
                text = marker.read_text(encoding="utf-8")
                if not text.startswith("gitdir: "):
                    return ("cwd", str(cwd))
                gitdir = Path(text[8:].strip())
                if not gitdir.is_absolute():
                    gitdir = marker.parent / gitdir
                gitdir = gitdir.resolve(strict=True)
                commondir = gitdir / "commondir"
                common = (
                    (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve(strict=True)
                    if commondir.is_file()
                    else gitdir
                )
            else:
                return ("cwd", str(cwd.resolve(strict=False)))
            info = (common / "config").stat()
            return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        except (OSError, RuntimeError, UnicodeError):
            return ("cwd", str(cwd))

    def _filter_config(self, cwd: Path) -> Tuple[str, ...]:
        key = self._git_config_key(cwd)
        cached = self._filter_cache.get(key)
        if cached is not None:
            return cached
        code, output, _error = self.git_run(
            ["config", "--local", "--name-only", "-z", "--get-regexp", r"^filter\."],
            cwd,
            discover_filters=False,
            timeout=min(30.0, max(0.1, self.deadline - time.monotonic())),
        )
        if code not in (0, 1):
            output = b""
        drivers = set()
        for raw in output.split(b"\0"):
            if not raw:
                continue
            try:
                name = raw.decode("utf-8")
            except UnicodeError:
                fail("git_filter_config_unsafe", "Git filter config is not UTF-8", EXIT_HARNESS)
            match = re.fullmatch(
                r"filter\.([A-Za-z0-9_.-]{1,128})\.(?:clean|smudge|process|required)",
                name,
            )
            if match is None:
                fail("git_filter_config_unsafe", "Git filter config name is unsafe", EXIT_HARNESS)
            drivers.add(match.group(1))
        values: List[str] = []
        for driver in sorted(drivers):
            values += [
                "-c",
                "filter.{}.process=".format(driver),
                "-c",
                "filter.{}.clean=".format(driver),
                "-c",
                "filter.{}.smudge=".format(driver),
                "-c",
                "filter.{}.required=false".format(driver),
            ]
        cached = tuple(values)
        if len(self._filter_cache) > 64:
            self._filter_cache.clear()
        self._filter_cache[key] = cached
        return cached

    def git_run(
        self,
        arguments: Sequence[str],
        cwd: Path,
        *,
        discover_filters: bool = True,
        timeout: float = GIT_TIMEOUT,
    ) -> Tuple[int, bytes, bytes]:
        if not arguments or arguments[0].startswith("-"):
            fail("git_command_unsafe", "Git command surface is unsafe", EXIT_HARNESS)
        deadline = min(self.deadline, time.monotonic() + timeout)
        filters = self._filter_config(cwd) if discover_filters and arguments[0] != "config" else ()
        result = run_bounded(
            [str(self.git), *GIT_BASE_CONFIG, *filters, *arguments],
            cwd=cwd,
            environment=self.git_environment(),
            deadline=deadline,
            capture_limit=16 * 1024 * 1024,
            signal_state=self.signals,
        )
        if not result.cleanup_verified:
            fail("git_cleanup_failed", "Git process-tree cleanup could not be verified", EXIT_HARNESS)
        if result.interrupted:
            fail("git_interrupted", "Git operation was interrupted", result.returncode)
        if result.timed_out:
            code = EXIT_TIMEOUT if time.monotonic() >= self.deadline else EXIT_HARNESS
            fail("operation_timeout" if code == EXIT_TIMEOUT else "git_timeout", "Git operation timed out", code)
        if not result.output_complete:
            fail("git_output_incomplete", "Git output exceeded its bound", EXIT_HARNESS)
        return result.returncode, result.stdout, result.stderr

    def worker_environment(self) -> Dict[str, str]:
        additions = {"HERMES_HOME": str(self.hermes)}
        return minimal_environment(home=self.home, additions=additions)

    def worker_command(self, operation: str, values: Sequence[str]) -> List[str]:
        resolve_executable(str(self.worker_script))
        command = [
            str(self.python),
            "-B",
            str(self.worker_script),
            "--registry",
            str(self.registry),
            "--wrapper",
            str(self.wrapper),
            "--permission-mode",
            self.permission_mode,
        ]
        if self.allow_bypass:
            command.append("--allow-bypass")
        return [*command, operation, *values]

    def run_worker(self, operation: str, values: Sequence[str], cwd: Path) -> Tuple[Any, int, bool]:
        result = run_bounded(
            self.worker_command(operation, values),
            cwd=cwd,
            environment=self.worker_environment(),
            deadline=self.deadline,
            capture_limit=4 * 1024 * 1024,
            signal_state=self.signals,
        )
        if not result.cleanup_verified:
            return {
                "ok": False,
                "reason_id": "worker_cleanup_failed",
                "launched": True,
                "acceptance_unknown": True,
            }, EXIT_HARNESS, True
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            payload = None
        if result.timed_out:
            payload = {
                "ok": False,
                "reason_id": "worker_timeout",
                "launched": result.launched,
                "acceptance_unknown": result.launched,
            }
        elif result.interrupted:
            payload = {
                "ok": False,
                "reason_id": "worker_interrupted",
                "launched": result.launched,
                "acceptance_unknown": result.launched,
                "signal": result.signal_number,
            }
        elif not result.output_complete:
            payload = {
                "ok": False,
                "reason_id": "worker_output_incomplete",
                "launched": result.launched,
                "acceptance_unknown": result.launched,
            }
        unknown = True
        if isinstance(payload, dict) and payload.get("ok") is False:
            unknown = bool(payload.get("acceptance_unknown"))
        if result.timed_out or result.interrupted or not result.output_complete:
            unknown = True
        if result.returncode == 0 and isinstance(payload, dict) and payload.get("ok") is True:
            return payload, 0, False
        return payload, result.returncode or 3, unknown


def load_state(ctx: Context, name: str) -> Dict[str, Any]:
    path = ctx.state_file(name)
    if not path.exists():
        fail("chat_unknown", "unknown chat '{}'".format(name))
    value = read_secure_json(path, STATE_LIMIT)
    if not state_valid(ctx, name, value):
        fail("state_invalid", "chat state is structurally or physically unsafe", EXIT_HARNESS)
    return value


def state_valid(ctx: Context, name: str, value: Any) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") not in (1, 2):
        return False
    worker = "deep-chat-{}".format(name)
    worktree = str(ctx.worktrees_dir / worker)
    branch = "hermes/deep-chat/{}".format(name)
    if (
        value.get("name") != name
        or value.get("worker") != worker
        or value.get("worktree") != worktree
        or value.get("branch") != branch
        or value.get("role") not in ROLE_MODELS
        or value.get("model") not in (None, ROLE_MODELS[value.get("role")])
        or not isinstance(value.get("source_repo"), str)
        or not os.path.isabs(value["source_repo"])
        or value.get("status")
        not in (
            "provisional",
            "starting",
            "active",
            "reconciliation_required",
            "failed_reconciled",
            "closed",
        )
    ):
        return False
    try:
        if Path(value["source_repo"]).exists() and str(Path(value["source_repo"]).resolve()) != value["source_repo"]:
            return False
        if Path(worktree).exists() and str(Path(worktree).resolve()) != worktree:
            return False
    except (OSError, RuntimeError):
        return False
    if value["schema_version"] == 2:
        turns = value.get("turns")
        counter = value.get("bridge_turn_counter")
        if not isinstance(turns, list) or not isinstance(counter, int) or counter != len(turns):
            return False
        for index, turn in enumerate(turns, 1):
            if (
                not isinstance(turn, dict)
                or turn.get("bridge_turn_id") != index
                or turn.get("operation") not in ("start", "send")
                or turn.get("status")
                not in (
                    "attempting",
                    "succeeded",
                    "acceptance_unknown",
                    "reconciled_not_accepted",
                )
            ):
                return False
    return True


def save_state(ctx: Context, value: Dict[str, Any]) -> None:
    atomic_write_json(ctx.state_file(value["name"]), value)


def registry_entry(ctx: Context, worker: str) -> Optional[Dict[str, Any]]:
    if not ctx.registry.exists():
        return None
    document = read_secure_json(ctx.registry, REGISTRY_LIMIT)
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 2
        or not isinstance(document.get("workers"), dict)
    ):
        fail("registry_invalid", "worker registry is invalid", EXIT_HARNESS)
    entry = document["workers"].get(worker)
    return dict(entry) if isinstance(entry, dict) else None


def binding_matches(state: Mapping[str, Any], entry: Optional[Mapping[str, Any]]) -> bool:
    return bool(
        entry
        and entry.get("schema_version") == 2
        and entry.get("worker") == state.get("worker")
        and entry.get("cwd") == state.get("worktree")
        and entry.get("role") == state.get("role")
        and entry.get("model") == ROLE_MODELS.get(state.get("role"))
        and entry.get("session_id") == state.get("worker_session_id")
    )


def authenticate(ctx: Context) -> None:
    try:
        executable = resolve_executable(str(ctx.wrapper))
    except SecurityError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason_id": "claude_auth_wrapper_missing",
                    "action": "Install and authenticate the Claude subscription wrapper manually, then retry.",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(EXIT_UNAVAILABLE)
    result = run_bounded(
        [str(executable), "auth", "status"],
        cwd=ctx.home,
        environment=minimal_environment(home=ctx.home),
        deadline=min(ctx.deadline, time.monotonic() + AUTH_TIMEOUT),
        capture_limit=64 * 1024,
        signal_state=ctx.signals,
    )
    if (
        result.returncode != 0
        or result.timed_out
        or result.interrupted
        or not result.cleanup_verified
    ):
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason_id": "claude_auth_unavailable",
                    "action": "Authenticate the Claude subscription wrapper manually, then retry.",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(EXIT_UNAVAILABLE)


def repository_root(ctx: Context, value: str) -> Path:
    if not value:
        fail("repo_missing", "repository path is required", 64)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        fail("repo_missing", "repository path is unavailable", 64)
    code, output, _ = ctx.git_run(["rev-parse", "--show-toplevel"], candidate)
    if code != 0:
        fail("not_a_repository", "path is not a Git repository", 64)
    try:
        root = Path(output.decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeError, OSError, RuntimeError):
        fail("repo_path_unsafe", "Git returned an unsafe repository path", EXIT_HARNESS)
    return root


def worktree_observation(ctx: Context, state: Mapping[str, Any]) -> Dict[str, Any]:
    repo = Path(state["source_repo"])
    worktree = Path(state["worktree"])
    exists = worktree.is_dir()
    registered = False
    branch = None
    clean = None
    if repo.is_dir():
        code, output, _ = ctx.git_run(["worktree", "list", "--porcelain"], repo)
        if code == 0:
            for line in output.decode("utf-8", "replace").splitlines():
                if line.startswith("worktree "):
                    try:
                        registered_path = Path(line[9:]).resolve(strict=True)
                    except (OSError, RuntimeError):
                        continue
                    if exists and registered_path == worktree.resolve(strict=True):
                        registered = True
    if exists:
        code, output, _ = ctx.git_run(["symbolic-ref", "--short", "HEAD"], worktree)
        if code == 0:
            branch = output.decode("utf-8", "replace").strip()
        code, output, _ = ctx.git_run(
            ["status", "--porcelain=v1", "--untracked-files=all"], worktree
        )
        if code == 0:
            clean = not bool(output)
    return {
        "exists": exists,
        "registered": registered,
        "branch": branch,
        "clean": clean,
    }


def turn_record(identifier: int, operation: str) -> Dict[str, Any]:
    return {
        "bridge_turn_id": identifier,
        "operation": operation,
        "attempted_at": now(),
        "succeeded_at": None,
        "status": "attempting",
        "worker_session_id": None,
    }


def settle_unknown(state: Dict[str, Any], reason: str) -> None:
    state["status"] = "reconciliation_required"
    state["reconciliation_required"] = True
    state["acceptance_unknown"] = True
    state["uncertainty_reason_id"] = reason
    state["updated_at"] = now()
    if state.get("schema_version") == 2 and state.get("turns"):
        state["turns"][-1]["status"] = "acceptance_unknown"
        state["turns"][-1]["succeeded_at"] = None
        state["turns"][-1]["worker_session_id"] = None


def cmd_start(args: argparse.Namespace, signals: SignalState) -> None:
    validate_name(args.name)
    task = message_text(args.message, "start")
    ctx = Context(args, signals)
    repo = repository_root(ctx, args.repo)
    role = args.role
    model = ROLE_MODELS[role]
    worker = "deep-chat-{}".format(args.name)
    worktree = ctx.worktrees_dir / worker
    branch = "hermes/deep-chat/{}".format(args.name)
    state_path = ctx.state_file(args.name)
    code, status, _ = ctx.git_run(
        ["status", "--porcelain=v1", "--untracked-files=all"], repo
    )
    if code != 0 or status:
        fail("source_dirty", "source repository must be clean", 64)
    code, _, _ = ctx.git_run(["check-ref-format", "--branch", branch], repo)
    if code != 0:
        fail("branch_invalid", "derived branch name is invalid", 64)
    code, _, _ = ctx.git_run(["show-ref", "--verify", "--quiet", "refs/heads/" + branch], repo)
    if code == 0:
        fail("branch_exists", "chat branch already exists")
    if state_path.exists() or worktree.exists():
        fail("chat_exists", "chat state or worktree already exists")
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "schema_version": args.schema_version,
                    "name": args.name,
                    "worker": worker,
                    "role": role,
                    "model": model,
                    "source_repo": str(repo),
                    "worktree": str(worktree),
                    "branch": branch,
                    "task": task,
                },
                sort_keys=True,
            )
        )
        return
    authenticate(ctx)
    ensure_private_directory(ctx.state_dir)
    ensure_private_directory(ctx.lock_dir)
    with descriptor_lock(ctx.state_lock(args.name), ctx.deadline):
        if state_path.exists() or worktree.exists():
            fail("chat_exists", "chat state or worktree already exists")
        timestamp = now()
        state: Dict[str, Any] = {
            "schema_version": args.schema_version,
            "status": "provisional",
            "name": args.name,
            "worker": worker,
            "role": role,
            "model": model,
            "source_repo": str(repo),
            "worktree": str(worktree),
            "branch": branch,
            "brief_file": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            "start_phase": "provisional_persisted",
            "branch_phase": "planned",
            "worktree_phase": "planned",
            "worker_session_id": None,
            "reconciliation_required": False,
            "acceptance_unknown": False,
        }
        if args.schema_version == 2:
            state["bridge_turn_counter"] = 1
            state["turns"] = [turn_record(1, "start")]
        save_state(ctx, state)  # Before the first Git mutation.
        ensure_private_directory(ctx.worktrees_dir)
        code, _output, error = ctx.git_run(
            ["worktree", "add", "-b", branch, str(worktree), "HEAD"], repo
        )
        if code != 0:
            state["status"] = "provisional"
            state["branch_phase"] = "unknown"
            state["worktree_phase"] = "unknown"
            state["start_phase"] = "git_mutation_failed"
            state["git_error"] = "worktree add returned {}".format(code)
            state["updated_at"] = now()
            save_state(ctx, state)
            fail("worktree_add_failed", "Git worktree creation failed; provisional state is preserved", EXIT_HARNESS)
        state["status"] = "starting"
        state["branch_phase"] = "created"
        state["worktree_phase"] = "created"
        state["start_phase"] = "worker_launch_pending"
        state["updated_at"] = now()
        save_state(ctx, state)
        prompt = (
            "You are the persistent Claude worker '{}'. Work only in the designated "
            "worktree {} on branch {}. This is a policy boundary, not an OS sandbox; "
            "do not access or modify paths outside it. Never commit, push, merge, rebase, "
            "deploy, or remove worktrees. Initial task: {}"
        ).format(worker, worktree, branch, task)
        payload, worker_rc, unknown = ctx.run_worker(
            "new",
            [
                worker,
                role,
                "--cwd",
                str(worktree),
                "--deadline-monotonic",
                repr(ctx.deadline),
                prompt,
            ],
            worktree,
        )
        if worker_rc != 0 or not isinstance(payload, dict):
            reason = payload.get("reason_id", "worker_response_invalid") if isinstance(payload, dict) else "worker_response_invalid"
            if unknown:
                settle_unknown(state, reason)
            else:
                state["status"] = "failed_reconciled"
                state["updated_at"] = now()
            save_state(ctx, state)
            fail(reason, "worker launch did not produce a certain successful turn", worker_rc or 3)
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            settle_unknown(state, "worker_session_id_missing")
            save_state(ctx, state)
            fail("worker_session_id_missing", "worker returned no session binding", 3)
        state["worker_session_id"] = session_id
        entry = registry_entry(ctx, worker)
        if not binding_matches(state, entry):
            settle_unknown(state, "registry_binding_mismatch")
            save_state(ctx, state)
            fail("registry_binding_mismatch", "worker registry did not match successful start", EXIT_HARNESS)
        state["status"] = "active"
        state["start_phase"] = "complete"
        state["updated_at"] = now()
        if args.schema_version == 2:
            state["turns"][-1].update(
                {
                    "status": "succeeded",
                    "succeeded_at": now(),
                    "worker_session_id": session_id,
                }
            )
        save_state(ctx, state)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def cmd_send(args: argparse.Namespace, signals: SignalState) -> None:
    validate_name(args.name)
    task = message_text(args.message, "send")
    ctx = Context(args, signals)
    ensure_private_directory(ctx.lock_dir)
    with descriptor_lock(ctx.state_lock(args.name), ctx.deadline):
        state = load_state(ctx, args.name)
        if state["status"] != "active" or state.get("reconciliation_required"):
            fail("chat_not_active", "chat is not active or requires reconciliation")
        observation = worktree_observation(ctx, state)
        if (
            not observation["exists"]
            or not observation["registered"]
            or observation["branch"] != state["branch"]
        ):
            fail("worktree_binding_mismatch", "recorded worktree binding is invalid", EXIT_HARNESS)
        entry = registry_entry(ctx, state["worker"])
        if not binding_matches(state, entry):
            fail("registry_binding_mismatch", "worker registry binding mismatch", EXIT_HARNESS)
        if state["schema_version"] == 2:
            state["bridge_turn_counter"] += 1
            state["turns"].append(
                turn_record(state["bridge_turn_counter"], "send")
            )
        state["updated_at"] = now()
        save_state(ctx, state)
        message = (
            "Trusted follow-up from the user. Continue only in worktree {} under the "
            "existing policy boundary. Message: {}"
        ).format(state["worktree"], task)
        payload, worker_rc, unknown = ctx.run_worker(
            "continue",
            [
                state["worker"],
                "--deadline-monotonic",
                repr(ctx.deadline),
                "--expected-cwd",
                state["worktree"],
                "--expected-role",
                state["role"],
                "--expected-model",
                ROLE_MODELS[state["role"]],
                "--expected-session-id",
                state["worker_session_id"],
                message,
            ],
            Path(state["worktree"]),
        )
        if worker_rc != 0 or not isinstance(payload, dict):
            reason = payload.get("reason_id", "worker_response_invalid") if isinstance(payload, dict) else "worker_response_invalid"
            if unknown:
                settle_unknown(state, reason)
            elif state["schema_version"] == 2:
                state["turns"][-1]["status"] = "reconciled_not_accepted"
            state["updated_at"] = now()
            save_state(ctx, state)
            fail(reason, "continuation did not produce a certain successful turn", worker_rc or 3)
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            settle_unknown(state, "worker_session_id_missing")
            save_state(ctx, state)
            fail("worker_session_id_missing", "worker returned no session binding", 3)
        state["worker_session_id"] = session_id
        entry = registry_entry(ctx, state["worker"])
        if not binding_matches(state, entry):
            settle_unknown(state, "registry_binding_mismatch")
            save_state(ctx, state)
            fail("registry_binding_mismatch", "registry update did not match the turn", EXIT_HARNESS)
        state["updated_at"] = now()
        if state["schema_version"] == 2:
            state["turns"][-1].update(
                {
                    "status": "succeeded",
                    "succeeded_at": now(),
                    "worker_session_id": session_id,
                }
            )
        save_state(ctx, state)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def cmd_status(args: argparse.Namespace, signals: SignalState) -> None:
    validate_name(args.name)
    ctx = Context(args, signals)
    state = load_state(ctx, args.name)
    observed = {
        "state_valid": True,
        "lock_held": lock_is_held(ctx.state_lock(args.name)),
        "worktree": worktree_observation(ctx, state),
        "registry": {
            "entry_exists": registry_entry(ctx, state["worker"]) is not None,
            "entry_matches": binding_matches(
                state, registry_entry(ctx, state["worker"])
            ),
        },
    }
    print(json.dumps({**state, "observed": observed}, ensure_ascii=False, sort_keys=True))


def cmd_reconcile(args: argparse.Namespace, signals: SignalState) -> None:
    validate_name(args.name)
    ctx = Context(args, signals)
    if args.resolve_unknown is None:
        state = load_state(ctx, args.name)
        observation = worktree_observation(ctx, state)
        if state.get("reconciliation_required"):
            classification = "acceptance_unknown"
        elif not observation["exists"]:
            classification = "worktree_missing"
        elif not observation["registered"]:
            classification = "worktree_unregistered"
        elif observation["branch"] != state["branch"]:
            classification = "worktree_branch_mismatch"
        elif not binding_matches(state, registry_entry(ctx, state["worker"])):
            classification = "worker_registry_mismatch"
        elif state["status"] == "closed":
            classification = "closed_consistent"
        else:
            classification = "consistent"
        print(
            json.dumps(
                {
                    "name": args.name,
                    "classification": classification,
                    "requires_human_instruction": classification != "consistent",
                    "observed": observation,
                },
                sort_keys=True,
            )
        )
        return
    ensure_private_directory(ctx.lock_dir)
    with descriptor_lock(ctx.state_lock(args.name), ctx.deadline):
        state = load_state(ctx, args.name)
        if not state.get("reconciliation_required"):
            fail("reconciliation_not_required", "chat has no uncertain turn")
        if args.resolve_unknown != "not-accepted":
            fail("reconciliation_choice_invalid", "only explicit not-accepted resolution is supported", 64)
        entry = registry_entry(ctx, state["worker"])
        if state.get("worker_session_id") and not binding_matches(state, entry):
            fail("reconciliation_binding_mismatch", "registry changed; cannot mark turn not accepted", EXIT_HARNESS)
        state["reconciliation_required"] = False
        state["acceptance_unknown"] = False
        state.pop("uncertainty_reason_id", None)
        state["updated_at"] = now()
        if state["schema_version"] == 2 and state["turns"]:
            state["turns"][-1]["status"] = "reconciled_not_accepted"
        state["status"] = "active" if state.get("worker_session_id") else "failed_reconciled"
        save_state(ctx, state)
        print(json.dumps({"ok": True, "status": state["status"]}, sort_keys=True))


def cmd_close(args: argparse.Namespace, signals: SignalState) -> None:
    validate_name(args.name)
    ctx = Context(args, signals)
    ensure_private_directory(ctx.lock_dir)
    with descriptor_lock(ctx.state_lock(args.name), ctx.deadline):
        state = load_state(ctx, args.name)
        if state.get("reconciliation_required"):
            fail("reconciliation_required", "uncertain chat cannot be closed before explicit reconciliation")
        state["status"] = "closed"
        state["updated_at"] = now()
        save_state(ctx, state)


def cmd_list(args: argparse.Namespace, signals: SignalState) -> None:
    ctx = Context(args, signals)
    if not ctx.state_dir.exists():
        print("[]")
        return
    values = []
    for path in sorted(ctx.state_dir.iterdir()):
        if path.suffix != ".json":
            continue
        try:
            value = read_secure_json(path, STATE_LIMIT)
        except SecurityError:
            continue
        values.append(value)
    print(json.dumps(values, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-deep-chat")
    subcommands = parser.add_subparsers(dest="command", required=True)
    start = subcommands.add_parser("start")
    start.add_argument("repo")
    start.add_argument("name")
    start.add_argument("--role", choices=sorted(ROLE_MODELS), default="deep")
    start.add_argument("--schema-version", type=int, choices=(1, 2), default=2)
    start.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    start.add_argument("--dry-run", action="store_true")
    # A real ``--`` delimiter keeps option parsing deterministic while still
    # allowing arbitrary message words after it.  REMAINDER would silently
    # swallow bridge options that appear after the chat name.
    start.add_argument("message", nargs="+")
    start.set_defaults(function=cmd_start)
    send = subcommands.add_parser("send")
    send.add_argument("name")
    send.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    send.add_argument("message", nargs="+")
    send.set_defaults(function=cmd_send)
    status = subcommands.add_parser("status")
    status.add_argument("name")
    status.add_argument("--timeout", type=int, default=120)
    status.set_defaults(function=cmd_status)
    reconcile = subcommands.add_parser("reconcile")
    reconcile.add_argument("name")
    reconcile.add_argument("--resolve-unknown", choices=("not-accepted",))
    reconcile.add_argument("--timeout", type=int, default=120)
    reconcile.set_defaults(function=cmd_reconcile)
    listing = subcommands.add_parser("list")
    listing.add_argument("--timeout", type=int, default=120)
    listing.set_defaults(function=cmd_list)
    close = subcommands.add_parser("close")
    close.add_argument("name")
    close.add_argument("--timeout", type=int, default=120)
    close.set_defaults(function=cmd_close)
    return parser


def main() -> None:
    if sys.version_info < (3, 11):
        fail("python_too_old", "Python 3.11 or newer is required", EXIT_UNAVAILABLE)
    args = build_parser().parse_args()
    if getattr(args, "timeout", 1) < 1:
        fail("timeout_invalid", "timeout must be positive", 64)
    signals = SignalState()
    signals.install()
    try:
        args.function(args, signals)
    except SecurityError as exc:
        fail(exc.reason_id, exc.message, exc.exit_code)
    finally:
        signals.restore()


if __name__ == "__main__":
    main()
