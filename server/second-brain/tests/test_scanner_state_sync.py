from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from hermes_second_brain.manifest import load_manifest
from hermes_second_brain.scanner import ScannedResource, scan_source
from hermes_second_brain.state import State
from hermes_second_brain.sync import openviking_lease_seconds, sync_manifest

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
FAKE_OV = TESTS / "fake_ov.py"


def write_manifest(tmp_path: Path, corpus: Path, fake_ov: Path = FAKE_OV) -> Path:
    manifest = {
        "state_db": str(tmp_path / "state.sqlite3"),
        "ov_binary": str(fake_ov),
        "ov_timeout_seconds": 5,
        "retry_attempts": 2,
        "retry_backoff_seconds": 0.01,
        "concurrency": 1,
        "sources": [{"id": "fixtures", "root": str(corpus), "namespace": "brain"}],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class ScannerStateSyncTests(unittest.TestCase):
    def test_scanner_excludes_secret_and_binary_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            for name in (
                "demo_project.md",
                "authentication-design.md",
                "token.txt",
                "api-token.txt",
                "password.txt",
                "private_key.pem.txt",
                "client.oauth.md",
                "browser_cookie.txt",
                "service-credential.md",
                "secret-token.txt",
                "auth_token.txt",
                "access.key.md",
            ):
                (corpus / name).write_text(name, encoding="utf-8")
            (corpus / ".env").write_text("SECRET=do-not-index", encoding="utf-8")
            (corpus / "safe-looking.md").symlink_to(corpus / ".env")
            manifest = load_manifest(write_manifest(tmp_path, corpus))
            resources = scan_source(manifest.sources[0])
        rels = {r.relative_path for r in resources}
        self.assertIn("demo_project.md", rels)
        self.assertIn("authentication-design.md", rels)
        self.assertNotIn("token.txt", rels)
        self.assertNotIn("api-token.txt", rels)
        self.assertNotIn("password.txt", rels)
        self.assertNotIn("private_key.pem.txt", rels)
        self.assertNotIn("client.oauth.md", rels)
        self.assertNotIn("browser_cookie.txt", rels)
        self.assertNotIn("service-credential.md", rels)
        self.assertNotIn("secret-token.txt", rels)
        self.assertNotIn("auth_token.txt", rels)
        self.assertNotIn("access.key.md", rels)
        self.assertNotIn("safe-looking.md", rels)

    def test_load_manifest_rejects_duplicate_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"id": "one", "root": str(tmp_path), "namespace": "brain"},
                            {"id": "two", "root": str(tmp_path), "namespace": "brain"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate source namespace 'brain'"):
                load_manifest(manifest_path)

    def test_sync_is_resumable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            manifest_path = write_manifest(tmp_path, FIXTURES / "corpus")
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["retry_attempts"] = 1
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            old_state = os.environ.get("FAKE_OV_STATE")
            old_fail = os.environ.get("FAKE_OV_FAIL_ONCE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            os.environ["FAKE_OV_FAIL_ONCE"] = "1"
            try:
                summary = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(summary.failed, 1)
                self.assertEqual(summary.synced, 2)
                second = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(second.enqueued, 1)
                self.assertEqual(second.synced, 1)
                third = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(third.enqueued, 0)
                self.assertEqual(third.synced, 0)
                fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
                self.assertEqual(len(fake_state["resources"]), 3)
                add_commands = [cmd for cmd in fake_state["commands"] if cmd[:1] == ["add-resource"]]
                self.assertEqual(len(add_commands), 4)
            finally:
                _restore_env("FAKE_OV_STATE", old_state)
                _restore_env("FAKE_OV_FAIL_ONCE", old_fail)

    def test_deletion_is_local_marker_not_remote_delete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            doc = corpus / "doc.md"
            doc.write_text("remember me", encoding="utf-8")
            manifest_path = write_manifest(tmp_path, corpus)
            old_state = os.environ.get("FAKE_OV_STATE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            try:
                first = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(first.synced, 1)
                doc.unlink()
                second = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(second.deleted_detected, 1)
                self.assertEqual(second.synced, 0)
            finally:
                _restore_env("FAKE_OV_STATE", old_state)
            conn = sqlite3.connect(tmp_path / "state.sqlite3")
            try:
                statuses = [r[0] for r in conn.execute("SELECT status FROM resources")]
            finally:
                conn.close()
            self.assertEqual(statuses, ["deleted"])
            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            self.assertFalse(any(cmd[:1] == ["rm"] for cmd in fake_state["commands"]))

    def test_missing_source_root_marks_only_that_source_deleted_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            missing = tmp_path / "missing"
            present = tmp_path / "present"
            missing.mkdir()
            present.mkdir()
            (missing / "gone.md").write_text("gone", encoding="utf-8")
            (present / "kept.md").write_text("kept", encoding="utf-8")
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "state_db": str(tmp_path / "state.sqlite3"),
                        "ov_binary": str(FAKE_OV),
                        "ov_timeout_seconds": 5,
                        "retry_attempts": 1,
                        "concurrency": 1,
                        "sources": [
                            {"id": "missing", "root": str(missing), "namespace": "missing"},
                            {"id": "present", "root": str(present), "namespace": "present"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            old_state = os.environ.get("FAKE_OV_STATE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            try:
                self.assertEqual(sync_manifest(load_manifest(manifest_path)).synced, 2)
                for path in missing.iterdir():
                    path.unlink()
                missing.rmdir()
                (present / "new.md").write_text("new", encoding="utf-8")
                second = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(second.deleted_detected, 1)
                self.assertEqual(second.synced, 1)
            finally:
                _restore_env("FAKE_OV_STATE", old_state)
            conn = sqlite3.connect(tmp_path / "state.sqlite3")
            try:
                rows = dict(conn.execute("SELECT relative_path,status FROM resources WHERE source_root_id='missing'"))
            finally:
                conn.close()
            self.assertEqual(rows, {"gone.md": "deleted"})
            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            self.assertFalse(any(cmd[:1] == ["rm"] for cmd in fake_state["commands"]))

    def test_changed_text_container_preserves_uri_and_replaces_exact_resource(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            doc = corpus / "doc.txt"
            doc.write_text("one", encoding="utf-8")
            manifest_path = write_manifest(tmp_path, corpus)
            old_state = os.environ.get("FAKE_OV_STATE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            try:
                self.assertEqual(sync_manifest(load_manifest(manifest_path)).synced, 1)
                doc.write_text("two", encoding="utf-8")
                self.assertEqual(sync_manifest(load_manifest(manifest_path)).synced, 1)
            finally:
                _restore_env("FAKE_OV_STATE", old_state)
            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            commands = fake_state["commands"]
            self.assertEqual(len([cmd for cmd in commands if cmd[:1] == ["add-resource"]]), 2)
            rm_commands = [cmd for cmd in commands if cmd[:1] == ["rm"]]
            self.assertEqual(len(rm_commands), 1)
            self.assertRegex(rm_commands[0][1], r"^viking://resources/brain/.+\.txt$")
            self.assertFalse(rm_commands[0][1].endswith("/"))
            self.assertNotIn("*", rm_commands[0][1])
            self.assertIn("--recursive", rm_commands[0])
            self.assertLess(rm_commands[0].index("--recursive"), rm_commands[0].index("--wait"))
            write_commands = [cmd for cmd in commands if cmd[:1] == ["write"]]
            self.assertEqual(write_commands, [])
            conn = sqlite3.connect(tmp_path / "state.sqlite3")
            try:
                row = conn.execute("SELECT status, ov_resource_id FROM resources").fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], "synced")
            self.assertEqual(row[1], rm_commands[0][1])

    def test_changed_pdf_removes_only_exact_existing_uri_then_readds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            doc = corpus / "doc.pdf"
            doc.write_bytes(b"%PDF-1")
            manifest_path = write_manifest(tmp_path, corpus)
            old_state = os.environ.get("FAKE_OV_STATE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            try:
                self.assertEqual(sync_manifest(load_manifest(manifest_path)).synced, 1)
                doc.write_bytes(b"%PDF-2")
                self.assertEqual(sync_manifest(load_manifest(manifest_path)).synced, 1)
            finally:
                _restore_env("FAKE_OV_STATE", old_state)
            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            rm_commands = [cmd for cmd in fake_state["commands"] if cmd[:1] == ["rm"]]
            self.assertEqual(len(rm_commands), 1)
            self.assertRegex(rm_commands[0][1], r"^viking://resources/brain/.+\.pdf$")
            self.assertFalse(rm_commands[0][1].endswith("/"))
            self.assertNotIn("*", rm_commands[0][1])
            self.assertIn("--recursive", rm_commands[0])
            self.assertLess(rm_commands[0].index("--recursive"), rm_commands[0].index("--wait"))
            self.assertEqual(fake_state["removed"], [rm_commands[0][1]])

    def test_batch_wait_failure_keeps_enqueue_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            (corpus / "doc.md").write_text("one", encoding="utf-8")
            manifest_path = write_manifest(tmp_path, corpus)
            old_state = os.environ.get("FAKE_OV_STATE")
            old_wait_fail = os.environ.get("FAKE_OV_WAIT_FAIL")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            os.environ["FAKE_OV_WAIT_FAIL"] = "1"
            try:
                first = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(first.synced, 0)
                self.assertEqual(first.failed, 1)
            finally:
                _restore_env("FAKE_OV_WAIT_FAIL", old_wait_fail)
                _restore_env("FAKE_OV_STATE", old_state)
            conn = sqlite3.connect(tmp_path / "state.sqlite3")
            try:
                row = conn.execute("SELECT status, attempts FROM resources").fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("failed", 1))

    def test_sync_can_mark_accepted_resources_without_global_index_wait(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            (corpus / "doc.md").write_text("one", encoding="utf-8")
            manifest_path = write_manifest(tmp_path, corpus)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["wait_for_indexing"] = False
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            old_state = os.environ.get("FAKE_OV_STATE")
            old_wait_fail = os.environ.get("FAKE_OV_WAIT_FAIL")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            os.environ["FAKE_OV_WAIT_FAIL"] = "1"
            try:
                summary = sync_manifest(load_manifest(manifest_path))
            finally:
                _restore_env("FAKE_OV_WAIT_FAIL", old_wait_fail)
                _restore_env("FAKE_OV_STATE", old_state)
            self.assertEqual((summary.synced, summary.failed), (1, 0))
            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            self.assertFalse(any(cmd[:1] == ["wait"] for cmd in fake_state["commands"]))

    def test_dry_run_does_not_enqueue_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            manifest_path = write_manifest(tmp_path, FIXTURES / "corpus")
            summary = sync_manifest(load_manifest(manifest_path), dry_run=True)
            self.assertEqual(summary.enqueued, 3)
            self.assertEqual(State(tmp_path / "state.sqlite3").pending(), [])

    def test_state_claims_are_atomic_and_lease_recovery_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state = State(tmp_path / "state.sqlite3")
            resource = _resource(tmp_path, "doc.md", "one")
            state.upsert_scan([resource], "root")
            first = state.claim_pending(lease_seconds=60, owner="worker-a")
            self.assertEqual([row.source_id for row in first], [resource.source_id])
            state.upsert_scan([resource], "root")
            self.assertEqual(state.claim_pending(lease_seconds=60, owner="worker-b"), [])
            with state.transaction() as conn:
                conn.execute("UPDATE resources SET lease_until=? WHERE source_id=?", (time.time() - 1, resource.source_id))
            recovered = state.claim_pending(lease_seconds=60, owner="worker-b")
            self.assertEqual([row.source_id for row in recovered], [resource.source_id])
            self.assertEqual(recovered[0].lease_owner, "worker-b")

    def test_state_recovers_legacy_ownerless_in_progress_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state = State(tmp_path / "state.sqlite3")
            resource = _resource(tmp_path, "doc.md", "one")
            state.upsert_scan([resource], "root")
            with state.transaction() as conn:
                conn.execute(
                    "UPDATE resources SET status='in_progress', lease_until=NULL, lease_owner=NULL WHERE source_id=?",
                    (resource.source_id,),
                )
            recovered = state.claim_pending(lease_seconds=60, owner="worker-b")
            self.assertEqual([row.source_id for row in recovered], [resource.source_id])
            self.assertEqual(recovered[0].lease_owner, "worker-b")

    def test_state_mark_synced_and_failed_are_compare_and_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state = State(tmp_path / "state.sqlite3")
            old = _resource(tmp_path, "doc.md", "one")
            state.upsert_scan([old], "root")
            claimed = state.claim_pending(owner="worker")[0]
            new = _resource(tmp_path, "doc.md", "two")
            state.upsert_scan([new], "root")
            self.assertFalse(state.mark_synced(claimed.source_id, claimed.sha256, "viking://resources/brain/old.md", owner="worker"))
            self.assertFalse(state.mark_failed(claimed.source_id, claimed.sha256, "stale", owner="worker"))
            fresh = state.claim_pending(owner="worker-2")[0]
            self.assertEqual(fresh.sha256, new.sha256)
            self.assertFalse(state.mark_failed(fresh.source_id, fresh.sha256, "wrong owner", owner="worker-x"))
            self.assertTrue(state.mark_failed(fresh.source_id, fresh.sha256, "retry", owner="worker-2"))
            retry = state.claim_pending(owner="worker-3")[0]
            self.assertEqual(retry.attempts, 1)
            self.assertFalse(state.mark_synced(retry.source_id, retry.sha256, "viking://resources/brain/doc.md", owner="worker-x"))
            self.assertTrue(state.mark_synced(retry.source_id, retry.sha256, "viking://resources/brain/doc.md", owner="worker-3"))

    def test_claim_pending_is_bounded_and_can_exclude_processed_failures(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state = State(tmp_path / "state.sqlite3")
            resources = [_resource(tmp_path, f"doc-{idx}.md", f"body {idx}", source_id=f"brain:doc-{idx}") for idx in range(3)]
            state.upsert_scan(resources, "root")
            first = state.claim_pending(limit=2, owner="worker", exclude_source_ids=set())
            self.assertEqual(len(first), 2)
            self.assertEqual({row.lease_owner for row in first}, {"worker"})
            for row in first:
                self.assertTrue(state.mark_failed(row.source_id, row.sha256, "fail", owner="worker"))
            second = state.claim_pending(limit=2, owner="worker", exclude_source_ids={row.source_id for row in first})
            self.assertEqual(len(second), 1)
            self.assertEqual(second[0].source_id, resources[2].source_id)

    def test_sync_failed_row_is_not_retried_in_same_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            (corpus / "a.md").write_text("a", encoding="utf-8")
            (corpus / "b.md").write_text("b", encoding="utf-8")
            manifest_path = write_manifest(tmp_path, corpus)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["retry_attempts"] = 1
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            old_state = os.environ.get("FAKE_OV_STATE")
            old_fail = os.environ.get("FAKE_OV_FAIL_ONCE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            os.environ["FAKE_OV_FAIL_ONCE"] = "1"
            try:
                first = sync_manifest(load_manifest(manifest_path))
            finally:
                _restore_env("FAKE_OV_STATE", old_state)
                _restore_env("FAKE_OV_FAIL_ONCE", old_fail)
            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            add_commands = [cmd for cmd in fake_state["commands"] if cmd[:1] == ["add-resource"]]
            wait_commands = [cmd for cmd in fake_state["commands"] if cmd[:1] == ["wait"]]
            self.assertEqual(first.failed, 1)
            self.assertEqual(first.synced, 1)
            self.assertEqual(len(add_commands), 2)
            self.assertEqual(len(wait_commands), 1)

    def test_sync_claims_only_active_manifest_source_roots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            current = tmp_path / "mail-bundles"
            legacy = tmp_path / "important-mail"
            current.mkdir()
            legacy.mkdir()
            (current / "mail-context-0.md").write_text("current bundle", encoding="utf-8")
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "state_db": str(tmp_path / "state.sqlite3"),
                        "ov_binary": str(FAKE_OV),
                        "ov_timeout_seconds": 5,
                        "retry_attempts": 1,
                        "concurrency": 2,
                        "sources": [
                            {
                                "id": "mail-context-bundles",
                                "root": str(current),
                                "namespace": "mail",
                                "include_extensions": [".md"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            old_state = os.environ.get("FAKE_OV_STATE")
            os.environ["FAKE_OV_STATE"] = str(tmp_path / "ov.json")
            try:
                first = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(first.synced, 1)
                fake_state_path = tmp_path / "ov.json"
                fake_state = json.loads(fake_state_path.read_text(encoding="utf-8"))
                fake_state["commands"] = []
                fake_state_path.write_text(json.dumps(fake_state), encoding="utf-8")

                state = State(tmp_path / "state.sqlite3")
                pending = _resource(legacy, "pending.md", "pending", source_root_id="important-mail", source_id="legacy:pending", namespace="mail")
                failed = _resource(legacy, "failed.md", "failed", source_root_id="important-mail", source_id="legacy:failed", namespace="mail")
                expired = _resource(legacy, "expired.md", "expired", source_root_id="important-mail", source_id="legacy:expired", namespace="mail")
                state.upsert_scan([pending, failed, expired], "important-mail")
                with state.transaction() as conn:
                    conn.execute("UPDATE resources SET status='failed', attempts=1 WHERE source_id=?", (failed.source_id,))
                    conn.execute(
                        "UPDATE resources SET status='in_progress', lease_until=?, lease_owner='stale-worker' WHERE source_id=?",
                        (time.time() - 1, expired.source_id),
                    )

                second = sync_manifest(load_manifest(manifest_path))
                self.assertEqual(second.synced, 0)
                self.assertEqual(second.failed, 0)
            finally:
                _restore_env("FAKE_OV_STATE", old_state)

            fake_state = json.loads((tmp_path / "ov.json").read_text(encoding="utf-8"))
            self.assertEqual([cmd for cmd in fake_state["commands"] if cmd[:1] == ["add-resource"]], [])
            self.assertFalse(any(cmd[:1] == ["rm"] for cmd in fake_state["commands"]))
            conn = sqlite3.connect(tmp_path / "state.sqlite3")
            try:
                rows = dict(conn.execute("SELECT source_id,status FROM resources WHERE source_root_id='important-mail' ORDER BY source_id"))
                lease = conn.execute("SELECT status, lease_owner FROM resources WHERE source_id=?", (expired.source_id,)).fetchone()
            finally:
                conn.close()
            self.assertEqual(rows, {"legacy:expired": "in_progress", "legacy:failed": "failed", "legacy:pending": "pending"})
            self.assertEqual(lease, ("in_progress", "stale-worker"))

    def test_claim_pending_empty_allowed_source_root_set_claims_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state = State(tmp_path / "state.sqlite3")
            resource = _resource(tmp_path, "doc.md", "one")
            state.upsert_scan([resource], "root")
            claimed = state.claim_pending(owner="worker", allowed_source_root_ids=set())
            self.assertEqual(claimed, [])
            self.assertEqual([row.source_id for row in state.pending()], [resource.source_id])

    def test_openviking_lease_formula_exceeds_command_chain_bound(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "state_db": str(tmp_path / "state.sqlite3"),
                        "ov_binary": str(FAKE_OV),
                        "ov_timeout_seconds": 17,
                        "retry_attempts": 4,
                        "retry_backoff_seconds": 0.5,
                        "concurrency": 3,
                        "sources": [{"id": "fixtures", "root": str(FIXTURES / "corpus"), "namespace": "brain"}],
                    }
                ),
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path)
            run_bound = (17 * 4) + (0.5 * (1 + 2 + 3))
            full_chain = (2 * 17) + (5 * run_bound)
            self.assertGreater(openviking_lease_seconds(manifest), full_chain)

    def test_state_migrates_existing_database_with_lease_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.sqlite3"
            conn = sqlite3.connect(db)
            try:
                conn.executescript(
                    """
                    CREATE TABLE resources (
                      source_id TEXT PRIMARY KEY, source_root_id TEXT NOT NULL, namespace TEXT NOT NULL,
                      path TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
                      size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, status TEXT NOT NULL,
                      ov_resource_id TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                      first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_synced REAL
                    );
                    """
                )
            finally:
                conn.close()
            State(db)
            conn = sqlite3.connect(db)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(resources)")}
            finally:
                conn.close()
            self.assertIn("lease_until", columns)
            self.assertIn("lease_owner", columns)


def _restore_env(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def _resource(tmp_path: Path, name: str, content: str, source_id: str = "brain:doc", source_root_id: str = "root", namespace: str = "brain") -> ScannedResource:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    stat = path.stat()
    import hashlib

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ScannedResource(
        source_root_id=source_root_id,
        source_id=source_id,
        namespace=namespace,
        path=path,
        relative_path=name,
        sha256=digest,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


if __name__ == "__main__":
    unittest.main()
