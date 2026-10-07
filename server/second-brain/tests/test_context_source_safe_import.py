from __future__ import annotations

import json
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from hermes_second_brain.cli import main
from hermes_second_brain.context_sources.adapters.safe_import import SafeImportAdapter, validate_record
from hermes_second_brain.context_sources.models import ScanRequest


class SafeImportTests(unittest.TestCase):
    def test_health_and_finance_are_schema_validated_and_minimized(self) -> None:
        health = validate_record("health", {"date": "2026-07-22", "metric": "steps", "value": 1234, "unit": "count"})
        finance = validate_record("banking", {"date": "2026-07-22", "amount": "12.34", "currency": "eur", "category": "food", "merchant": "Cafe Secret"})
        self.assertEqual(health["value"], 1234)
        self.assertNotIn("merchant", finance)
        self.assertIn("merchant_hash", finance)
        with self.assertRaises(ValueError):
            validate_record("health", {"date": "2026-07-22", "metric": "run", "value": 1, "latitude": 52.5})
        with self.assertRaises(ValueError):
            validate_record("banking", {"date": "2026-07-22", "amount": 1, "currency": "EUR", "iban": "DE89370400440532013000"})

    def test_file_cursor_zip_traversal_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "health.json"
            source.write_text(json.dumps([{"id": "a", "date": "2026-07-22", "metric": "steps", "value": 10}]), encoding="utf-8")
            adapter = SafeImportAdapter("health", source, "json")
            first = adapter.scan(ScanRequest(), None)
            second = adapter.scan(ScanRequest(), first.cursor)
            self.assertEqual(len(first.raw_items), 1)
            self.assertEqual(len(second.raw_items), 0)
            link = root / "link.json"; link.symlink_to(source)
            with self.assertRaises(ValueError):
                SafeImportAdapter("health", link, "json").scan(ScanRequest(), None)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf: zf.writestr("../escape.json", "[]")
            with self.assertRaises(ValueError):
                SafeImportAdapter("health", archive, "zip").scan(ScanRequest(), None)

    def test_cli_dry_runs_create_no_vaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "health.json"
            source.write_text(json.dumps([{"date": "2026-07-22", "metric": "steps", "value": 10}]), encoding="utf-8")
            source_db = root / "source.sqlite3"
            inbox_db = root / "inbox.sqlite3"
            with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                code = main(["context-import-export", "--source", "health", "--path", str(source), "--format", "json", "--source-db", str(source_db), "--db", str(inbox_db), "--dry-run", "--json"])
            self.assertEqual(code, 0)
            self.assertFalse(source_db.exists())
            self.assertFalse(inbox_db.exists())
            self.assertNotIn(str(source), output.getvalue())
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["context-retention", "--source-db", str(source_db), "--dry-run", "--json"]), 0)
            self.assertFalse(source_db.exists())
