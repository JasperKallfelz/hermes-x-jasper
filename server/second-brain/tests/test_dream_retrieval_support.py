import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dream_retrieval_contract as contract
from hermes_second_brain.dreaming.config import ConfigError, RetrievalConfig
from hermes_second_brain.dreaming.retrieval import SearchHit, retrieve_context
from hermes_second_brain.dreaming.retrieval_support import (
    diverse_merge, document_uri, excerpt, keyword_pattern, lexical_rows, verified_local_evidence,
)


class RetrievalSupportTests(unittest.TestCase):
    def hit(self, document, suffix="/.abstract.md"):
        uri = "viking://resources/brain/" + document + suffix
        return SearchHit(uri, "Native summary", {"source_uri": uri, "source_date": None,
                                                 "source_version": None, "evidence_status": "unknown"})

    def test_document_diversity_keeps_room_for_both_searches(self):
        semantic = [self.hit("a.md"), self.hit("a.md", "/.overview.md"), self.hit("b.md")]
        lexical = [self.hit("c.md", "/content.md"), self.hit("b.md", "/content.md")]
        merged = diverse_merge(semantic, lexical, 3)
        self.assertEqual([h.source_ref.split("/")[4] for h in merged], ["a.md", "c.md", "b.md"])

    def test_keyword_regex_is_literal_and_bounded(self):
        pattern = keyword_pattern("Which Composio project provides access to Notion? (.*)")
        self.assertEqual(pattern, "composio|notion")
        self.assertEqual(keyword_pattern("the and or"), "")
        self.assertLessEqual(len(keyword_pattern("Alpha Beta Gamma Delta Epsilon").split("|")), 3)

    def test_lexical_results_are_ranked_by_query_terms_and_confined_to_namespace(self):
        payload = {"result": {"matches": [
            {"uri": "viking://resources/brain/a", "content": "Composio connects."},
            {"uri": "viking://resources/mail/secret", "content": "Composio company Notion workspace"},
            {"uri": "viking://resources/brain/b", "content": "Composio company Notion workspace"}]}}
        rows = lexical_rows(payload, "Composio company Notion workspace", "brain", 5)
        self.assertEqual([r["uri"] for r in rows], ["viking://resources/brain/b", "viking://resources/brain/a"])

    def test_excerpt_selects_relevant_original_words_without_frontmatter(self):
        text = "---\ndate: 2026-05-01\n---\n" + "unrelated background. " * 30 + "\nComposio uses the company project for Notion."
        result = excerpt(text, "Composio Notion company project", 100)
        self.assertIn("Composio uses the company project", result)
        self.assertLessEqual(len(result), 100)
        self.assertNotIn("date:", result)
        self.assertEqual(excerpt(text, "nonexistent", 100), "")

    def registry_fixture(self, text, *, stored_hash=None, status="synced"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        source = root / "document.md"
        source.write_text(text)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        registry = root / "state.sqlite3"
        with sqlite3.connect(registry) as db:
            db.execute("CREATE TABLE resources(path, sha256, source_id, ov_resource_id, namespace, status)")
            db.execute("INSERT INTO resources VALUES(?,?,?,?,?,?)", (str(source), stored_hash or digest,
                       "brain:doc", "viking://resources/brain/doc.md", "brain", status))
        return registry, source, digest

    def test_verified_source_text_carries_exact_version_and_explicit_date(self):
        registry, _, digest = self.registry_fixture("---\nsource_date: '2026-05-03'\n---\nComposio uses the company project.")
        original = self.hit("doc.md")
        enriched = verified_local_evidence(original, "Composio company", "brain", registry, 100)
        self.assertEqual(enriched.snippet, "Composio uses the company project.")
        self.assertEqual(enriched.provenance["source_version"], digest)
        self.assertEqual(enriched.provenance["source_date"], "2026-05-03")
        self.assertEqual(enriched.provenance["evidence_origin"], "verified_local_source")
        self.assertFalse(enriched.provenance["index_version_verified"])
        self.assertEqual(enriched.provenance["retrieval_uri"], original.source_ref)

    def test_file_mtime_and_generated_at_are_not_source_dates(self):
        registry, _, _ = self.registry_fixture("---\ngenerated_at: 2026-05-03\n---\nComposio source text")
        enriched = verified_local_evidence(self.hit("doc.md"), "Composio", "brain", registry, 100)
        self.assertIsNone(enriched.provenance["source_date"])

    def test_changed_or_unsynced_source_never_gets_claimed_version(self):
        for kwargs in ({"stored_hash": "not-the-current-hash"}, {"status": "pending"}):
            registry, _, _ = self.registry_fixture("Composio source", **kwargs)
            original = self.hit("doc.md")
            self.assertIs(verified_local_evidence(original, "Composio", "brain", registry, 100), original)

    def test_namespace_or_missing_registry_cannot_widen_file_access(self):
        registry, _, _ = self.registry_fixture("Composio source")
        original = self.hit("doc.md")
        self.assertIs(verified_local_evidence(original, "Composio", "mail", registry, 100), original)
        self.assertIs(verified_local_evidence(original, "Composio", "brain", registry.with_name("missing"), 100), original)

    def test_local_excerpt_is_redacted_before_handoff(self):
        registry, _, _ = self.registry_fixture("Composio project password=do-not-retain in this source")
        result = verified_local_evidence(self.hit("doc.md"), "Composio project", "brain", registry, 100)
        self.assertNotIn("do-not-retain", result.snippet)

    def test_real_retrieval_merges_original_text_and_survives_keyword_failure(self):
        for fail_keyword in (False, True):
            config = contract.OpenVikingRetrievalContractTests().config()
            config.retrieval.namespaces = ("brain",)
            config.retrieval.levels = (0, 1, 2)
            config.retrieval.keyword_enabled = True
            requests = []
            def request(req, **kwargs):
                requests.append((req.full_url, json.loads(req.data)))
                if req.full_url.endswith("grep"):
                    if fail_keyword:
                        raise TimeoutError()
                    result = {"matches": [{"uri": "viking://resources/brain/lexical.md/source.md",
                                            "content": "Composio company Notion workspace"}]}
                else:
                    result = {"resources": [{"uri": "viking://resources/brain/semantic.md/.abstract.md", "abstract": "Semantic excerpt"}]}
                return io.BytesIO(json.dumps({"status": "ok", "result": result}).encode())
            with patch("hermes_second_brain.dreaming.retrieval._open_request", request):
                outcome = retrieve_context(["Composio Notion workspace"], config)
            self.assertEqual(outcome.failed, 0)
            self.assertEqual(len(outcome.items), 1 if fail_keyword else 2)
            self.assertEqual(requests[0][1]["level"], [0, 1, 2])
            self.assertTrue(requests[0][1]["include_provenance"])
            self.assertTrue(all(len(hit.snippet) <= 80 for hit in outcome.items))

    def test_two_questions_keep_distinct_excerpts_from_one_original(self):
        text = "Terminal Ghostty uses a black background. " + "unrelated filler. " * 40 + "Composio Notion uses the company project."
        registry, _, _ = self.registry_fixture(text)
        config = contract.OpenVikingRetrievalContractTests().config()
        config.retrieval.namespaces = ("brain",)
        config.retrieval.source_registry = registry
        payload = {"status": "ok", "result": {"resources": [{"uri": "viking://resources/brain/doc.md/content.md", "abstract": "Native summary"}]}}
        with patch("hermes_second_brain.dreaming.retrieval._open_request", side_effect=lambda *_a, **_k: io.BytesIO(json.dumps(payload).encode())):
            result = retrieve_context(["Terminal Ghostty", "Composio Notion"], config)
        self.assertEqual(len(result.items), 2)
        self.assertNotEqual(result.items[0].ref, result.items[1].ref)
        self.assertIn("Terminal Ghostty", result.items[0].snippet)
        self.assertIn("Composio Notion", result.items[1].snippet)
        self.assertEqual(document_uri(result.items[0].provenance["source_uri"]), document_uri(result.items[1].provenance["source_uri"]))

    def test_invalid_levels_and_relative_registry_are_rejected(self):
        with self.assertRaises(ConfigError):
            RetrievalConfig(levels=(3,))
        with self.assertRaises(ConfigError):
            RetrievalConfig(source_registry=Path("relative.sqlite3"))


if __name__ == "__main__":
    unittest.main()
