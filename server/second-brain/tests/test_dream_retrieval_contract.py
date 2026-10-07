"""Exercise the installed OpenViking response contract through Dreaming."""

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hermes_second_brain.dreaming.retrieval import retrieve_context


class OpenVikingRetrievalContractTests(unittest.TestCase):
    def config(self):
        return SimpleNamespace(
            retrieval=SimpleNamespace(
                enabled=True,
                namespaces=("brain", "sessions"),
                endpoint_url="http://127.0.0.1:1933/api/v1/search/find",
                timeout_seconds=1,
                limit=5,
                max_response_bytes=10000,
                is_canonical=lambda namespace: namespace == "brain",
            ),
            budgets=SimpleNamespace(
                max_retrieval_queries=2,
                max_retrieval_hits_per_query=5,
                max_retrieval_snippet_characters=80,
            ),
        )

    def retrieve(self, resources):
        payload = {"status": "ok", "result": {
            "memories": [], "resources": resources, "skills": [],
            "total": len(resources),
        }}
        records = []
        with patch(
            "hermes_second_brain.dreaming.retrieval._open_request",
            side_effect=lambda *_args, **_kwargs: io.BytesIO(json.dumps(payload).encode()),
        ):
            outcome = retrieve_context(
                ["Synthetic evidence"], self.config(), record=lambda **row: records.append(row)
            )
        return outcome, records

    def test_native_abstract_reaches_dreaming_with_provenance(self):
        outcome, records = self.retrieve([{
            "context_type": "resource", "uri": "viking://resources/brain/example",
            "level": 0, "score": 0.9, "abstract": "Grounded evidence from the source.",
            "tags": [],
        }])
        self.assertEqual(len(outcome.items), 2)
        self.assertEqual({item.role for item in outcome.items}, {"canonical", "assistant"})
        self.assertTrue(all(item.snippet == "Grounded evidence from the source." for item in outcome.items))
        self.assertEqual([row["hits"] for row in records], [1, 1])
        self.assertTrue(all("query" not in row and len(row["query_hash"]) == 64 for row in records))

    def test_source_revisions_get_distinct_evidence_references(self):
        common = {"uri": "viking://resources/brain/example", "abstract": "Same quote across revisions"}
        first, _ = self.retrieve([{**common, "tags": ["sha256=revision-a"]}])
        second, _ = self.retrieve([{**common, "tags": ["sha256=revision-b"]}])
        self.assertNotEqual(first.items[0].ref, second.items[0].ref)
        self.assertEqual(first.items[0].provenance["source_version"], "revision-a")
        self.assertEqual(second.items[0].provenance["source_version"], "revision-b")

    def test_empty_abstract_is_not_evidence(self):
        outcome, records = self.retrieve([{"uri": "viking://resources/brain/empty", "abstract": " "}])
        self.assertFalse(outcome.items)
        self.assertEqual([row["hits"] for row in records], [0, 0])

    def test_existing_excerpt_wins_and_budget_remains_enforced(self):
        outcome, _ = self.retrieve([{
            "uri": "viking://resources/brain/example", "abstract": "Fallback text",
            "snippet": "Specific excerpt. " * 20,
        }])
        self.assertTrue(outcome.items)
        self.assertTrue(all(item.snippet.startswith("Specific excerpt.") for item in outcome.items))
        self.assertTrue(all(len(item.snippet) <= 80 for item in outcome.items))


if __name__ == "__main__":
    unittest.main()
