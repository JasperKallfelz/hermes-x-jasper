from __future__ import annotations

import json
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes_second_brain.cli import main
from hermes_second_brain.eval import _parse_hits, run_eval
from hermes_second_brain.lcm_export import export_lcm_summaries
from hermes_second_brain.mail_ingest import ingest_jsonl, materialize_markdown
from hermes_second_brain.migrate import migrate_holographic_db, migrate_markdown, write_jsonl, write_markdown_records
from hermes_second_brain.ov_adapter import OpenVikingAdapter, build_target_uri

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
FAKE_OV = TESTS / "fake_ov.py"


class AdapterMailMigrateEvalTests(unittest.TestCase):
    def test_adapter_builds_real_add_and_tag_commands_without_shell(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 4, stdout="", stderr="missing")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"uri":"viking://resources/brain/brain_s1.md"}', stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=9, attempts=1)
            result = adapter.sync_resource(path=Path(td) / "a.md", source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.md", existing_uri=None)

        self.assertEqual(result.resource_id, "viking://resources/brain/brain_s1.md")
        self.assertEqual(calls[0][0], ["ov-test", "stat", "viking://resources/brain/brain_s1.md", "-o", "json"])
        self.assertEqual(calls[1][0], ["ov-test", "add-resource", str(Path(td) / "a.md"), "--to", "viking://resources/brain/brain_s1.md", "--no-progress", "-o", "json"])
        self.assertEqual(calls[2][0], ["ov-test", "set-tags", "viking://resources/brain/brain_s1.md", "--tags", "source_id=brain:s1,sha256=abc,namespace=brain", "--mode", "replace", "-o", "json"])
        self.assertTrue(all(kwargs["shell"] is False for _, kwargs in calls))

    def test_adapter_uses_write_for_existing_text_and_exact_rm_for_existing_binary_file(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":false}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            adapter.sync_resource(path=Path(td) / "a.txt", source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.txt", existing_uri="viking://resources/brain/brain_s1.txt")
            adapter.sync_resource(path=Path(td) / "b.pdf", source_id="brain:s2", namespace="brain", sha256="def", relative_path="b.pdf", existing_uri="viking://resources/brain/brain_s2.pdf")

        self.assertIn(["ov-test", "write", "viking://resources/brain/brain_s1.txt", "--from-file", str(Path(td) / "a.txt"), "-o", "json"], calls)
        rm = ["ov-test", "rm", "viking://resources/brain/brain_s2.pdf", "--wait", "--timeout", "5", "-o", "json"]
        self.assertIn(rm, calls)
        self.assertNotIn("--recursive", rm)
        self.assertNotIn(["ov-test", "rm", "viking://resources/brain/", "--recursive", "--wait", "--timeout", "5", "-o", "json"], calls)

    def test_write_resource_argv_matches_openviking_0_4_10(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            path = Path(td) / "a.txt"
            OpenVikingAdapter(attempts=1).write_resource(path=path, target_uri="viking://resources/brain/brain_s1.txt")

        self.assertEqual(calls[-1][0], ["ov", "write", "viking://resources/brain/brain_s1.txt", "--from-file", str(path), "-o", "json"])
        self.assertTrue(all(kwargs["shell"] is False for _, kwargs in calls))

    def test_write_resource_does_not_add_no_progress(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            path = Path(td) / "a.txt"
            OpenVikingAdapter(attempts=1).write_resource(path=path, target_uri="viking://resources/brain/brain_s1.txt")

        self.assertEqual(calls, [["ov", "write", "viking://resources/brain/brain_s1.txt", "--from-file", str(path), "-o", "json"]])
        self.assertNotIn("--no-progress", calls[0])

    def test_changed_text_container_replaces_exact_uri_without_write(self) -> None:
        calls = []
        target = "viking://resources/brain/brain_s1.txt"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":true}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"uri":"' + target + '"}', stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            path = Path(td) / "a.txt"
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            adapter.sync_resource(path=path, source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.txt", existing_uri=target)

        self.assertEqual(calls[0], ["ov-test", "stat", target, "-o", "json"])
        rm = ["ov-test", "rm", target, "--recursive", "--wait", "--timeout", "5", "-o", "json"]
        self.assertIn(rm, calls)
        self.assertFalse(rm[2].endswith("/"))
        self.assertNotIn("*", rm[2])
        self.assertIn(["ov-test", "add-resource", str(path), "--to", target, "--no-progress", "-o", "json"], calls)
        self.assertFalse(any(command[1] == "write" for command in calls))
        self.assertEqual(len([command for command in calls if command[1] == "stat"]), 1)

    def test_async_changed_container_removes_exact_uri_without_wait(self) -> None:
        calls = []
        target = "viking://resources/brain/brain_s1.txt"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":true}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"uri":"' + target + '"}', stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            path = Path(td) / "a.txt"
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            adapter.sync_resource(
                path=path,
                source_id="brain:s1",
                namespace="brain",
                sha256="abc",
                relative_path="a.txt",
                existing_uri=target,
                resume_locked=True,
            )

        rm = ["ov-test", "rm", target, "--recursive", "-o", "json"]
        self.assertIn(rm, calls)
        self.assertNotIn("--wait", rm)
        self.assertNotIn("--timeout", rm)
        self.assertFalse(rm[2].endswith("/"))
        self.assertNotIn("*", rm[2])

    def test_changed_text_actual_file_uses_write_without_no_progress(self) -> None:
        calls = []
        target = "viking://resources/brain/brain_s1.md"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":false}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            path = Path(td) / "a.md"
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            adapter.sync_resource(path=path, source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.md", existing_uri=target)

        write = ["ov-test", "write", target, "--from-file", str(path), "-o", "json"]
        self.assertIn(write, calls)
        self.assertNotIn("--no-progress", write)
        self.assertFalse(any(command[1] == "rm" for command in calls))

    def test_interrupted_initial_add_existing_container_only_tags(self) -> None:
        calls = []
        target = "viking://resources/brain/brain_s1.md"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":true,"tags":{"sha256":"abc"}}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            result = adapter.sync_resource(path=Path(td) / "a.md", source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.md", existing_uri=None)

        self.assertEqual(result.resource_id, target)
        self.assertEqual([command[1] for command in calls], ["stat", "set-tags"])
        self.assertFalse(any(command[1] in {"rm", "add-resource", "write"} for command in calls))

    def test_changed_chunked_container_never_writes_child(self) -> None:
        calls = []
        target = "viking://resources/context/context-inbox.txt"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":true}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"uri":"' + target + '"}', stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            path = Path(td) / "context-inbox.txt"
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            adapter.sync_resource(path=path, source_id="context:context-inbox", namespace="context", sha256="abc", relative_path="context-inbox.txt", existing_uri=target)

        child = f"{target}/{path.name}"
        self.assertNotIn(["ov-test", "write", child, "--from-file", str(path), "-o", "json"], calls)
        self.assertFalse(any(command[1] == "write" for command in calls))
        self.assertIn(["ov-test", "rm", target, "--recursive", "--wait", "--timeout", "5", "-o", "json"], calls)

    def test_changed_pdf_and_docx_containers_replace_exact_uri_recursively(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":true}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            for name in ("a.pdf", "b.docx"):
                target = f"viking://resources/brain/brain_{Path(name).stem}{Path(name).suffix}"
                adapter.sync_resource(path=Path(td) / name, source_id=f"brain:{Path(name).stem}", namespace="brain", sha256="abc", relative_path=name, existing_uri=target)

        self.assertIn(["ov-test", "rm", "viking://resources/brain/brain_a.pdf", "--recursive", "--wait", "--timeout", "5", "-o", "json"], calls)
        self.assertIn(["ov-test", "add-resource", str(Path(td) / "a.pdf"), "--to", "viking://resources/brain/brain_a.pdf", "--no-progress", "-o", "json"], calls)
        self.assertIn(["ov-test", "rm", "viking://resources/brain/brain_b.docx", "--recursive", "--wait", "--timeout", "5", "-o", "json"], calls)
        self.assertIn(["ov-test", "add-resource", str(Path(td) / "b.docx"), "--to", "viking://resources/brain/brain_b.docx", "--no-progress", "-o", "json"], calls)

    def test_remove_resource_recursive_validates_exact_safe_uri_before_cli(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            with self.assertRaisesRegex(ValueError, "unsafe OpenViking resource URI"):
                adapter.remove_resource("viking://resources/brain/", recursive=True)
            with self.assertRaisesRegex(ValueError, "unsafe OpenViking resource URI"):
                adapter.remove_resource("viking://resources/brain/*.txt", recursive=True)
            adapter.remove_resource("viking://resources/brain/brain_s1.txt", recursive=True)

        self.assertEqual(calls, [["ov-test", "rm", "viking://resources/brain/brain_s1.txt", "--recursive", "--wait", "--timeout", "5", "-o", "json"]])

    def test_adapter_resumes_created_resource_without_duplicate_vlm_write(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":false,"tags":{"sha256":"abc"}}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            result = adapter.sync_resource(
                path=Path(td) / "a.md",
                source_id="brain:s1",
                namespace="brain",
                sha256="abc",
                relative_path="a.md",
                existing_uri=None,
            )

        self.assertEqual(result.resource_id, "viking://resources/brain/brain_s1.md")
        self.assertEqual([command[1] for command in calls], ["stat", "set-tags"])
        self.assertNotIn("add-resource", [command[1] for command in calls])
        self.assertNotIn("write", [command[1] for command in calls])

    def test_adapter_replaces_stale_deterministic_resource_without_matching_sha_tag(self) -> None:
        calls = []
        target = "viking://resources/brain/brain_s1.md"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"result":{"isDir":true,"tags":{"sha256":"old"}}}', stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"uri":"' + target + '"}', stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", fake_run):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            result = adapter.sync_resource(path=Path(td) / "a.md", source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.md", existing_uri=None)

        self.assertEqual(result.resource_id, target)
        self.assertEqual([command[1] for command in calls], ["stat", "rm", "add-resource", "set-tags"])

    def test_adapter_treats_already_exists_as_resume_only_after_exact_stat(self) -> None:
        calls = []

        def stat_succeeds(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "stat":
                code = 4 if len([c for c in calls if c[1] == "stat"]) == 1 else 0
                return subprocess.CompletedProcess(cmd, code, stdout="{}", stderr="")
            if cmd[1] == "add-resource":
                return subprocess.CompletedProcess(cmd, 5, stdout="", stderr='{"error":"already exists"}')
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", stat_succeeds):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            result = adapter.sync_resource(path=Path(td) / "a.md", source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.md", existing_uri=None)
        self.assertEqual(result.resource_id, "viking://resources/brain/brain_s1.md")

        def stat_fails(cmd, **kwargs):
            if cmd[1] == "stat":
                return subprocess.CompletedProcess(cmd, 4, stdout="", stderr="missing")
            if cmd[1] == "add-resource":
                return subprocess.CompletedProcess(cmd, 5, stdout="", stderr='{"error":"already exists"}')
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(subprocess, "run", stat_fails):
            adapter = OpenVikingAdapter(binary="ov-test", timeout_seconds=5, attempts=1)
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                adapter.sync_resource(path=Path(td) / "a.md", source_id="brain:s1", namespace="brain", sha256="abc", relative_path="a.md", existing_uri=None)

    def test_target_uri_sanitizes_namespace_source_id_and_suffix(self) -> None:
        self.assertEqual(
            build_target_uri(source_id="team/brain:abc..def", namespace="Team Brain", relative_path="notes/Doc.MD"),
            "viking://resources/Team_Brain/team_brain_abc_def.md",
        )
        with self.assertRaises(ValueError):
            build_target_uri(source_id="../bad", namespace="brain", relative_path="../x.sh")

    def test_mail_ingest_redacts_security_codes_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "mail.jsonl"
            count = ingest_jsonl(FIXTURES / "mail" / "events.jsonl", output)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(count, 2)
        self.assertTrue(all("123456" not in json.dumps(row) for row in rows))
        self.assertTrue(any("[CODE]" in row["subject"] for row in rows))
        self.assertTrue(any("[SECURITY-MAIL]" in row["body_text"] for row in rows))
        self.assertEqual(rows[0]["retention_days"], 30)

    def test_mail_markdown_metadata_idempotency_security_and_no_deletion_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            out_dir = tmp_path / "mail-md"

            self.assertEqual(materialize_markdown(FIXTURES / "mail" / "events.jsonl", out_dir), 2)
            files = sorted(out_dir.glob("*.md"))
            self.assertEqual(len(files), 2)
            rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)
            self.assertIn("event_id:", rendered)
            self.assertIn('message_id: "m-1"', rendered)
            self.assertIn('received_at: "2026-07-17T09:00:00Z"', rendered)
            self.assertIn('from_domain: "example.com"', rendered)
            self.assertIn('subject: "Project update"', rendered)
            self.assertIn('tags: ["workflow"]', rendered)
            self.assertIn("retention_days: 30", rendered)
            self.assertIn("Ghostty workflow notes attached", rendered)
            self.assertNotIn("123456", rendered)
            self.assertIn("[CODE]", rendered)

            first_mtimes = {path.name: path.stat().st_mtime_ns for path in files}
            self.assertEqual(materialize_markdown(FIXTURES / "mail" / "events.jsonl", out_dir), 2)
            self.assertEqual({path.name: path.stat().st_mtime_ns for path in files}, first_mtimes)

            empty = tmp_path / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(materialize_markdown(empty, out_dir), 0)
            self.assertEqual(sorted(path.name for path in out_dir.glob("*.md")), sorted(first_mtimes))

            files[0].unlink()
            files[0].symlink_to(tmp_path / "escape.md")
            with self.assertRaises(ValueError):
                materialize_markdown(FIXTURES / "mail" / "events.jsonl", out_dir)

    def test_mail_cli_supports_jsonl_and_markdown_and_is_quiet_without_verbose(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp_path = Path(td)
            code = main(
                [
                    "mail-ingest",
                    "--input",
                    str(FIXTURES / "mail" / "events.jsonl"),
                    "--output",
                    str(tmp_path / "mail.jsonl"),
                    "--output-dir",
                    str(tmp_path / "mail-md"),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(len((tmp_path / "mail.jsonl").read_text(encoding="utf-8").splitlines()), 2)
            self.assertEqual(len(list((tmp_path / "mail-md").glob("*.md"))), 2)

    def test_markdown_and_holographic_migration_preserve_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            memory = tmp_path / "MEMORY.md"
            memory.write_text("# Memory\n\nCritical always-on fact.\n\nCritical always-on fact.\n", encoding="utf-8")
            records = migrate_markdown(memory, "MEMORY.md")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].category, "MEMORY")
            self.assertEqual(records[0].trust, "user-authored")

            db = tmp_path / "memory_store.db"
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE facts(id TEXT, fact TEXT, category TEXT, tags TEXT, trust TEXT, created_at TEXT, updated_at TEXT)")
                conn.execute(
                    "INSERT INTO facts VALUES(?,?,?,?,?,?,?)",
                    ("f1", "Holographic fact", "workflow", '["holographic","workflow"]', "0.9", "2026-01-01", "2026-01-02"),
                )
                conn.commit()
            finally:
                conn.close()
            facts = migrate_holographic_db(db)
            self.assertEqual(facts[0].source, str(db))
            self.assertEqual(facts[0].tags, ("holographic", "workflow"))
            self.assertEqual(facts[0].created_at, "2026-01-01")
            out = tmp_path / "migration.jsonl"
            self.assertEqual(write_jsonl(records + facts, out), 2)

    def test_holographic_exact_schema_preserves_fact_metadata_without_vector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory_store.db"
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    CREATE TABLE facts(
                        fact_id INTEGER,
                        content TEXT,
                        category TEXT,
                        tags TEXT,
                        trust_score REAL,
                        retrieval_count INTEGER,
                        helpful_count INTEGER,
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP,
                        hrr_vector BLOB
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (42, "Production fact", "workflow", '["holographic","prod"]', 0.875, 3, 2, "2026-07-01 01:02:03", "2026-07-02 03:04:05", b"raw-vector"),
                )
                conn.commit()
            finally:
                conn.close()

            records = migrate_holographic_db(db)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fact_id, "42")
        self.assertEqual(records[0].text, "Production fact")
        self.assertEqual(records[0].category, "workflow")
        self.assertEqual(records[0].tags, ("holographic", "prod"))
        self.assertEqual(records[0].trust_score, "0.875")
        self.assertEqual(records[0].created_at, "2026-07-01 01:02:03")
        self.assertNotIn("raw-vector", records[0].as_json())

    def test_migration_markdown_materialization_is_stable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            memory = tmp_path / "MEMORY.md"
            memory.write_text("# Memory\n\nKeep this fact.\n", encoding="utf-8")
            records = migrate_markdown(memory, "MEMORY.md")
            out_dir = tmp_path / "migration-md"
            self.assertEqual(write_markdown_records(records, out_dir), 1)
            files = list(out_dir.glob("*.md"))
            self.assertEqual(len(files), 1)
            first = files[0].read_text(encoding="utf-8")
            first_mtime = files[0].stat().st_mtime_ns
            self.assertIn('source: "' + str(memory) + '"', first)
            self.assertIn("Keep this fact.", first)
            self.assertEqual(write_markdown_records(records, out_dir), 1)
            self.assertEqual(files[0].stat().st_mtime_ns, first_mtime)

    def test_lcm_export_metadata_idempotency_and_no_deletion_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            db = tmp_path / "lcm.db"
            _create_lcm_db(db)
            out_dir = tmp_path / "lcm-out"

            self.assertEqual(export_lcm_summaries([db], out_dir), 1)
            files = list(out_dir.glob("*.md"))
            self.assertEqual(len(files), 1)
            exported = files[0].read_text(encoding="utf-8")
            self.assertIn('source_db: "' + str(db) + '"', exported)
            self.assertIn('session_id: "session-a"', exported)
            self.assertIn("node_id: 7", exported)
            self.assertIn("depth: 2", exported)
            self.assertIn("token_count: 12", exported)
            self.assertIn('expand_hint: "expand here"', exported)
            self.assertIn("Exact summary text.", exported)
            self.assertNotIn("raw message secret", exported)
            first_mtime = files[0].stat().st_mtime_ns

            self.assertEqual(export_lcm_summaries([db], out_dir), 1)
            self.assertEqual(files[0].stat().st_mtime_ns, first_mtime)

            conn = sqlite3.connect(db)
            try:
                conn.execute("DELETE FROM summary_nodes")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(export_lcm_summaries([db], out_dir), 0)
            self.assertTrue(files[0].exists())

    def test_lcm_cli_is_quiet_without_verbose_and_supports_multiple_dbs(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp_path = Path(td)
            db1 = tmp_path / "one.db"
            db2 = tmp_path / "two.db"
            _create_lcm_db(db1, node_id=1, session_id="a")
            _create_lcm_db(db2, node_id=2, session_id="b")
            code = main(["lcm-export", "--lcm-db", str(db1), "--lcm-db", str(db2), "--output-dir", str(tmp_path / "out")])
            exported_count = len(list((tmp_path / "out").glob("*.md")))
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(exported_count, 2)

    def test_scheduled_scripts_are_quiet_and_run_exports_before_sync(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            (corpus / "doc.md").write_text("hello", encoding="utf-8")
            manifest = tmp_path / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "state_db": str(tmp_path / "state.sqlite3"),
                        "ov_binary": str(FAKE_OV),
                        "ov_timeout_seconds": 5,
                        "retry_attempts": 1,
                        "retry_backoff_seconds": 0.01,
                        "concurrency": 1,
                        "sources": [{"id": "fixtures", "root": str(corpus), "namespace": "brain"}],
                    }
                ),
                encoding="utf-8",
            )
            memory = tmp_path / "MEMORY.md"
            memory.write_text("# Memory\n\nScheduled fact.\n", encoding="utf-8")
            lcm_db = tmp_path / "lcm.db"
            _create_lcm_db(lcm_db)
            env = {
                **os.environ,
                "PROJECT_DIR": str(Path.cwd()),
                "MANIFEST": str(manifest),
                "PYTHON": sys.executable,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "LOCK_DIR": str(tmp_path / "lock"),
                "IMPORT_DIR": str(tmp_path / "imports"),
                "HERMES_MEMORY_MD": str(memory),
                "LCM_DBS": str(lcm_db),
                "MAIL_EVENTS": str(FIXTURES / "mail" / "events.jsonl"),
                "FAKE_OV_STATE": str(tmp_path / "ov.json"),
                "OV_BINARY": str(FAKE_OV),
            }
            deploy = subprocess.run(["sh", "scripts/deploy_check.sh"], shell=False, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(deploy.returncode, 0, deploy.stderr)
            self.assertEqual(deploy.stdout, "")
            self.assertEqual(deploy.stderr, "")
            self.assertEqual(len(list((tmp_path / "imports" / "mail").glob("*.md"))), 2)
            self.assertEqual(len(list((tmp_path / "imports" / "lcm").glob("*.md"))), 1)
            self.assertEqual(len(list((tmp_path / "imports" / "migration").glob("*.md"))), 1)

            health = subprocess.run(["sh", "scripts/health_check.sh"], shell=False, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(health.returncode, 0, health.stderr)
            self.assertEqual(health.stdout, "")
            self.assertEqual(health.stderr, "")

    def test_scheduled_mail_missing_file_is_healthy_noop_and_import_dir_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            (corpus / "doc.md").write_text("hello", encoding="utf-8")
            manifest = tmp_path / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "state_db": str(tmp_path / "state.sqlite3"),
                        "ov_binary": str(FAKE_OV),
                        "ov_timeout_seconds": 5,
                        "retry_attempts": 1,
                        "retry_backoff_seconds": 0.01,
                        "concurrency": 1,
                        "sources": [{"id": "fixtures", "root": str(corpus), "namespace": "brain"}],
                    }
                ),
                encoding="utf-8",
            )
            base_env = {
                **os.environ,
                "PROJECT_DIR": str(Path.cwd()),
                "MANIFEST": str(manifest),
                "PYTHON": sys.executable,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "LOCK_DIR": str(tmp_path / "lock"),
                "MAIL_EVENTS": str(tmp_path / "missing.jsonl"),
                "FAKE_OV_STATE": str(tmp_path / "ov.json"),
                "OV_BINARY": str(FAKE_OV),
            }

            env = {**base_env, "IMPORT_DIR": str(tmp_path / "imports")}
            deploy = subprocess.run(["sh", "scripts/deploy_check.sh"], shell=False, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(deploy.returncode, 0, deploy.stderr)
            self.assertEqual(deploy.stdout, "")
            self.assertFalse((tmp_path / "imports" / "mail").exists())

            fail_env = dict(base_env)
            fail_env.pop("IMPORT_DIR", None)
            fail_env["LOCK_DIR"] = str(tmp_path / "lock-fail")
            deploy = subprocess.run(["sh", "scripts/deploy_check.sh"], shell=False, cwd=Path.cwd(), env=fail_env, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(deploy.returncode, 1)
            self.assertIn("IMPORT_DIR is required", deploy.stderr)

    def test_recall_eval_report_passes_with_fake_ov(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "eval.json"
            code = run_eval(FIXTURES / "eval_spec.json", report, ov_binary=str(FAKE_OV), threshold=0.75)
            data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertIs(data["passed"], True)
        self.assertEqual(data["avg_recall_at_k"], 1.0)

    def test_recall_eval_parses_ov_command_preamble_and_any_of_sources(self) -> None:
        payload = {
            "ok": True,
            "result": {
                "memories": [],
                "resources": [{"uri": "viking://resources/brain/alternate.md/section.md"}],
                "skills": [],
            },
        }
        self.assertEqual(
            _parse_hits("cmd: ov find --uri=viking://resources/brain\n" + json.dumps(payload) + "\n"),
            ["viking://resources/brain/alternate.md/section.md"],
        )

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            fake_ov = tmp_path / "ov"
            fake_ov.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'cmd: ov find' "
                "'{\"ok\":true,\"result\":{\"memories\":[],\"resources\":[{\"uri\":\"viking://resources/brain/alternate.md/section.md\"}],\"skills\":[]}}'\n",
                encoding="utf-8",
            )
            fake_ov.chmod(0o700)
            spec = tmp_path / "spec.json"
            spec.write_text(
                json.dumps(
                    {
                        "k": 3,
                        "queries": [
                            {
                                "query": "alternate source",
                                "namespace": "brain",
                                "expected_any_source_ids": [
                                    "viking://resources/brain/preferred.md",
                                    "viking://resources/brain/alternate.md",
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = tmp_path / "report.json"
            code = run_eval(spec, report, ov_binary=str(fake_ov), threshold=1.0)
        self.assertEqual(code, 0)

    @unittest.skipUnless(os.environ.get("HSB_REAL_OV"), "set HSB_REAL_OV=/path/to/ov to run against installed OpenViking")
    def test_real_openviking_cli_contract_help(self) -> None:
        ov_binary = os.environ["HSB_REAL_OV"]
        for subcommand in ("add-resource", "write", "stat", "rm", "set-tags", "find"):
            proc = subprocess.run([ov_binary, subcommand, "--help"], shell=False, capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

def _create_lcm_db(path: Path, node_id: int = 7, session_id: str = "session-a") -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE summary_nodes(
                node_id INTEGER,
                session_id TEXT,
                depth INTEGER,
                summary TEXT,
                token_count INTEGER,
                source_token_count INTEGER,
                source_ids TEXT,
                source_type TEXT,
                created_at REAL,
                earliest_at REAL,
                latest_at REAL,
                expand_hint TEXT
            )
            """
        )
        conn.execute("CREATE TABLE messages(message_id INTEGER, body TEXT)")
        conn.execute("INSERT INTO messages VALUES(?,?)", (1, "raw message secret"))
        conn.execute(
            "INSERT INTO summary_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (node_id, session_id, 2, "Exact summary text.", 12, 34, '["m1"]', "message", 1.5, 1.0, 2.0, "expand here"),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
