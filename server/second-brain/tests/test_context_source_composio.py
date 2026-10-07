from __future__ import annotations

import json
import unittest

from hermes_second_brain.context_sources.adapters.composio import NotionAdapter, SlackAdapter
from hermes_second_brain.context_sources.models import ScanRequest, SourceStatus


class FixtureTransport:
    def __init__(self): self.calls = []
    def execute(self, tool, arguments):
        self.calls.append((tool, arguments))
        return {"items": [{"id": "p1", "title": "Plan token=supersecretvalue", "updated_at": "2026-07-22", "url": "https://notion.so/p?q=secret"}]}


class ComposioBoundaryTests(unittest.TestCase):
    def test_no_transport_is_pending_and_mutations_are_rejected(self) -> None:
        self.assertEqual(NotionAdapter().health(ScanRequest()).status, SourceStatus.PENDING_PERMISSION)
        with self.assertRaises(ValueError):
            NotionAdapter(tool="NOTION_CREATE")
        with self.assertRaises(ValueError):
            SlackAdapter(tool="SLACK_SEND_MESSAGE")

    def test_injected_fixture_is_bounded_and_redacted(self) -> None:
        transport = FixtureTransport()
        batch = NotionAdapter(transport=transport).scan(ScanRequest(limit=5), None)
        value = json.dumps(batch.raw_items[0].payload)
        self.assertEqual(transport.calls[0][0], "NOTION_SEARCH")
        self.assertNotIn("supersecretvalue", value)
        self.assertNotIn("q=secret", value)
        self.assertFalse(hasattr(NotionAdapter(), "credentials"))
