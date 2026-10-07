from __future__ import annotations

import json
import unittest

from hermes_second_brain.context_sources.adapters.contacts import ContactsAdapter
from hermes_second_brain.context_sources.models import ScanRequest, SourceStatus


class ContactsTests(unittest.TestCase):
    def test_fixture_provider_hashes_contact_fields(self) -> None:
        adapter = ContactsAdapter(provider=lambda: [{"identifier": "abc", "emails": ["person@example.com"], "phones": ["+49123456789"], "organization": "Secret Org", "note": "never"}])
        batch = adapter.scan(ScanRequest(), None)
        serialized = json.dumps(batch.raw_items[0].payload)
        self.assertNotIn("person@example.com", serialized)
        self.assertNotIn("49123456789", serialized)
        self.assertNotIn("Secret Org", serialized)
        self.assertNotIn("never", serialized)

    def test_default_boundary_is_pending_or_unsupported(self) -> None:
        self.assertIn(ContactsAdapter().health(ScanRequest()).status, {SourceStatus.PENDING_PERMISSION, SourceStatus.UNSUPPORTED})
