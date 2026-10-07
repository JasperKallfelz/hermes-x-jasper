from __future__ import annotations

import json
import subprocess
import unittest

from hermes_second_brain.context_sources.adapters.reminders import RemindersAdapter
from hermes_second_brain.context_sources.models import ScanRequest, SourceCursor


class ReminderTests(unittest.TestCase):
    def test_read_allowlist_redaction_and_cursor(self) -> None:
        calls = []
        payload = {"reminders": [{"id": "r1", "title": "Email me@example.com token=supersecretvalue", "modified_at": "2026-07-22T10:00:00Z", "due_date": "2026-07-23"}]}

        def execute(argv, timeout):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        adapter = RemindersAdapter("fixture-remindctl", executor=execute)
        batch = adapter.scan(ScanRequest(), None)
        again = adapter.scan(ScanRequest(), batch.cursor)
        self.assertEqual(calls[0], ["fixture-remindctl", "show", "all", "--json", "--no-input"])
        self.assertNotIn("supersecretvalue", json.dumps(batch.raw_items[0].payload))
        self.assertNotIn("me@example.com", batch.derivatives[0].event["body"])
        self.assertEqual(len(again.raw_items), 0)
