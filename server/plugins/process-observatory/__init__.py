"""Passive, metadata-only Process Observatory plugin for Hermes.

This is a *separate* Hermes plugin template, not a patch to Hermes core. It
observes the current Hermes lifecycle hooks and records a strictly bounded,
allowlisted metadata event per observation into a private spool directory. It
never stores tool arguments or results, prompts, model text, command strings,
message bodies, filesystem paths, credentials, or any raw external content:
every correlation identifier is one-way hashed and every classification is
projected onto a fixed allow-list before anything is written.

The hook path is fail-open and non-blocking: it projects an event and attempts a
non-blocking put into a bounded memory queue.  A daemon batch worker owns all
directory checks, atomic private file writes, fsyncs, and disk quotas.  Queue or
quota overflow drops telemetry and increments a counter; user-facing work is
never delayed for observability.  A separate importer (in
``hermes_second_brain.observatory``) drains the spool.

Stdlib-only and self-contained so it can be dropped into a Hermes ``plugins``
directory without depending on the Second Brain package at runtime.
"""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import os
import queue as queue_module
import stat
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1
DEFAULT_SPOOL = "~/.hermes/second-brain/process-observatory-spool.d"
DEFAULT_QUEUE_CAPACITY = 256
DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_FILES = 1_024
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

_SPOOL_LOCK = threading.Lock()
_SEQ_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()
_STATS_LOCK = threading.Lock()
_SEQ = 0
_WORKER: threading.Thread | None = None
_STOP = object()
_SETTINGS = {
    "queue_capacity": DEFAULT_QUEUE_CAPACITY,
    "batch_size": DEFAULT_BATCH_SIZE,
    "max_files": DEFAULT_MAX_FILES,
    "max_bytes": DEFAULT_MAX_BYTES,
    "max_age_seconds": DEFAULT_MAX_AGE_SECONDS,
}
_EVENT_QUEUE: queue_module.Queue[Any] = queue_module.Queue(
    maxsize=_SETTINGS["queue_capacity"]
)
_STATS = {
    "accepted": 0,
    "written": 0,
    "dropped_queue_full": 0,
    "dropped_writer": 0,
    "dropped_quota": 0,
    "dropped_invalid": 0,
}
_TRUSTED_SYSTEM_SYMLINKS = {
    Path("/etc"): Path("/private/etc"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}

# --- Fixed allow-lists (the read-side importer re-validates against these). ---
KINDS = {"tool", "llm", "subagent", "session", "kanban", "job"}
STATUSES = {
    "ok", "error", "blocked", "stalled", "finalized", "claimed",
    "verified_completion", "unverified_completion",
}
ERROR_CLASSES = {
    "tool_error", "plugin_block", "api_error", "timeout", "rate_limit",
    "retryable", "terminal", "other",
}
TOOL_FAMILIES = {
    "filesystem", "shell", "network", "delegate", "memory", "search",
    "editor", "kanban", "other",
}
MODEL_FAMILIES = {"claude", "gpt", "gemini", "llama", "qwen", "mistral", "other"}
LIFECYCLES = {"claimed", "completed", "blocked", "finalized", "stalled", "verified", "unverified"}

_TOOL_FAMILY_KEYWORDS = (
    ("delegate", "delegate"), ("subagent", "delegate"), ("agent", "delegate"),
    ("kanban", "kanban"), ("board", "kanban"),
    ("bash", "shell"), ("shell", "shell"), ("exec", "shell"), ("command", "shell"),
    ("grep", "search"), ("glob", "search"), ("search", "search"), ("find", "search"),
    ("edit", "editor"), ("apply_patch", "editor"), ("write", "filesystem"),
    ("read", "filesystem"), ("file", "filesystem"), ("ls", "filesystem"), ("cat", "filesystem"),
    ("memory", "memory"), ("remember", "memory"), ("recall", "memory"),
    ("http", "network"), ("fetch", "network"), ("curl", "network"), ("web", "network"),
    ("url", "network"), ("request", "network"),
)
_MODEL_FAMILY_KEYWORDS = (
    ("claude", "claude"), ("anthropic", "claude"), ("sonnet", "claude"),
    ("opus", "claude"), ("haiku", "claude"), ("fable", "claude"), ("mythos", "claude"),
    ("gpt", "gpt"), ("openai", "gpt"), ("o1", "gpt"), ("o3", "gpt"),
    ("gemini", "gemini"), ("google", "gemini"),
    ("llama", "llama"), ("qwen", "qwen"), ("mistral", "mistral"),
)
_ERROR_KEYWORDS = (
    ("timeout", "timeout"), ("timed out", "timeout"),
    ("rate", "rate_limit"), ("429", "rate_limit"),
    ("connection", "api_error"), ("network", "api_error"), ("http", "api_error"),
    ("api", "api_error"),
)


# The only source keys any handler ever reads.  A positional event mapping from
# a real host is projected onto exactly this allow-list before anything else;
# every other key (and any non-mapping positional value) is ignored so raw
# content -- command strings, prompts, message bodies, paths -- can never be
# consumed, let alone written.
_SOURCE_KEYS = frozenset({
    "session_id", "task_id", "turn_id", "tool_call_id", "tool_name",
    "duration_ms", "status", "error_type", "model", "provider",
    "api_request_id", "api_duration", "error", "reason", "retry_count",
    "parent_session_id", "parent_turn_id", "child_session_id", "child_status",
})


def _project_fields(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge optional positional event mappings with keyword fields.

    Only allowlisted source keys are read from a positional mapping; non-mapping
    positional values are ignored entirely.  Keyword arguments take precedence
    so existing keyword dispatch behaviour is exactly preserved.
    """

    merged: dict[str, Any] = {}
    for arg in args:
        if isinstance(arg, Mapping):
            for key in _SOURCE_KEYS:
                if key in arg:
                    merged[key] = arg[key]
    merged.update(kwargs)
    return merged


def _hook(handler: Callable[..., None]) -> Callable[..., None]:
    """Adapt a keyword-only handler to a real host's calling convention.

    A host may invoke a hook with keywords, with a single positional event
    mapping, or with an unexpected positional value.  The wrapper accepts all of
    them, projects only allowlisted metadata, and never propagates an exception
    (including TypeError) back into the host's hot path.
    """

    @functools.wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            handler(**_project_fields(args, kwargs))
        except Exception:
            _count("dropped_invalid")
        return None

    return wrapper


def register(ctx: Any) -> None:
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_llm_call", on_post_llm_call)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("api_request_error", on_api_request_error)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("on_session_finalize", on_session_finalize)
    ctx.register_hook("kanban_task_claimed", on_kanban_claimed)
    ctx.register_hook("kanban_task_completed", on_kanban_completed)
    ctx.register_hook("kanban_task_blocked", on_kanban_blocked)


# ------------------------------------------------------------------ handlers
@_hook
def on_post_tool_call(**kwargs: Any) -> None:
    record = {
        "kind": "tool",
        "session_hash": _hash(kwargs.get("session_id")),
        "task_hash": _hash(kwargs.get("task_id")),
        "turn_hash": _hash(kwargs.get("turn_id")),
        "actor_hash": _hash(kwargs.get("tool_call_id")),
        "tool_family": _tool_family(kwargs.get("tool_name")),
        "duration_ms": _duration(kwargs.get("duration_ms")),
        "status": _status(kwargs.get("status"), default="ok"),
        "error_class": _error_class(kwargs.get("error_type")),
    }
    _emit(record)


@_hook
def on_post_llm_call(**kwargs: Any) -> None:
    """Observe a completed turn while intentionally ignoring every text field."""

    _emit({
        "kind": "llm",
        "session_hash": _hash(kwargs.get("session_id")),
        "task_hash": _hash(kwargs.get("task_id")),
        "turn_hash": _hash(kwargs.get("turn_id")),
        "model_family": _model_family(kwargs.get("model"), None),
        "status": "ok",
        "lifecycle": "completed",
    })


@_hook
def on_post_api_request(**kwargs: Any) -> None:
    duration = kwargs.get("api_duration")
    record = {
        "kind": "llm",
        "session_hash": _hash(kwargs.get("session_id")),
        "task_hash": _hash(kwargs.get("task_id")),
        "turn_hash": _hash(kwargs.get("turn_id")),
        "actor_hash": _hash(kwargs.get("api_request_id")),
        "model_family": _model_family(kwargs.get("model"), kwargs.get("provider")),
        "duration_ms": _duration(_seconds_to_ms(duration)),
        "status": "ok",
    }
    _emit(record)


@_hook
def on_api_request_error(**kwargs: Any) -> None:
    error = kwargs.get("error")
    error_hint = error.get("type") if isinstance(error, dict) else kwargs.get("reason")
    record = {
        "kind": "llm",
        "session_hash": _hash(kwargs.get("session_id")),
        "task_hash": _hash(kwargs.get("task_id")),
        "turn_hash": _hash(kwargs.get("turn_id")),
        "actor_hash": _hash(kwargs.get("api_request_id")),
        "model_family": _model_family(kwargs.get("model"), kwargs.get("provider")),
        "duration_ms": _duration(_seconds_to_ms(kwargs.get("api_duration"))),
        "status": "error",
        "error_class": _error_class(error_hint),
        "retried": _truthy_int(kwargs.get("retry_count")),
    }
    _emit(record)


@_hook
def on_subagent_start(**kwargs: Any) -> None:
    """Record linkage only; ``child_goal`` and role text are never consumed."""

    _emit({
        "kind": "subagent",
        "session_hash": _hash(kwargs.get("parent_session_id")),
        "turn_hash": _hash(kwargs.get("parent_turn_id")),
        "actor_hash": _hash(kwargs.get("child_session_id")),
        "status": "claimed",
        "lifecycle": "claimed",
    })


@_hook
def on_subagent_stop(**kwargs: Any) -> None:
    record = {
        "kind": "subagent",
        "session_hash": _hash(kwargs.get("parent_session_id")),
        "turn_hash": _hash(kwargs.get("parent_turn_id")),
        "actor_hash": _hash(kwargs.get("child_session_id")),
        "duration_ms": _duration(kwargs.get("duration_ms")),
        "status": _child_status(kwargs.get("child_status")),
    }
    _emit(record)


@_hook
def on_session_finalize(**kwargs: Any) -> None:
    _emit({
        "kind": "session",
        "session_hash": _hash(kwargs.get("session_id")),
        "status": "finalized",
        "lifecycle": "finalized",
    })


@_hook
def on_kanban_claimed(**kwargs: Any) -> None:
    _emit({
        "kind": "kanban",
        "actor_hash": _hash(kwargs.get("task_id")),
        "status": "claimed",
        "lifecycle": "claimed",
    })


@_hook
def on_kanban_completed(**kwargs: Any) -> None:
    _emit({
        "kind": "kanban",
        "actor_hash": _hash(kwargs.get("task_id")),
        "status": "ok",
        "lifecycle": "completed",
    })


@_hook
def on_kanban_blocked(**kwargs: Any) -> None:
    _emit({
        "kind": "kanban",
        "actor_hash": _hash(kwargs.get("task_id")),
        "status": "stalled",
        "lifecycle": "blocked",
    })


# ------------------------------------------------------------------ emission
def _emit(record: dict[str, Any]) -> None:
    """Fail-open, bounded enqueue only; never perform filesystem I/O here."""
    try:
        payload = _finalize(record)
        worker = _ensure_worker()
        if worker is None:
            _count("dropped_writer")
            return None
        try:
            _EVENT_QUEUE.put_nowait(payload)
        except queue_module.Full:
            _count("dropped_queue_full")
        else:
            _count("accepted")
    except Exception:
        _count("dropped_invalid")
        return None
    return None


def _ensure_worker() -> threading.Thread | None:
    global _WORKER
    worker = _WORKER
    if worker is not None and worker.is_alive():
        return worker
    with _WORKER_LOCK:
        worker = _WORKER
        if worker is not None and worker.is_alive():
            return worker
        try:
            worker = threading.Thread(
                target=_writer_loop,
                args=(_EVENT_QUEUE,),
                name="hermes-process-observatory-writer",
                daemon=True,
            )
            worker.start()
        except Exception:
            return None
        _WORKER = worker
        return worker


def _writer_loop(event_queue: queue_module.Queue[Any]) -> None:
    stop_after_batch = False
    while not stop_after_batch:
        item = event_queue.get()
        if item is _STOP:
            event_queue.task_done()
            break
        batch = [item]
        while len(batch) < _SETTINGS["batch_size"]:
            try:
                item = event_queue.get_nowait()
            except queue_module.Empty:
                break
            if item is _STOP:
                event_queue.task_done()
                stop_after_batch = True
                break
            batch.append(item)
        for record in batch:
            try:
                _append_event(record)
            except Exception:
                _count("dropped_writer")
            else:
                _count("written")
            finally:
                event_queue.task_done()


def flush(timeout: float = 2.0) -> bool:
    """Wait for already accepted telemetry; intended for tests/finalization."""

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        return False
    event_queue = _EVENT_QUEUE
    if event_queue.unfinished_tasks and _ensure_worker() is None:
        return False
    deadline = time.monotonic() + float(timeout)
    with event_queue.all_tasks_done:
        while event_queue.unfinished_tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            event_queue.all_tasks_done.wait(remaining)
    return True


def stop(timeout: float = 2.0) -> bool:
    """Best-effort explicit worker stop; plugin unload may safely omit it."""

    global _WORKER
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        return False
    worker = _WORKER
    if worker is None:
        return True
    deadline = time.monotonic() + float(timeout)
    remaining = deadline - time.monotonic()
    try:
        _EVENT_QUEUE.put(_STOP, timeout=max(0.0, remaining))
    except queue_module.Full:
        return False
    worker.join(timeout=max(0.0, deadline - time.monotonic()))
    if worker.is_alive():
        return False
    with _WORKER_LOCK:
        if _WORKER is worker:
            _WORKER = None
    return True


def telemetry_stats() -> dict[str, int]:
    with _STATS_LOCK:
        result = dict(_STATS)
    result["queue_depth"] = _EVENT_QUEUE.qsize()
    return result


def _count(name: str, amount: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[name] = _STATS.get(name, 0) + amount


def _configure_for_tests(
    *,
    queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> None:
    """Reset bounded worker state.  Kept private for deterministic tests."""

    global _EVENT_QUEUE, _WORKER
    if not stop(timeout=2.0):
        raise RuntimeError("observatory worker did not stop")
    bounds = {
        "queue_capacity": (queue_capacity, 1, 4_096),
        "batch_size": (batch_size, 1, 128),
        "max_files": (max_files, 1, 10_000),
        "max_bytes": (max_bytes, 1_024, 100 * 1024 * 1024),
        "max_age_seconds": (max_age_seconds, 1, 90 * 24 * 60 * 60),
    }
    for name, (value, minimum, maximum) in bounds.items():
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"invalid {name}")
    with _WORKER_LOCK:
        _SETTINGS.update({name: value for name, (value, _, _) in bounds.items()})
        _EVENT_QUEUE = queue_module.Queue(maxsize=queue_capacity)
        _WORKER = None
    with _STATS_LOCK:
        for name in _STATS:
            _STATS[name] = 0


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    ts = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "kind": record["kind"], "ts": ts}
    # Copy only allowlisted, non-empty fields; drop anything else defensively.
    for key in (
        "session_hash", "task_hash", "turn_hash", "actor_hash", "tool_family",
        "model_family", "duration_ms", "status", "error_class", "retried", "lifecycle",
    ):
        value = record.get(key)
        if value in (None, ""):
            continue
        payload[key] = value
    payload["event_id"] = _event_id(now)
    return payload


def _event_id(now: dt.datetime) -> str:
    global _SEQ
    with _SEQ_LOCK:
        _SEQ += 1
        seq = _SEQ
    seed = f"{now.timestamp():.6f}-{os.getpid()}-{threading.get_ident()}-{seq}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _append_event(record: dict[str, Any]) -> None:
    queue = Path(os.environ.get("HERMES_PROCESS_OBSERVATORY_SPOOL", DEFAULT_SPOOL)).expanduser()
    _reject_unsafe_ancestors(queue)
    queue.mkdir(parents=True, exist_ok=True)
    _reject_unsafe_ancestors(queue)
    info = queue.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("observatory queue is not a safe directory")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ValueError("observatory queue is not owned by the current user")
    os.chmod(queue, 0o700)
    if stat.S_IMODE(queue.lstat().st_mode) & 0o077:
        raise ValueError("observatory queue permissions are not private")
    line = json.dumps(record, sort_keys=True)
    encoded_size = len(line.encode("utf-8"))
    if encoded_size > _SETTINGS["max_bytes"]:
        _count("dropped_quota")
        return
    with _SPOOL_LOCK:
        _reject_unsafe_ancestors(queue)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(queue, directory_flags)
        final_name = f"{record['event_id']}.jsonl"
        temp_name = f".{record['event_id']}.{threading.get_ident()}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        removed = _enforce_spool_quotas(
            directory_fd,
            now=time.time(),
            reserve_files=1,
            reserve_bytes=encoded_size,
        )
        if removed:
            _count("dropped_quota", removed)
        fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temp_name,
                final_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            removed = _enforce_spool_quotas(directory_fd, now=time.time())
            if removed:
                _count("dropped_quota", removed)
            os.fsync(directory_fd)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except OSError:
                pass
            raise
        finally:
            os.close(directory_fd)


def _enforce_spool_quotas(
    directory_fd: int,
    *,
    now: float,
    reserve_files: int = 0,
    reserve_bytes: int = 0,
) -> int:
    """Delete only plugin-shaped files, oldest first, until all bounds hold."""

    entries: list[tuple[float, str, int]] = []
    removed = 0
    for name in os.listdir(directory_fd):
        if not (
            name.endswith(".jsonl")
            or (name.startswith(".") and name.endswith(".tmp"))
        ):
            continue
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            continue
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            continue
        if now - info.st_mtime > _SETTINGS["max_age_seconds"]:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                continue
            removed += 1
            continue
        entries.append((info.st_mtime, name, max(0, int(info.st_size))))
    entries.sort(key=lambda item: (item[0], item[1]))
    total_bytes = sum(item[2] for item in entries)
    allowed_files = max(0, _SETTINGS["max_files"] - reserve_files)
    allowed_bytes = max(0, _SETTINGS["max_bytes"] - reserve_bytes)
    while len(entries) > allowed_files or total_bytes > allowed_bytes:
        _, name, size = entries.pop(0)
        try:
            os.unlink(name, dir_fd=directory_fd)
        except OSError:
            continue
        total_bytes -= size
        removed += 1
    return removed


def _reject_unsafe_ancestors(path: Path) -> None:
    """Reject symlinks except an exact allow-list of root-owned OS aliases."""

    absolute = path.absolute()
    missing = False
    for candidate in reversed((absolute, *absolute.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            missing = True
            continue
        if missing:
            raise ValueError("observatory queue ancestry changed while checking")
        if stat.S_ISLNK(info.st_mode):
            if not _trusted_system_symlink(candidate, info):
                raise ValueError("observatory queue ancestor is unsafe")
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("observatory queue ancestor is unsafe")


def _trusted_system_symlink(candidate: Path, info: os.stat_result) -> bool:
    expected = _TRUSTED_SYSTEM_SYMLINKS.get(candidate)
    if expected is None or info.st_uid != 0:
        return False
    try:
        target = Path(os.path.abspath(os.path.join(candidate.parent, os.readlink(candidate))))
    except OSError:
        return False
    return target == expected


# ------------------------------------------------------------------ projectors
def _hash(value: Any) -> str:
    if value in (None, ""):
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _classify(value: Any, keywords, allowed: set[str], default: str) -> str:
    if not isinstance(value, str):
        return default
    lowered = value.lower()
    for needle, family in keywords:
        if needle in lowered:
            return family
    return default


def _tool_family(name: Any) -> str:
    return _classify(name, _TOOL_FAMILY_KEYWORDS, TOOL_FAMILIES, "other")


def _model_family(model: Any, provider: Any) -> str:
    for candidate in (model, provider):
        family = _classify(candidate, _MODEL_FAMILY_KEYWORDS, MODEL_FAMILIES, "")
        if family:
            return family
    return "other"


def _error_class(hint: Any) -> str:
    if not isinstance(hint, str) or not hint.strip():
        return ""
    if hint in ERROR_CLASSES:
        return hint
    return _classify(hint, _ERROR_KEYWORDS, ERROR_CLASSES, "other")


def _status(value: Any, *, default: str) -> str:
    if isinstance(value, str) and value in STATUSES:
        return value
    return default


def _child_status(value: Any) -> str:
    mapping = {
        "completed": "ok", "complete": "ok", "ok": "ok", "success": "ok",
        "stalled": "stalled", "timeout": "stalled",
        "error": "error", "failed": "error", "failure": "error",
        "blocked": "blocked",
    }
    if isinstance(value, str):
        return mapping.get(value.lower(), "ok")
    return "ok"


def _duration(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ms = int(value)
    if 0 <= ms <= 30 * 24 * 60 * 60 * 1000:
        return ms
    return None


def _seconds_to_ms(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value * 1000.0


def _truthy_int(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    return None
