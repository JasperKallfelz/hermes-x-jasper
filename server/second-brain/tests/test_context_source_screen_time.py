from __future__ import annotations

import unittest

from hermes_second_brain.context_sources.adapters.screen_time import ScreenTimeAdapter
from hermes_second_brain.context_sources.models import ScanRequest, SourceStatus


class ScreenTimeTests(unittest.TestCase):
    def test_live_private_database_is_not_supported(self) -> None:
        adapter = ScreenTimeAdapter()
        self.assertEqual(adapter.health(ScanRequest()).status, SourceStatus.PENDING_PERMISSION)
        with self.assertRaises(PermissionError):
            adapter.scan(ScanRequest(), None)
