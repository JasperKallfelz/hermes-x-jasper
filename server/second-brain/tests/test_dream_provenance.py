import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import test_dreaming as fixtures
from hermes_second_brain.dreaming.provenance import source_provenance, source_day
from hermes_second_brain.dreaming.retrieval import RetrievedItem, RetrievalOutcome
from hermes_second_brain.dreaming.runner import run_sweep
from hermes_second_brain.dreaming.store import DreamStore, EvidenceRecord, snippet_hash


class ProvenanceTests(unittest.TestCase):
    def test_native_tags_preserve_source_identity_and_version(self):
        p = source_provenance({"uri": "viking://resources/brain/note",
                               "tags": ["source_id=brain:note", "sha256=abc",
                                        "source_date=2026-05-12", "evidence_status=superseded"]})
        self.assertEqual(p["source_uri"], "viking://resources/brain/note")
        self.assertEqual(p["source_id"], "brain:note")
        self.assertEqual(p["source_version"], "abc")
        self.assertEqual(p["source_date"], "2026-05-12")
        self.assertEqual(p["evidence_status"], "superseded")

    def test_index_timestamp_is_not_a_source_date(self):
        p = source_provenance({"created_at": "2026-09-23", "updated_at": "2026-09-23",
                               "status": "confirmed"})
        self.assertIsNone(p["source_date"])
        self.assertIsNone(p["source_version"])
        self.assertEqual(p["evidence_status"], "unknown")
        self.assertIsNotNone(p["retrieved_at"])
        self.assertIsNone(source_day("2026-02-30"))
        self.assertIsNone(source_day("yesterday"))

    def test_metadata_is_bounded_and_redacted(self):
        p = source_provenance({"source_version": "x" * 1000,
                               "metadata": {"source_id": "password=do-not-retain",
                                            "document_date": "2026-06-02T10:00:00Z"}})
        self.assertNotIn("do-not-retain", json.dumps(p))
        self.assertLessEqual(len(p["source_version"]), 256)
        self.assertEqual(p["source_date"], "2026-06-02")

    def test_additive_migration_and_round_trip_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td).resolve() / "dream.sqlite3"
            # A pre-v6 evidence table must survive the new additive migration.
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, candidate_id TEXT, ref TEXT, profile TEXT, session_id TEXT, role TEXT, day TEXT, snippet TEXT, snippet_hash TEXT, created_at REAL, run_id TEXT)")
                conn.execute("INSERT INTO evidence VALUES ('old','candidate','r','user','s','user','2026-01-01','legacy','hash',0,'run')")
            store = DreamStore(path)
            legacy = store.evidence_for("candidate")[0]
            self.assertEqual(legacy.day, "2026-01-01")
            self.assertEqual(legacy.provenance, {})
            p = source_provenance({"uri": "viking://resources/brain/new", "source_date": "2026-02-01"})
            evidence = EvidenceRecord("new", "openviking", "s2", "canonical", "2026-02-01",
                                      "A bounded source quote", snippet_hash("quote"), p)
            candidate, _ = store.upsert_candidate(run_id="r", kind="fact", claim="A bounded source quote",
                                                   detail="", evidence=[evidence], confidence=0.8,
                                                   durability=0.8, actionability=0, tags=[])
            self.assertEqual(store.evidence_for(candidate)[0].provenance, p)
            self.assertEqual(len(DreamStore(path).evidence_for("candidate")), 1)

    def _sweep(self, provenance):
        fixture = fixtures.DreamingTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        config = fixture.config("canonical-link")
        config = replace(config, retrieval=replace(config.retrieval, enabled=True))
        outcome = RetrievalOutcome(items=[RetrievedItem(
            "ov:source", "brain", "a" * 64,
            "Canonical Second Brain consolidation is approved", "canonical", provenance)], queried=1)
        result = run_sweep(config, now=lambda: fixture.now,
                           retrieval_fn=lambda *_args, **_kwargs: outcome)
        self.assertEqual(result.status, "complete")
        store = DreamStore(config.dream_state_db)
        entries = [e for insight in store.insights(run_id=result.run_id)
                   for e in insight["evidence"] if e["ref"] == "ov:source"]
        self.assertTrue(entries)
        return entries, config, result

    def test_real_sweep_keeps_origin_in_insights_and_artifact(self):
        p = source_provenance({"uri": "viking://resources/brain/original", "source_date": "2026-05-01",
                               "source_version": "revision-2", "evidence_status": "confirmed"})
        entries, config, result = self._sweep(p)
        self.assertEqual(entries[0]["day"], "2026-05-01")
        self.assertEqual(entries[0]["provenance"], p)
        self.assertIn(p["source_uri"], Path(result.report).read_text())

    def test_real_sweep_unknown_date_stays_unknown(self):
        entries, _, _ = self._sweep(source_provenance({"uri": "viking://resources/brain/undated"}))
        self.assertEqual(entries[0]["day"], "unknown")
        self.assertIsNone(entries[0]["provenance"]["source_date"])

    def test_superseded_source_cannot_become_canonical_by_namespace(self):
        entries, _, _ = self._sweep(source_provenance({"uri": "viking://resources/brain/old",
                                                      "evidence_status": "superseded"}))
        self.assertEqual(entries[0]["role"], "assistant")
