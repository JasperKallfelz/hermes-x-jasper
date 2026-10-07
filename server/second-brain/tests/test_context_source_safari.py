from __future__ import annotations

import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_second_brain.context_sources.adapters.safari import SafariAdapter
from hermes_second_brain.context_sources.models import ScanRequest
from hermes_second_brain.context_sources.sqlite_snapshot import sqlite_snapshot


class SafariTests(unittest.TestCase):
    def test_history_bookmarks_strip_queries_and_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            history = root / "History.db"
            conn = sqlite3.connect(history)
            conn.executescript("CREATE TABLE history_items(id INTEGER PRIMARY KEY,url TEXT); CREATE TABLE history_visits(id INTEGER PRIMARY KEY,history_item INTEGER,visit_time REAL,title TEXT); INSERT INTO history_items VALUES(1,'https://example.com/page?token=secret#frag'); INSERT INTO history_visits VALUES(7,1,800000000,'Example');")
            conn.close()
            bookmarks = root / "Bookmarks.plist"
            bookmarks.write_bytes(plistlib.dumps({"Children": [{"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": "https://example.org/x?q=secret#x", "URIDictionary": {"title": "Saved"}}]}))
            adapter = SafariAdapter(history, bookmarks)
            first = adapter.scan(ScanRequest(), None)
            second = adapter.scan(ScanRequest(), first.cursor)
        serialized = str([item.payload for item in first.raw_items])
        self.assertNotIn("token=", serialized)
        self.assertNotIn("q=", serialized)
        self.assertNotIn("#frag", serialized)
        self.assertEqual(len(second.raw_items), 0)

    def test_snapshot_rejects_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "real.sqlite"
            sqlite3.connect(target).close()
            link = root / "link.sqlite"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                with sqlite_snapshot(link):
                    pass
