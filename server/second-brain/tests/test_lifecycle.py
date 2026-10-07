"""Behavioral tests for the three durable Second Brain lifecycles.

Intent, tool-job/work-order, and their linkage to (separate) memory lifecycles.
The store must validate transitions, be idempotent where safe, transactional,
private on disk, resumable, gate completion on explicit verification evidence,
and expose metadata-only surfaces. A stale-job detector marks stalled only from
explicit liveness conditions and never kills processes.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hermes_second_brain.lifecycle import (
    InvalidTransition,
    LifecycleError,
    LifecycleStore,
)


class LifecycleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "nested" / "lifecycle.sqlite3"
        self.store = LifecycleStore(self.db_path)

    def T(self, hour: int, minute: int = 0, second: int = 0) -> str:
        return f"2026-07-21T{hour:02d}:{minute:02d}:{second:02d}Z"


class IntentLifecycleTests(LifecycleTestBase):
    def test_capture_then_full_happy_path(self) -> None:
        intent = self.store.capture_intent(
            idempotency_key="intent-1",
            summary="Prepare quarterly review",
            source="manual",
            now=self.T(9),
        )
        self.assertEqual(intent["state"], "captured")
        self.assertTrue(intent["created"])
        intent_id = intent["intent_id"]
        self.assertEqual(self.store.advance_intent(intent_id, "clarified", now=self.T(9, 1))["state"], "clarified")
        self.assertEqual(self.store.advance_intent(intent_id, "approved", now=self.T(9, 2))["state"], "approved")
        self.assertEqual(self.store.advance_intent(intent_id, "planned", now=self.T(9, 3))["state"], "planned")
        done = self.store.advance_intent(intent_id, "done", now=self.T(9, 4))
        self.assertEqual(done["state"], "done")
        self.assertTrue(done["terminal_at"])

    def test_capture_is_idempotent_on_key(self) -> None:
        first = self.store.capture_intent(idempotency_key="dup", summary="a", source="dream", now=self.T(9))
        second = self.store.capture_intent(idempotency_key="dup", summary="a", source="dream", now=self.T(10))
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

    def test_capture_conflicting_content_on_same_key_fails(self) -> None:
        self.store.capture_intent(idempotency_key="dup", summary="a", source="dream", now=self.T(9))
        with self.assertRaises(LifecycleError):
            self.store.capture_intent(idempotency_key="dup", summary="DIFFERENT", source="dream", now=self.T(9))

    def test_illegal_intent_skip_is_rejected(self) -> None:
        intent = self.store.capture_intent(idempotency_key="i", summary="a", source="manual", now=self.T(9))
        with self.assertRaises(InvalidTransition):
            self.store.advance_intent(intent["intent_id"], "done", now=self.T(9, 1))

    def test_cancel_from_nonterminal_is_allowed_but_not_from_terminal(self) -> None:
        intent = self.store.capture_intent(idempotency_key="i", summary="a", source="manual", now=self.T(9))
        cancelled = self.store.cancel_intent(intent["intent_id"], now=self.T(9, 1))
        self.assertEqual(cancelled["state"], "cancelled")
        with self.assertRaises(InvalidTransition):
            self.store.advance_intent(intent["intent_id"], "clarified", now=self.T(9, 2))

    def test_advance_replay_with_idempotency_key_is_noop(self) -> None:
        intent = self.store.capture_intent(idempotency_key="i", summary="a", source="manual", now=self.T(9))
        intent_id = intent["intent_id"]
        a = self.store.advance_intent(intent_id, "clarified", idempotency_key="step-1", now=self.T(9, 1))
        b = self.store.advance_intent(intent_id, "clarified", idempotency_key="step-1", now=self.T(9, 5))
        self.assertEqual(a["state"], "clarified")
        self.assertEqual(b["state"], "clarified")
        self.assertEqual(a["updated_at"], b["updated_at"])


class JobLifecycleTests(LifecycleTestBase):
    def _queued(self, key: str = "job-1", **kw) -> str:
        return self.store.enqueue_job(idempotency_key=key, job_kind="dream_round", now=self.T(3), **kw)["job_id"]

    def test_happy_path_requires_verification_before_completion(self) -> None:
        job_id = self._queued()
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=600, now=self.T(3, 1)
        )["lease_generation"]
        self.store.checkpoint_job(
            job_id, owner="w1", lease_generation=generation,
            checkpoint="round=1", now=self.T(3, 2)
        )
        self.store.begin_verification(
            job_id, owner="w1", lease_generation=generation, now=self.T(3, 3)
        )
        completed = self.store.complete_job(
            job_id, owner="w1", lease_generation=generation,
            verification_evidence="gate=green;tests=238", now=self.T(3, 4)
        )
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(completed["terminal_at"])
        self.assertEqual(completed["verification_evidence"], "gate=green;tests=238")

    def test_direct_running_to_completed_is_rejected(self) -> None:
        job_id = self._queued()
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=600, now=self.T(3, 1)
        )["lease_generation"]
        with self.assertRaises(InvalidTransition):
            self.store.complete_job(
                job_id, owner="w1", lease_generation=generation,
                verification_evidence="x", now=self.T(3, 2)
            )

    def test_completion_without_evidence_is_rejected(self) -> None:
        job_id = self._queued()
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=600, now=self.T(3, 1)
        )["lease_generation"]
        self.store.begin_verification(
            job_id, owner="w1", lease_generation=generation, now=self.T(3, 2)
        )
        with self.assertRaises(LifecycleError):
            self.store.complete_job(
                job_id, owner="w1", lease_generation=generation,
                verification_evidence="   ", now=self.T(3, 3)
            )

    def test_start_is_idempotent_by_key_and_enqueue_dedup(self) -> None:
        first = self.store.enqueue_job(idempotency_key="dup", job_kind="k", now=self.T(3))
        second = self.store.enqueue_job(idempotency_key="dup", job_kind="k", now=self.T(3))
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

    def test_heartbeat_and_checkpoint_extend_lease_and_resume(self) -> None:
        job_id = self._queued()
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=120, now=self.T(3, 1)
        )["lease_generation"]
        beat = self.store.heartbeat(
            job_id, owner="w1", lease_generation=generation,
            lease_seconds=60, now=self.T(3, 1, 30)
        )
        self.assertGreater(beat["lease_until"], 0)
        self.store.checkpoint_job(
            job_id, owner="w1", lease_generation=generation,
            checkpoint="round=2", now=self.T(3, 2)
        )
        resumed = self.store.resume_job(
            job_id, owner="w1", lease_generation=generation,
            lease_seconds=60, now=self.T(3, 2, 10)
        )
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(resumed["checkpoint"], "round=2")

    def test_heartbeat_from_wrong_owner_is_rejected(self) -> None:
        job_id = self._queued()
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=60, now=self.T(3, 1)
        )["lease_generation"]
        with self.assertRaises(LifecycleError):
            self.store.heartbeat(
                job_id, owner="intruder", lease_generation=generation,
                lease_seconds=60, now=self.T(3, 1, 10)
            )

    def test_lease_generation_fences_all_active_mutations_and_retry_clears_terminal_fields(self) -> None:
        job_id = self._queued(max_attempts=3)
        started = self.store.start_job(
            job_id, owner="w1", lease_seconds=60, now=self.T(3, 1)
        )
        generation = started["lease_generation"]
        self.store.checkpoint_job(
            job_id,
            owner="w1",
            lease_generation=generation,
            checkpoint="round=1",
            now=self.T(3, 1, 10),
        )
        self.store.begin_verification(
            job_id,
            owner="w1",
            lease_generation=generation,
            now=self.T(3, 1, 20),
        )
        failed = self.store.fail_job(
            job_id,
            owner="w1",
            lease_generation=generation,
            error_type="timeout",
            now=self.T(3, 1, 30),
        )
        self.assertTrue(failed["terminal_at"])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE lifecycle_jobs SET verification_evidence='old-terminal-evidence' WHERE job_id=?",
                (job_id,),
            )
        retried = self.store.retry_job(
            job_id,
            owner="w1",
            lease_generation=generation,
            now=self.T(3, 2),
        )
        self.assertEqual(retried["state"], "queued")
        self.assertEqual(retried["terminal_at"], "")
        self.assertEqual(retried["verification_evidence"], "")
        self.assertEqual(retried["error_type"], "")
        self.assertEqual(retried["heartbeat_at"], "")

        restarted = self.store.start_job(
            job_id, owner="w2", lease_seconds=60, now=self.T(3, 3)
        )
        self.assertGreater(restarted["lease_generation"], generation)
        for mutation in (
            lambda: self.store.heartbeat(
                job_id,
                owner="w1",
                lease_generation=generation,
                lease_seconds=60,
                now=self.T(3, 3, 10),
            ),
            lambda: self.store.checkpoint_job(
                job_id,
                owner="w1",
                lease_generation=generation,
                checkpoint="stale",
                now=self.T(3, 3, 10),
            ),
            lambda: self.store.begin_verification(
                job_id,
                owner="w1",
                lease_generation=generation,
                now=self.T(3, 3, 10),
            ),
            lambda: self.store.fail_job(
                job_id,
                owner="w1",
                lease_generation=generation,
                error_type="stale",
                now=self.T(3, 3, 10),
            ),
        ):
            with self.assertRaises(LifecycleError):
                mutation()

        fresh_generation = restarted["lease_generation"]
        with self.assertRaises(LifecycleError):
            self.store.heartbeat(
                job_id,
                owner="w2",
                lease_generation=fresh_generation,
                lease_seconds=60,
                now=self.T(3, 5),
            )

    def test_stale_detector_marks_stalled_only_when_lease_expired(self) -> None:
        job_id = self._queued()
        self.store.start_job(job_id, owner="w1", lease_seconds=60, now=self.T(3, 1))
        # Lease still valid: nothing stalls.
        healthy = self.store.detect_stale_jobs(now=self.T(3, 1, 30))
        self.assertEqual(healthy["stalled"], [])
        # Lease expired: exactly this job stalls, deterministically.
        stale = self.store.detect_stale_jobs(now=self.T(3, 5))
        self.assertEqual(stale["stalled"], [job_id])
        self.assertEqual(self.store.get_job(job_id)["state"], "stalled")
        # Idempotent: a second sweep does not re-report an already stalled job.
        again = self.store.detect_stale_jobs(now=self.T(3, 6))
        self.assertEqual(again["stalled"], [])

    def test_stale_detector_dry_run_does_not_mutate(self) -> None:
        job_id = self._queued()
        self.store.start_job(job_id, owner="w1", lease_seconds=60, now=self.T(3, 1))
        result = self.store.detect_stale_jobs(now=self.T(3, 5), dry_run=True)
        self.assertEqual(result["stalled"], [job_id])
        self.assertEqual(self.store.get_job(job_id)["state"], "running")

    def test_retry_budget_is_bounded(self) -> None:
        job_id = self._queued(max_attempts=2)
        # attempt 1
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=120, now=self.T(3, 1)
        )["lease_generation"]
        self.store.fail_job(
            job_id, owner="w1", lease_generation=generation,
            error_type="timeout", now=self.T(3, 2)
        )
        self.store.retry_job(
            job_id, owner="w1", lease_generation=generation, now=self.T(3, 3)
        )
        # attempt 2
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=120, now=self.T(3, 4)
        )["lease_generation"]
        self.store.fail_job(
            job_id, owner="w1", lease_generation=generation,
            error_type="timeout", now=self.T(3, 5)
        )
        with self.assertRaises(LifecycleError):
            self.store.retry_job(
                job_id, owner="w1", lease_generation=generation, now=self.T(3, 6)
            )

    def test_stalled_job_can_be_retried(self) -> None:
        job_id = self._queued(max_attempts=3)
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=180, now=self.T(3, 1)
        )["lease_generation"]
        self.store.detect_stale_jobs(now=self.T(3, 5))
        requeued = self.store.retry_job(
            job_id, owner="w1", lease_generation=generation, now=self.T(3, 6)
        )
        self.assertEqual(requeued["state"], "queued")

    def test_starting_job_does_not_complete_linked_intent(self) -> None:
        intent = self.store.capture_intent(idempotency_key="i", summary="a", source="dream", now=self.T(3))
        job_id = self.store.enqueue_job(
            idempotency_key="j", job_kind="k", intent_id=intent["intent_id"], now=self.T(3)
        )["job_id"]
        generation = self.store.start_job(
            job_id, owner="w1", lease_seconds=180, now=self.T(3, 1)
        )["lease_generation"]
        self.store.begin_verification(
            job_id, owner="w1", lease_generation=generation, now=self.T(3, 2)
        )
        self.store.complete_job(
            job_id, owner="w1", lease_generation=generation,
            verification_evidence="ok", now=self.T(3, 3)
        )
        # The intent lifecycle is independent: it is NOT auto-advanced.
        self.assertEqual(self.store.get_intent(intent["intent_id"])["state"], "captured")

    def test_enqueue_rejects_unknown_intent(self) -> None:
        with self.assertRaises(LifecycleError):
            self.store.enqueue_job(idempotency_key="j", job_kind="k", intent_id="nope", now=self.T(3))


class PrivacyAndMetadataTests(LifecycleTestBase):
    def test_database_and_parent_are_private(self) -> None:
        self.store.capture_intent(idempotency_key="i", summary="a", source="manual", now=self.T(9))
        db_mode = stat.S_IMODE(os.stat(self.db_path).st_mode)
        parent_mode = stat.S_IMODE(os.stat(self.db_path.parent).st_mode)
        self.assertEqual(db_mode & 0o077, 0)
        self.assertEqual(parent_mode & 0o077, 0)

    def test_snapshot_is_metadata_only_counts(self) -> None:
        job_id = self.store.enqueue_job(idempotency_key="j", job_kind="k", now=self.T(3))["job_id"]
        self.store.start_job(job_id, owner="w1", lease_seconds=60, now=self.T(3, 1))
        snap = self.store.snapshot()
        self.assertEqual(snap["jobs"]["running"], 1)
        self.assertEqual(snap["intents"].get("captured", 0), 0)
        # No free-form payload/content keys leak into the snapshot.
        flat = repr(snap)
        self.assertNotIn("owner", flat)

    def test_operator_visible_classes_reject_free_form_or_path_text(self) -> None:
        with self.assertRaises(LifecycleError):
            self.store.enqueue_job(
                idempotency_key="unsafe-kind",
                job_kind="run /private/customer/path",
                now=self.T(3),
            )
        job_id = self.store.enqueue_job(
            idempotency_key="safe-kind", job_kind="dream_round", now=self.T(3)
        )["job_id"]
        generation = self.store.start_job(
            job_id, owner="worker-1", lease_seconds=60, now=self.T(3, 1)
        )["lease_generation"]
        with self.assertRaises(LifecycleError):
            self.store.fail_job(
                job_id,
                owner="worker-1",
                lease_generation=generation,
                error_type="failed opening /private/customer/path",
                now=self.T(3, 2),
            )

    def test_reject_symlinked_database_path(self) -> None:
        target = Path(self._tmp.name) / "real.sqlite3"
        target.write_text("")
        link = Path(self._tmp.name) / "link.sqlite3"
        link.symlink_to(target)
        with self.assertRaises(Exception):
            LifecycleStore(link)


if __name__ == "__main__":
    unittest.main()
