from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from hermes_second_brain.context_sources.models import Derivative, RawItem, ScanBatch, SourceCursor
from hermes_second_brain.context_sources.store import CursorConflictError, SourceStore


class ContextSourceStoreTests(unittest.TestCase):
    def test_idempotent_batch_cursor_and_private_modes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "private" / "sources.sqlite3"
            store = SourceStore(db)
            batch = ScanBatch(
                (RawItem("one", {"value": 1}, "2026-07-22T10:00:00Z"),),
                (Derivative("one", {"platform": "test", "source_message_id": "one", "body": "safe", "raw": {"source": "test"}}),),
                SourceCursor({"n": 1}),
            )
            first = store.commit_batch("test", "personal", batch, started_at=time.time(), retention=(30, 90))
            second = store.commit_batch("test", "personal", batch, started_at=time.time(), retention=(30, 90))
            counts = store.counts()
            cursor = store.cursor("test")
            modes = (os.stat(db.parent).st_mode & 0o777, os.stat(db).st_mode & 0o777)
        self.assertEqual(first["stored"], 1)
        self.assertEqual(second["stored"], 0)
        self.assertEqual(counts["raw_items"], 1)
        self.assertEqual(counts["derivatives"], 1)
        self.assertEqual(cursor, SourceCursor({"n": 1}))
        self.assertEqual(modes, (0o700, 0o600))

    def test_cursor_rolls_back_with_invalid_payload_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = SourceStore(Path(td) / "sources.sqlite3")
            good = ScanBatch((RawItem("old", {"ok": True}, "", expires_at=1),), (), SourceCursor({"n": 1}))
            store.commit_batch("test", "personal", good, started_at=time.time(), retention=(1, 1))
            bad = ScanBatch((RawItem("bad", {"bad": object()}, ""),), (), SourceCursor({"n": 2}))
            with self.assertRaises(TypeError):
                store.commit_batch("test", "personal", bad, started_at=time.time(), retention=(1, 1))
            self.assertEqual(store.cursor("test"), SourceCursor({"n": 1}))
            preview = store.retention(dry_run=True, now=2)
            self.assertEqual(store.counts()["raw_items"], 1)
            removed = store.retention(now=2)
            self.assertEqual(store.counts()["raw_items"], 0)
        self.assertEqual(preview["raw_items"], 1)
        self.assertEqual(removed["raw_items"], 1)

    def test_vault_and_sidecar_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.write_text("do not alter", encoding="utf-8")
            (root / "sources.sqlite3").symlink_to(target)
            with self.assertRaises(ValueError):
                SourceStore(root / "sources.sqlite3")
            self.assertEqual(target.read_text(encoding="utf-8"), "do not alter")

    def test_cursor_compare_and_swap_rejects_stale_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = SourceStore(Path(td) / "sources.sqlite3")
            first = ScanBatch(cursor=SourceCursor({"n": 1}))
            store.commit_batch("test", "personal", first, started_at=time.time(), retention=(1, 1), expected_cursor=None)
            stale = ScanBatch(cursor=SourceCursor({"n": 2}))
            with self.assertRaises(CursorConflictError):
                store.commit_batch("test", "personal", stale, started_at=time.time(), retention=(1, 1), expected_cursor=None)
            self.assertEqual(store.cursor("test"), SourceCursor({"n": 1}))

    def test_retention_preview_of_missing_vault_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "missing.sqlite3"
            result = SourceStore.retention_preview(db)
            self.assertFalse(db.exists())
        self.assertEqual(result["raw_items"], 0)

    def test_non_relevant_derivatives_never_enter_publish_queue(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = SourceStore(Path(td) / "sources.sqlite3")
            batch = ScanBatch(
                (RawItem("passive", {"kind": "history"}, ""),),
                (Derivative("passive", {"platform": "test", "body": "passive", "raw": {"source": "test"}}, export_allowed=False),),
                SourceCursor({"n": 1}),
            )
            store.commit_batch("test", "personal", batch, started_at=time.time(), retention=(1, 1))
            self.assertEqual(store.pending_derivatives("test"), [])
