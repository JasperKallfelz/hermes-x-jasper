from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_second_brain.context_inbox import ContextInbox
from hermes_second_brain.context_sources.models import Derivative, RawItem, ScanBatch, ScanRequest, Sensitivity, SourceCursor, SourceHealth, SourceStatus
from hermes_second_brain.context_sources.registry import SourceRegistry
from hermes_second_brain.context_sources.runner import SourceRunner
from hermes_second_brain.context_sources.store import SourceStore


class FixtureAdapter:
    source_name = "fixture"
    sensitivity = Sensitivity.PERSONAL
    retention = (1, 2)

    def health(self, request: ScanRequest) -> SourceHealth:
        return SourceHealth(self.source_name, SourceStatus.HEALTHY)

    def scan(self, request: ScanRequest, cursor: SourceCursor | None) -> ScanBatch:
        number = int((cursor.value if cursor else {}).get("n", 0)) + 1
        return ScanBatch(
            (RawItem(str(number), {"summary": "minimal"}, "2026-07-22T10:00:00Z"),),
            (Derivative(str(number), {"platform": "fixture", "conversation_id": "c", "source_message_id": str(number), "message_ts": "2026-07-22T10:00:00Z", "body": "redacted", "raw": {"source": "fixture", "redaction_version": "v1"}}),),
            SourceCursor({"n": number}),
        )


class BrokenAdapter(FixtureAdapter):
    source_name = "broken"

    def scan(self, request: ScanRequest, cursor: SourceCursor | None) -> ScanBatch:
        raise RuntimeError("secret /private/path token=abc")


class ContextSourceRunnerTests(unittest.TestCase):
    def test_runner_publishes_derivative_and_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            registry = SourceRegistry([FixtureAdapter()])
            runner = SourceRunner(registry, source_db=root / "sources.sqlite3", inbox_db=root / "inbox.sqlite3")
            first = runner.scan_one("fixture")
            second = runner.scan_one("fixture")
            rows = ContextInbox(root / "inbox.sqlite3").list_events()
            cursor = SourceStore(root / "sources.sqlite3").cursor("fixture")
        self.assertEqual(first.status, SourceStatus.HEALTHY)
        self.assertEqual(second.status, SourceStatus.HEALTHY)
        self.assertEqual(len(rows), 2)
        self.assertEqual(cursor, SourceCursor({"n": 2}))
        self.assertNotIn("summary", rows[0]["raw_json"])

    def test_dry_run_creates_no_files_and_failure_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            registry = SourceRegistry([FixtureAdapter(), BrokenAdapter()])
            runner = SourceRunner(registry, source_db=root / "sources.sqlite3", inbox_db=root / "inbox.sqlite3")
            dry = runner.scan_one("fixture", dry_run=True)
            broken = runner.scan_one("broken", dry_run=True)
            files = list(root.iterdir())
        self.assertTrue(dry.dry_run)
        self.assertEqual(broken.reason_code, "runtimeerror")
        self.assertEqual(files, [])
