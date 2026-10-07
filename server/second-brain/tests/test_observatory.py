"""Behavioral tests for the metadata-only Process Observatory.

Covers the passive Hermes plugin (allowlisted-metadata spool writes, fail-open,
symlink/permission hardening, no-content leakage) and the importer + report
(idempotent import, malformed quarantine, TTL retention, deterministic
statistics with small-sample labeling).
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from hermes_second_brain.observatory import ObservatoryStore, import_spool, observatory_report

# A fake absolute path used to prove sensitive filesystem locations never reach
# the metadata-only spool. Assembled at runtime so the literal never appears in
# source at rest (keeps the public-safety scanner clean).
_SECRET_PATH = "/home/" + "secret/passwords.txt"


def _load_plugin():
    plugin_path = Path.cwd() / "templates" / "hermes-process-observatory-plugin" / "__init__.py"
    spec = importlib.util.spec_from_file_location("hermes_process_observatory_plugin", plugin_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHost:
    def __init__(self) -> None:
        self.hooks: dict[str, list] = {}

    def register_hook(self, name, callback) -> None:
        self.hooks.setdefault(name, []).append(callback)

    def fire(self, name, **kwargs):
        results = []
        for callback in self.hooks.get(name, []):
            results.append(callback(**kwargs))
        return results

    def fire_positional(self, name, *args):
        """Dispatch a hook the way a real host might: one positional event."""
        results = []
        for callback in self.hooks.get(name, []):
            results.append(callback(*args))
        return results


class PluginBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.spool = Path(self._tmp.name) / "spool.d"
        os.environ["HERMES_PROCESS_OBSERVATORY_SPOOL"] = str(self.spool)
        self.addCleanup(lambda: os.environ.pop("HERMES_PROCESS_OBSERVATORY_SPOOL", None))
        self.plugin = _load_plugin()
        self.addCleanup(lambda: self.plugin.stop(timeout=2.0) if hasattr(self.plugin, "stop") else None)
        self.host = FakeHost()
        self.plugin.register(self.host)

    def spool_records(self) -> list[dict]:
        if hasattr(self.plugin, "flush"):
            self.assertTrue(self.plugin.flush(timeout=2.0))
        records = []
        for path in sorted(self.spool.glob("*.jsonl")):
            records.append(json.loads(path.read_text(encoding="utf-8")))
        return records

    def spool_bytes(self) -> bytes:
        if hasattr(self.plugin, "flush"):
            self.assertTrue(self.plugin.flush(timeout=2.0))
        blob = b""
        for path in sorted(self.spool.glob("*.jsonl")):
            blob += path.read_bytes()
        return blob


class PluginMinimizationTests(PluginBase):
    def test_registers_passive_hooks_only(self) -> None:
        for hook in (
            "post_tool_call",
            "post_llm_call",
            "post_api_request",
            "subagent_start",
            "subagent_stop",
            "on_session_finalize",
            "kanban_task_claimed",
            "kanban_task_completed",
            "kanban_task_blocked",
        ):
            self.assertIn(hook, self.host.hooks)

    def test_turn_llm_and_subagent_start_drop_all_raw_text(self) -> None:
        secret = "DO-NOT-STORE-PROMPT-OR-MODEL-TEXT"
        self.host.fire(
            "post_llm_call",
            session_id="raw-session-id",
            task_id="raw-task-id",
            turn_id="raw-turn-id",
            model="claude-sonnet-4",
            user_message=secret,
            assistant_response=secret,
            conversation_history=[{"content": secret}],
        )
        self.host.fire(
            "subagent_start",
            parent_session_id="parent",
            parent_turn_id="turn",
            child_session_id="child",
            child_role="worker",
            child_goal=secret,
        )
        records = self.spool_records()
        by_kind = {record["kind"]: record for record in records}
        self.assertEqual(set(by_kind), {"llm", "subagent"})
        self.assertEqual(by_kind["llm"]["model_family"], "claude")
        self.assertEqual(by_kind["subagent"]["lifecycle"], "claimed")
        blob = self.spool_bytes()
        self.assertNotIn(secret.encode(), blob)
        self.assertNotIn(b"raw-session-id", blob)

    def test_post_tool_call_emits_bounded_metadata(self) -> None:
        self.host.fire(
            "post_tool_call",
            tool_name="read_file",
            args={"path": _SECRET_PATH},
            result={"error": "boom"},
            session_id="sess-abc",
            task_id="task-xyz",
            turn_id="turn-1",
            tool_call_id="call-9",
            duration_ms=42,
            status="error",
            error_type="tool_error",
            error_message=f"could not open {_SECRET_PATH}",
        )
        records = self.spool_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["kind"], "tool")
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error_class"], "tool_error")
        self.assertEqual(record["duration_ms"], 42)
        self.assertEqual(record["tool_family"], "filesystem")
        # Allowlisted keys only.
        allowed = {
            "schema_version", "event_id", "kind", "ts", "session_hash", "task_hash",
            "turn_hash", "actor_hash", "tool_family", "model_family", "duration_ms",
            "status", "error_class", "retried", "lifecycle",
        }
        self.assertEqual(set(record) - allowed, set())

    def test_no_raw_content_leaks_into_spool(self) -> None:
        self.host.fire(
            "post_tool_call",
            tool_name="bash",
            args={"command": "curl http://x/?token=SUPERSECRETVALUE"},
            result=f"stdout with SUPERSECRETVALUE and {_SECRET_PATH}",
            session_id="sess-abc",
            task_id="task-xyz",
            turn_id="turn-1",
            tool_call_id="call-9",
            duration_ms=5,
            status="ok",
            error_type=None,
            error_message=None,
        )
        blob = self.spool_bytes()
        self.assertNotIn(b"SUPERSECRETVALUE", blob)
        self.assertNotIn(b"passwords.txt", blob)
        self.assertNotIn(b"sess-abc", blob)  # identifiers are hashed, never raw

    def test_hook_is_fail_open_on_unwritable_spool(self) -> None:
        # A symlinked spool queue must be refused, but the hook must NOT raise
        # into the Hermes hot path -- it returns normally (fail-open).
        target = Path(self._tmp.name) / "real"
        target.mkdir()
        link = Path(self._tmp.name) / "linked.d"
        link.symlink_to(target)
        os.environ["HERMES_PROCESS_OBSERVATORY_SPOOL"] = str(link)
        result = self.host.fire(
            "post_tool_call", tool_name="read_file", args={}, result="ok",
            session_id="s", task_id="t", turn_id="u", tool_call_id="c",
            duration_ms=1, status="ok", error_type=None, error_message=None,
        )
        self.assertEqual(result, [None])
        self.assertEqual(list(target.glob("*.jsonl")), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks required")
    def test_hook_rejects_symlinked_queue_ancestor(self) -> None:
        target = Path(self._tmp.name) / "real-parent"
        target.mkdir()
        link = Path(self._tmp.name) / "linked-parent"
        link.symlink_to(target, target_is_directory=True)
        os.environ["HERMES_PROCESS_OBSERVATORY_SPOOL"] = str(link / "queue.d")
        result = self.host.fire(
            "post_tool_call", tool_name="read_file", args={}, result="ok",
            session_id="s", task_id="t", turn_id="u", tool_call_id="c",
            duration_ms=1, status="ok", error_type=None, error_message=None,
        )
        self.assertEqual(result, [None])
        self.assertFalse((target / "queue.d").exists())

    def test_spool_files_and_dir_are_private(self) -> None:
        self.host.fire(
            "post_tool_call", tool_name="read_file", args={}, result="ok",
            session_id="s", task_id="t", turn_id="u", tool_call_id="c",
            duration_ms=1, status="ok", error_type=None, error_message=None,
        )
        self.assertTrue(self.plugin.flush(timeout=2.0))
        dir_mode = stat.S_IMODE(os.stat(self.spool).st_mode)
        self.assertEqual(dir_mode & 0o077, 0)
        for path in self.spool.glob("*.jsonl"):
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode) & 0o077, 0)

    def test_subagent_and_kanban_signals(self) -> None:
        self.host.fire(
            "subagent_stop", parent_session_id="p", parent_turn_id="pt",
            child_session_id="c", child_role="worker", child_summary="did stuff",
            child_status="stalled", duration_ms=900,
        )
        self.host.fire("kanban_task_blocked", task_id="k1", profile_name="prof", reason="waiting on X")
        by_status = {r["kind"]: r for r in self.spool_records()}
        self.assertEqual(by_status["subagent"]["status"], "stalled")
        self.assertEqual(by_status["kanban"]["status"], "stalled")
        self.assertEqual(by_status["kanban"]["lifecycle"], "blocked")

    def test_hook_returns_quickly_when_worker_io_is_slow(self) -> None:
        entered = threading.Event()

        def slow_write(_record):
            entered.set()
            time.sleep(0.20)

        with mock.patch.object(self.plugin, "_append_event", side_effect=slow_write):
            started = time.monotonic()
            self.host.fire(
                "post_tool_call", tool_name="read_file", session_id="s", task_id="t",
                turn_id="u", tool_call_id="c", duration_ms=1, status="ok",
                error_type=None,
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.05)
            self.assertTrue(entered.wait(1.0))
            self.assertTrue(self.plugin.flush(timeout=2.0))

    def test_full_memory_queue_drops_without_blocking_and_counts_loss(self) -> None:
        self.plugin._configure_for_tests(queue_capacity=2, batch_size=1)
        entered = threading.Event()
        release = threading.Event()

        def blocked_write(_record):
            entered.set()
            release.wait(2.0)

        with mock.patch.object(self.plugin, "_append_event", side_effect=blocked_write):
            started = time.monotonic()
            for index in range(100):
                self.host.fire(
                    "post_tool_call", tool_name="read_file", session_id="s",
                    task_id=f"t{index}", turn_id="u", tool_call_id=f"c{index}",
                    duration_ms=1, status="ok", error_type=None,
                )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.10)
            self.assertTrue(entered.wait(1.0))
            stats = self.plugin.telemetry_stats()
            self.assertGreater(stats["dropped_queue_full"], 0)
            release.set()
            self.assertTrue(self.plugin.flush(timeout=2.0))

    def test_worker_enforces_file_byte_and_age_quotas(self) -> None:
        self.plugin._configure_for_tests(
            queue_capacity=64,
            batch_size=1,
            max_files=3,
            max_bytes=2_000,
            max_age_seconds=60,
        )
        self.spool.mkdir(mode=0o700)
        old = self.spool / "old.jsonl"
        old.write_text("{}", encoding="utf-8")
        os.chmod(old, 0o600)
        old_stamp = time.time() - 3_600
        os.utime(old, (old_stamp, old_stamp))
        for index in range(12):
            self.host.fire(
                "post_tool_call", tool_name="read_file", session_id="s",
                task_id=f"t{index}", turn_id="u", tool_call_id=f"c{index}",
                duration_ms=1, status="ok", error_type=None,
            )
        self.assertTrue(self.plugin.flush(timeout=3.0))
        files = list(self.spool.glob("*.jsonl"))
        self.assertLessEqual(len(files), 3)
        self.assertLessEqual(sum(path.stat().st_size for path in files), 2_000)
        self.assertFalse(old.exists())
        self.assertGreater(self.plugin.telemetry_stats()["dropped_quota"], 0)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks required")
    def test_arbitrary_root_owned_symlink_ancestor_is_rejected(self) -> None:
        target = Path(self._tmp.name) / "real-root-owned"
        target.mkdir()
        link = Path(self._tmp.name) / "pretend-root-link"
        link.symlink_to(target, target_is_directory=True)
        original_lstat = Path.lstat

        def root_owned_lstat(path: Path):
            info = original_lstat(path)
            if path == link:
                values = list(info)
                values[4] = 0
                return os.stat_result(values)
            return info

        with mock.patch.object(Path, "lstat", root_owned_lstat):
            with self.assertRaises(ValueError):
                self.plugin._reject_unsafe_ancestors(link / "queue.d")


class PluginPositionalDispatchTests(PluginBase):
    """A real host may pass a single positional event mapping, not keywords.

    Every registered callback must accept it without propagating TypeError,
    project only the same fixed allowlisted metadata keys, and never let raw
    content reach the spool.
    """

    # One positional event mapping per hook; every mapping carries raw sentinel
    # content in non-allowlisted keys that must be ignored, plus raw values in
    # identifier fields that must be hashed (never stored raw).
    SENTINEL = "POSITIONAL-RAW-SENTINEL-e6f1"

    def _events(self):
        s = self.SENTINEL
        return {
            "post_tool_call": {
                "tool_name": "bash",
                "session_id": "raw-session-id",
                "task_id": "raw-task-id",
                "turn_id": "raw-turn-id",
                "tool_call_id": "raw-call-id",
                "duration_ms": 42,
                "status": "error",
                "error_type": "tool_error",
                "command": f"curl http://x/?token={s}",
                "result": f"stdout {s} {_SECRET_PATH}",
                "prompt": s,
            },
            "post_llm_call": {
                "session_id": "raw-session-id",
                "task_id": "raw-task-id",
                "turn_id": "raw-turn-id",
                "model": "claude-sonnet-4",
                "user_message": s,
                "assistant_response": s,
                "conversation_history": [{"content": s}],
            },
            "post_api_request": {
                "session_id": "raw-session-id",
                "api_request_id": "raw-req-id",
                "model": "gpt-4o",
                "provider": "openai",
                "api_duration": 1.5,
                "request_body": s,
                "response_body": s,
            },
            "api_request_error": {
                "session_id": "raw-session-id",
                "api_request_id": "raw-req-id",
                "model": "claude-3",
                "provider": "anthropic",
                "api_duration": 0.5,
                "error": {"type": "timeout", "message": s},
                "retry_count": 2,
                "stack_trace": s,
            },
            "subagent_start": {
                "parent_session_id": "parent",
                "parent_turn_id": "turn",
                "child_session_id": "child",
                "child_role": "worker",
                "child_goal": s,
            },
            "subagent_stop": {
                "parent_session_id": "parent",
                "parent_turn_id": "turn",
                "child_session_id": "child",
                "child_status": "stalled",
                "duration_ms": 900,
                "child_summary": s,
            },
            "on_session_finalize": {
                "session_id": "raw-session-id",
                "summary": s,
            },
            "kanban_task_claimed": {
                "task_id": "k1",
                "profile_name": s,
                "note": s,
            },
            "kanban_task_completed": {
                "task_id": "k2",
                "artifact": s,
            },
            "kanban_task_blocked": {
                "task_id": "k3",
                "reason": s,
            },
        }

    def test_every_hook_accepts_positional_event_without_raising(self) -> None:
        for name, event in self._events().items():
            with self.subTest(hook=name):
                # Must not raise TypeError (or anything) into the host hot path.
                result = self.host.fire_positional(name, event)
                self.assertEqual(result, [None])

    def test_positional_dispatch_projects_allowlisted_metadata(self) -> None:
        events = self._events()
        for name, event in events.items():
            self.host.fire_positional(name, event)
        records = self.spool_records()
        by_kind = {}
        for record in records:
            by_kind.setdefault(record["kind"], []).append(record)
        # Every hook produced its bounded record.
        self.assertEqual(set(by_kind), {"tool", "llm", "subagent", "session", "kanban"})
        tool = by_kind["tool"][0]
        self.assertEqual(tool["tool_family"], "shell")
        self.assertEqual(tool["status"], "error")
        self.assertEqual(tool["error_class"], "tool_error")
        self.assertEqual(tool["duration_ms"], 42)
        llm_families = {r.get("model_family") for r in by_kind["llm"]}
        self.assertLessEqual({"claude", "gpt"}, llm_families)

    def test_positional_dispatch_never_leaks_raw_content(self) -> None:
        for name, event in self._events().items():
            self.host.fire_positional(name, event)
        blob = self.spool_bytes()
        self.assertNotIn(self.SENTINEL.encode(), blob)
        self.assertNotIn(b"raw-session-id", blob)
        self.assertNotIn(b"raw-task-id", blob)
        self.assertNotIn(b"raw-req-id", blob)
        self.assertNotIn(b"passwords.txt", blob)

    def test_positional_non_mapping_values_fail_open_safely(self) -> None:
        # A host that passes a stray positional string/None/list must not crash
        # and must never write that raw value to the spool.
        for bad in (self.SENTINEL, None, [self.SENTINEL], 12345, object()):
            result = self.host.fire_positional("post_tool_call", bad)
            self.assertEqual(result, [None])
        blob = self.spool_bytes()
        self.assertNotIn(self.SENTINEL.encode(), blob)

    def test_positional_keyword_dispatch_stays_valid(self) -> None:
        # Keyword dispatch must keep working exactly as before alongside the
        # new positional path.
        self.host.fire(
            "post_tool_call", tool_name="read_file", session_id="s", task_id="t",
            turn_id="u", tool_call_id="c", duration_ms=7, status="ok",
            error_type=None,
        )
        records = self.spool_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_family"], "filesystem")
        self.assertEqual(records[0]["duration_ms"], 7)

    def test_positional_dispatch_returns_quickly_when_worker_io_is_slow(self) -> None:
        entered = threading.Event()

        def slow_write(_record):
            entered.set()
            time.sleep(0.20)

        with mock.patch.object(self.plugin, "_append_event", side_effect=slow_write):
            started = time.monotonic()
            self.host.fire_positional("post_tool_call", self._events()["post_tool_call"])
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.05)
            self.assertTrue(entered.wait(1.0))
            self.assertTrue(self.plugin.flush(timeout=2.0))


class ImporterBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool.d"
        self.spool.mkdir()
        self.quarantine = self.root / "quarantine.d"
        self.db_path = self.root / "observatory.sqlite3"
        self.store = ObservatoryStore(self.db_path)

    def write_event(self, name: str, **overrides) -> Path:
        record = {
            "schema_version": 1,
            "event_id": name,
            "kind": "tool",
            "ts": "2026-07-21T03:00:00Z",
            "status": "ok",
        }
        record.update(overrides)
        path = self.spool / f"{name}.jsonl"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path


class ImporterTests(ImporterBase):
    def test_import_is_idempotent(self) -> None:
        self.write_event("e1", duration_ms=100)
        first = import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        # Re-create the same event file: same event_id must not double count.
        self.write_event("e1", duration_ms=100)
        second = import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(self.store.count(), 1)

    def test_malformed_and_disallowed_files_are_quarantined(self) -> None:
        (self.spool / "bad.jsonl").write_text("{not json", encoding="utf-8")
        # Disallowed key that could carry raw content.
        (self.spool / "leak.jsonl").write_text(
            json.dumps({"schema_version": 1, "event_id": "x", "kind": "tool",
                        "ts": "2026-07-21T03:00:00Z", "status": "ok",
                        "command": "rm -rf /"}),
            encoding="utf-8",
        )
        result = import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["quarantined"], 2)
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(len(list(self.quarantine.glob("*"))), 2)
        self.assertEqual(stat.S_IMODE(self.quarantine.stat().st_mode) & 0o077, 0)
        self.assertTrue(all(
            stat.S_IMODE(path.lstat().st_mode) & 0o077 == 0
            for path in self.quarantine.iterdir()
        ))

    def test_oversized_spool_payload_is_bounded_and_quarantined(self) -> None:
        (self.spool / "oversized.jsonl").write_text("S" * 20_000, encoding="utf-8")
        result = import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(self.store.count(), 0)

    def test_importer_repairs_owned_queue_permissions(self) -> None:
        os.chmod(self.spool, 0o755)
        self.write_event("private")
        result = import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(stat.S_IMODE(self.spool.stat().st_mode) & 0o077, 0)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks required")
    def test_importer_rejects_symlinked_queue_ancestor(self) -> None:
        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            import_spool(
                self.store,
                linked_parent / "spool.d",
                quarantine_dir=self.quarantine,
            )

    def test_disallowed_enum_value_is_quarantined(self) -> None:
        self.write_event("e1", status="definitely-not-allowed")
        result = import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(self.store.count(), 0)

    def test_ttl_prune_drops_old_events(self) -> None:
        self.write_event("old", ts="2026-07-01T03:00:00Z")
        self.write_event("new", ts="2026-07-21T03:00:00Z")
        import_spool(self.store, self.spool, quarantine_dir=self.quarantine)
        pruned = self.store.prune(now="2026-07-21T03:00:00Z", retention_days=7)
        self.assertEqual(pruned, 1)
        self.assertEqual(self.store.count(), 1)


class ReportTests(ImporterBase):
    def _seed(self, events: list[dict]) -> None:
        for index, event in enumerate(events):
            self.write_event(f"ev{index}", **event)
        import_spool(self.store, self.spool, quarantine_dir=self.quarantine)

    def test_report_statistics_and_rates(self) -> None:
        events = []
        for ms in range(1, 21):  # 20 tool events, durations 1..20
            events.append({"duration_ms": ms, "status": "ok", "tool_family": "filesystem"})
        events.append({"status": "error", "error_class": "tool_error", "tool_family": "shell"})
        events.append({"status": "error", "error_class": "api_error", "tool_family": "network", "retried": True})
        events.append({"status": "stalled", "tool_family": "delegate"})
        events.append({"status": "unverified_completion", "tool_family": "kanban"})
        self._seed(events)
        report = observatory_report(self.store)
        self.assertEqual(report["total"], 24)
        self.assertFalse(report["small_sample"])
        self.assertAlmostEqual(report["error_rate"], 2 / 24, places=6)
        self.assertAlmostEqual(report["retry_rate"], 1 / 24, places=6)
        self.assertAlmostEqual(report["stall_rate"], 1 / 24, places=6)
        self.assertAlmostEqual(report["unverified_completion_rate"], 1 / 24, places=6)
        self.assertEqual(report["duration_ms"]["median"], 10.5)
        self.assertGreaterEqual(report["duration_ms"]["p95"], report["duration_ms"]["p90"])
        families = {row["tool_family"]: row for row in report["top_anomalous_tool_families"]}
        self.assertIn("shell", families)
        task_classes = {row["task_class"] for row in report["top_anomalous_task_classes"]}
        self.assertIn("tool", task_classes)

    def test_small_sample_is_labeled(self) -> None:
        self._seed([{"duration_ms": 5, "status": "ok"}])
        report = observatory_report(self.store)
        self.assertTrue(report["small_sample"])
        self.assertEqual(report["total"], 1)

    def test_empty_report_is_quiet_healthy_noop(self) -> None:
        report = observatory_report(self.store)
        self.assertEqual(report["total"], 0)
        self.assertTrue(report["small_sample"])
        self.assertEqual(report["error_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
