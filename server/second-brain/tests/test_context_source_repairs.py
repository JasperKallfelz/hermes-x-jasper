from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from hermes_second_brain.context_inbox import ContextInbox
from hermes_second_brain.context_sources.adapters.composio import NotionAdapter
from hermes_second_brain.context_sources.adapters.contacts import ContactsAdapter
from hermes_second_brain.context_sources.adapters.safe_import import SafeImportAdapter, validate_record
from hermes_second_brain.context_sources.adapters.whatsapp import WhatsAppAdapter
from hermes_second_brain.context_sources.models import Derivative, RawItem, ScanBatch, ScanRequest, Sensitivity, SourceCursor, SourceHealth, SourceStatus
from hermes_second_brain.context_sources.registry import SourceRegistry, default_registry
from hermes_second_brain.context_sources.runner import SourceRunner
from hermes_second_brain.context_sources.store import SourceStore


def health_record(identifier: str, value: int) -> dict[str, object]:
    return {"id": identifier, "date": "2026-07-22", "metric": "steps", "value": value}


class ContinuousIngressTests(unittest.TestCase):
    def test_private_ingress_discovers_hashes_and_resumes_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ingress"
            adapter = SafeImportAdapter.ingress_adapter("health", root)
            self.assertEqual(adapter.health(ScanRequest()).status, SourceStatus.HEALTHY)
            source_dir = root / "health"
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(source_dir).st_mode & 0o777, 0o700)

            staged = source_dir / ".export.tmp"
            staged.write_text(json.dumps([health_record("one", 1), health_record("two", 2)]), encoding="utf-8")
            final = source_dir / "export.json"
            os.replace(staged, final)
            original = final.read_bytes()

            first = adapter.scan(ScanRequest(limit=1, full=True), None)
            self.assertFalse(first.scan_complete)
            self.assertTrue(first.full_reconciliation)
            self.assertEqual(first.cursor.value["active"]["record_index"], 0)
            second = adapter.scan(ScanRequest(limit=1), first.cursor)
            self.assertEqual([item.external_id for item in second.raw_items], ["two"])
            self.assertFalse(second.scan_complete)
            third = adapter.scan(ScanRequest(limit=1), second.cursor)
            self.assertTrue(third.scan_complete)
            self.assertEqual(third.raw_items, ())
            self.assertEqual(final.read_bytes(), original)

            duplicate = source_dir / "duplicate.json"
            duplicate.write_bytes(original)
            unchanged = adapter.scan(ScanRequest(limit=10), third.cursor)
            self.assertEqual(unchanged.raw_items, ())
            (source_dir / "later.json").write_text(json.dumps([health_record("three", 3)]), encoding="utf-8")
            later = adapter.scan(ScanRequest(limit=10), unchanged.cursor)
            self.assertEqual([item.external_id for item in later.raw_items], ["three"])

    def test_zip_cursor_tracks_member_and_record_without_completing_at_limit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "health.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("a.json", json.dumps([health_record("one", 1)]))
                handle.writestr("b.json", json.dumps([health_record("two", 2)]))
            adapter = SafeImportAdapter("health", archive, "zip")
            first = adapter.scan(ScanRequest(limit=1), None)
            second = adapter.scan(ScanRequest(limit=1), first.cursor)
            self.assertFalse(first.scan_complete)
            self.assertEqual(first.cursor.value["active"], {"content_hash": first.cursor.value["active"]["content_hash"], "member_index": 0, "record_index": 0})
            self.assertEqual(second.cursor.value["active"]["member_index"], 1)
            self.assertEqual([item.external_id for item in second.raw_items], ["two"])


class WhatsAppWalTests(unittest.TestCase):
    def test_unchanged_main_with_grown_wal_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ChatStorage.sqlite"
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            adapter = WhatsAppAdapter(path)
            first = adapter.scan(ScanRequest(), None)
            main_before = (path.stat().st_mtime_ns, path.stat().st_size)
            conn.execute("INSERT INTO sample(value) VALUES('committed in wal')")
            conn.commit()
            main_after = (path.stat().st_mtime_ns, path.stat().st_size)
            second = adapter.scan(ScanRequest(), first.cursor)
            conn.close()
        self.assertEqual(main_before, main_after)
        self.assertEqual(len(second.raw_items), 1)


class ReconciliationAdapter:
    source_name = "reconcile-fixture"
    sensitivity = Sensitivity.PERSONAL
    retention = (30, 90)

    def __init__(self) -> None:
        self.phase = 0

    def health(self, request: ScanRequest) -> SourceHealth:
        return SourceHealth(self.source_name, SourceStatus.HEALTHY)

    @staticmethod
    def item(identifier: str, body: str) -> tuple[RawItem, Derivative]:
        event = {
            "platform": "reconcile-fixture",
            "conversation_id": "tasks",
            "source_message_id": identifier,
            "message_ts": "2026-07-22T10:00:00Z",
            "direction": "inbound",
            "body": body,
            "raw": {"source": "reconcile-fixture", "redaction_version": "v1"},
        }
        return RawItem(identifier, {"summary": "bounded"}, event["message_ts"]), Derivative(identifier, event)

    def scan(self, request: ScanRequest, cursor: SourceCursor | None) -> ScanBatch:
        self.phase += 1
        if self.phase == 1:
            pairs = [self.item("keep", "Status update"), self.item("remove", "Please call tomorrow")]
            return ScanBatch(tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs), SourceCursor({"phase": 1}), full_reconciliation=True)
        if self.phase == 2:
            pair = self.item("keep", "Status update")
            return ScanBatch((pair[0],), (pair[1],), SourceCursor({"phase": 2}), full_reconciliation=True, scan_complete=False)
        return ScanBatch(cursor=SourceCursor({"phase": 3}), full_reconciliation=True, scan_complete=True)


class ThrowingHealthAdapter(ReconciliationAdapter):
    source_name = "throwing-health"

    def health(self, request: ScanRequest) -> SourceHealth:
        raise RuntimeError("secret /private/path token=never-publish")


class ReconciliationAndHealthTests(unittest.TestCase):
    def test_tombstone_waits_for_complete_generation_and_preserves_open_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adapter = ReconciliationAdapter()
            runner = SourceRunner(SourceRegistry([adapter]), source_db=root / "sources.sqlite3", inbox_db=root / "inbox.sqlite3")
            runner.scan_one(adapter.source_name)
            inbox = ContextInbox(root / "inbox.sqlite3")
            reminder = next(item for item in inbox.extract_reminders() if "call" in item["text"].lower())
            inbox.set_reminder_status(reminder["reminder_id"], "later")

            runner.scan_one(adapter.source_name)
            remove_event = next(row for row in inbox.list_events() if row["source_message_id"] == "remove")
            self.assertEqual(remove_event["source_tombstoned"], 0)

            runner.scan_one(adapter.source_name)
            remove_event = next(row for row in inbox.list_events() if row["source_message_id"] == "remove")
            self.assertEqual(remove_event["source_tombstoned"], 1)
            self.assertGreaterEqual(remove_event["source_generation"], 2)
            self.assertEqual(inbox.get_reminder(reminder["reminder_id"])["status"], "later")

    def test_throwing_health_is_isolated_for_scan_and_probe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            healthy = ReconciliationAdapter()
            registry = SourceRegistry([ThrowingHealthAdapter(), healthy])
            runner = SourceRunner(registry, source_db=root / "sources.sqlite3", inbox_db=root / "inbox.sqlite3")
            results = runner.scan(("throwing-health", "reconcile-fixture"))
            probe = runner.probe(("throwing-health", "reconcile-fixture"), persist=True)
            persisted = SourceStore(root / "sources.sqlite3").health("throwing-health")[0]
        self.assertEqual([item.status for item in results], [SourceStatus.ERROR, SourceStatus.HEALTHY])
        self.assertEqual(probe[0]["reason_code"], "runtimeerror")
        self.assertEqual(persisted["status"], "error")


class SchemaAndProviderTests(unittest.TestCase):
    def test_schema_rejects_composites_nonfinite_and_sensitive_values_and_redacts_text(self) -> None:
        invalid = [
            {"date": "2026-07-22", "metric": "steps", "value": []},
            {"date": "2026-07-22", "metric": "steps", "value": float("nan")},
            {"date": "2026-07-22", "metric": "diagnosis: flu", "value": 1},
            {"date": "2026-07-22", "metric": "home location Berlin", "value": 1},
        ]
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_record("health", record)
        valid = validate_record("health", {"date": "2026-07-22", "metric": "steps token=supersecretvalue me@example.com", "value": 1})
        serialized = json.dumps(valid)
        self.assertNotIn("supersecretvalue", serialized)
        self.assertNotIn("me@example.com", serialized)

    def test_authorized_native_contacts_and_configured_transports_are_healthy(self) -> None:
        class FakeNative:
            def authorized(self):
                return True

            def __call__(self):
                return [{"identifier": "native", "emails": [], "phones": []}]

        framework = object()
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.dict(sys.modules, {"Contacts": framework}):
            contacts = ContactsAdapter(native_provider_factory=lambda _: FakeNative())
            self.assertEqual(contacts.health(ScanRequest()).status, SourceStatus.HEALTHY)
            self.assertEqual(len(contacts.scan(ScanRequest(), None).raw_items), 1)

        class Transport:
            def execute(self, tool, arguments):
                return {"items": []}

        transport = Transport()
        self.assertEqual(NotionAdapter(transport=transport).health(ScanRequest()).status, SourceStatus.HEALTHY)
        registry = default_registry(transports={"notion": transport}, ingress_dir=Path(tempfile.gettempdir()) / "unused-ingress")
        self.assertEqual(registry.get("notion").health(ScanRequest()).status, SourceStatus.HEALTHY)
        self.assertEqual(registry.get("slack").health(ScanRequest()).status, SourceStatus.PENDING_PERMISSION)


if __name__ == "__main__":
    unittest.main()
