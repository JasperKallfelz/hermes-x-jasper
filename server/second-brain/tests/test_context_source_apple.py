from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from hermes_second_brain.context_sources.adapters.calendar import CalendarAdapter
from hermes_second_brain.context_sources.adapters.messages import MessagesAdapter
from hermes_second_brain.context_sources.adapters.notes import NotesMetadataAdapter
from hermes_second_brain.context_sources.models import ScanRequest
from hermes_second_brain.context_sources.registry import default_registry


class AppleSourceTests(unittest.TestCase):
    def test_calendar_is_read_only_redacted_and_paginated(self) -> None:
        calls = []
        value = [{"id": "event-1", "calendar_id": "work", "title": "Call me@example.com api_key=supersecretvalue eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturevalue", "start": "2026-07-22T10:00:00Z", "end": "2026-07-22T11:00:00Z", "location": "HQ", "notes": "do not retain", "url": "https://secret.example"}]
        def execute(argv, timeout):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        adapter = CalendarAdapter("fixture-cal", executor=execute)
        batch = adapter.scan(ScanRequest(limit=1), None)
        self.assertEqual(calls[0][0:3], ["fixture-cal", "--json", "list"])
        self.assertNotIn("notes", batch.raw_items[0].payload)
        self.assertNotIn("url", batch.raw_items[0].payload)
        self.assertNotIn("supersecretvalue", str(batch.raw_items[0].payload))
        self.assertNotIn("eyJhbGci", str(batch.raw_items[0].payload))
        self.assertNotEqual(batch.raw_items[0].payload["calendar_hash"], "work")

    def test_messages_snapshot_has_no_identity_or_attachment_export_and_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "chat.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
              CREATE TABLE message(ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,handle_id INTEGER,service TEXT,date INTEGER,is_from_me INTEGER,is_system_message INTEGER,is_empty INTEGER,attributedBody BLOB);
              CREATE TABLE chat_message_join(chat_id INTEGER,message_id INTEGER,message_date INTEGER);
              CREATE TABLE chat(ROWID INTEGER PRIMARY KEY,guid TEXT,display_name TEXT,chat_identifier TEXT,service_name TEXT);
              CREATE TABLE handle(ROWID INTEGER PRIMARY KEY,id TEXT);
              INSERT INTO handle VALUES(1,'person@example.com');
              INSERT INTO chat VALUES(2,'chat-guid','Private name','+49123456789','iMessage');
              INSERT INTO message VALUES(3,'message-guid','Hello person@example.com password=supersecretvalue eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturevalue',1,'iMessage',1000000000,0,0,0,X'00');
              INSERT INTO chat_message_join VALUES(2,3,1000000000);
            """)
            conn.close()
            adapter = MessagesAdapter(db)
            first = adapter.scan(ScanRequest(limit=1), None)
            second = adapter.scan(ScanRequest(limit=1), first.cursor)
        rendered = json.dumps([x.payload for x in first.raw_items]) + json.dumps([x.event for x in first.derivatives])
        self.assertNotIn("person@example.com", rendered)
        self.assertNotIn("49123456789", rendered)
        self.assertNotIn("supersecretvalue", rendered)
        self.assertNotIn("eyJhbGci", rendered)
        self.assertNotIn("attachment", rendered.lower())
        self.assertFalse(first.derivatives[0].export_allowed)
        self.assertEqual(len(second.raw_items), 0)

    def test_messages_rejects_symlink_and_schema_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); target = root / "real.db"; sqlite3.connect(target).close()
            link = root / "chat.db"; link.symlink_to(target)
            with self.assertRaises(ValueError):
                MessagesAdapter(link).scan(ScanRequest(), None)
            db = root / "old.db"; conn = sqlite3.connect(db)
            conn.executescript("CREATE TABLE message(ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,date INTEGER); INSERT INTO message VALUES(1,'g','hi',1);")
            conn.close()
            self.assertEqual(len(MessagesAdapter(db).scan(ScanRequest(), None).raw_items), 1)

    def test_messages_full_reconciliation_pages_without_restarting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "chat.db"; conn = sqlite3.connect(db)
            conn.executescript("CREATE TABLE message(ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,date INTEGER); INSERT INTO message VALUES(1,'one','first',1); INSERT INTO message VALUES(2,'two','second',2);")
            conn.close(); adapter = MessagesAdapter(db)
            first = adapter.scan(ScanRequest(full=True, limit=1), None)
            second = adapter.scan(ScanRequest(full=True, limit=1), first.cursor)
        self.assertEqual([x.external_id for x in first.raw_items], ["message:one"])
        self.assertEqual([x.external_id for x in second.raw_items], ["message:two"])

    def test_messages_periodically_reconciles_from_the_start(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "chat.db"; conn = sqlite3.connect(db)
            conn.executescript("CREATE TABLE message(ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,date INTEGER); INSERT INTO message VALUES(1,'one','first',1); INSERT INTO message VALUES(2,'two','second',2);")
            conn.close(); adapter = MessagesAdapter(db)
            stale = adapter.scan(ScanRequest(limit=1), None).cursor
            stale = stale.__class__({"rowid": 2, "reconciled_at": 0, "reconciling": False})
            refreshed = adapter.scan(ScanRequest(limit=1), stale)
        self.assertEqual([x.external_id for x in refreshed.raw_items], ["message:one"])
        self.assertTrue(refreshed.full_reconciliation)

    def test_messages_skips_system_and_empty_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "chat.db"; conn = sqlite3.connect(db)
            conn.executescript("CREATE TABLE message(ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,date INTEGER,is_system_message INTEGER,is_empty INTEGER); INSERT INTO message VALUES(1,'system','notice',1,1,0); INSERT INTO message VALUES(2,'empty','',2,0,1);")
            conn.close()
            batch = MessagesAdapter(db).scan(ScanRequest(), None)
        self.assertEqual(batch.raw_items, ())
        self.assertEqual(batch.skipped, 2)

    def test_default_registry_registers_apple_sources(self) -> None:
        self.assertTrue({"calendar", "messages", "notes"}.issubset(default_registry().names()))

    def test_notes_list_only_omits_secret_titles_and_never_exports(self) -> None:
        calls = []
        output = "All your notes:\n\n1. Work - Project update\n2. Notes - sk-" + "or-v1-" + "abcdefabcdefabcdef\n3. Notes - API key deployment\n"
        def execute(argv, timeout):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, output, "")
        batch = NotesMetadataAdapter("fixture-memo", executor=execute).scan(ScanRequest(), None)
        self.assertEqual(calls, [["fixture-memo", "notes", "--no-cache"]])
        self.assertEqual(len(batch.raw_items), 1)
        self.assertIn("Project update", batch.raw_items[0].payload["title"])
        self.assertNotIn("Project update", json.dumps(batch.derivatives[0].event))
        self.assertFalse(batch.derivatives[0].export_allowed)
