"""B+ morning operator report and once-only delivery wrapper."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from hermes_second_brain.dreaming.config import DreamingConfig
from hermes_second_brain.dreaming.store import DreamStore
from hermes_second_brain.improvement import ImprovementStore
from hermes_second_brain.lifecycle import LifecycleStore
from hermes_second_brain.operations import claim_morning_report, emit_morning_report
from hermes_second_brain.temporary_memory import TemporaryMemoryStore


class MorningReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.now = datetime(2026, 7, 21, 6, 30, tzinfo=timezone.utc)
        self.context_db = self.root / "context.sqlite3"
        self.config = DreamingConfig(
            enabled=True,
            timezone="Europe/Berlin",
            profiles=(),
            dream_state_db=self.root / "dream.sqlite3",
            reports_dir=self.root / "reports",
            import_dir=self.root / "imports",
            dreams_md=self.root / "DREAMS.md",
            staging_dir=self.root / "staging",
            lifecycle_db=self.root / "lifecycle.sqlite3",
            improvement_db=self.root / "improvements.sqlite3",
        )
        dream = DreamStore(self.config.dream_state_db)
        run_id = dream.start_run(mode="incremental", deadline=None, now=self.now.timestamp())
        dream.finish_run(
            run_id,
            status="complete",
            rounds=1,
            summary={"sessions": 2, "secret": "MUST-NOT-LEAK"},
            now=self.now.timestamp(),
        )
        dream.upsert_insight(
            kind="contradiction",
            claim="MUST-NOT-LEAK contradiction text",
            detail="MUST-NOT-LEAK",
            confidence=0.8,
            status="grounded",
            run_id=run_id,
            evidence=[],
            candidate_ids=[],
            now=self.now.timestamp(),
        )
        dream.record_report(
            run_id=run_id,
            path=self.root / "raw-private-report.md",
            fingerprint="morning-fingerprint",
            now=self.now.timestamp(),
        )

        lifecycle = LifecycleStore(self.config.lifecycle_db)
        job_id = lifecycle.enqueue_job(
            idempotency_key="morning-job", job_kind="dream_round", now="2026-07-21T04:00:00Z"
        )["job_id"]
        lifecycle.start_job(
            job_id, owner="worker", lease_seconds=60, now="2026-07-21T04:01:00Z"
        )
        lifecycle.detect_stale_jobs(now="2026-07-21T04:03:00Z")

        improvements = ImprovementStore(self.config.improvement_db)
        proposal = improvements.propose(
            idempotency_key="morning-proposal",
            title="MUST-NOT-LEAK proposal",
            risk_class="low",
            target_kind="test",
            proposed_intervention="MUST-NOT-LEAK intervention",
            expected_metric="error_rate",
            now="2026-07-20T00:00:00Z",
        )
        improvements.review(proposal["proposal_id"], now="2026-07-20T00:01:00Z")

        temporary = TemporaryMemoryStore(self.context_db)
        temporary.add(
            idempotency_key="expired-context",
            kind="context",
            text="MUST-NOT-LEAK temporary text",
            expires_at="2026-07-21T05:00:00Z",
            now="2026-07-21T04:00:00Z",
        )

    def test_report_is_bounded_german_metadata_and_once_only(self) -> None:
        delivery = claim_morning_report(
            self.config,
            context_db=self.context_db,
            owner="morning-test",
            now=self.now,
        )
        self.assertIsNotNone(delivery)
        assert delivery is not None
        text = delivery.text
        self.assertIn("Morgenlage", text)
        self.assertIn(f"Liefer-ID: {delivery.report_id}", text)
        self.assertIn("Dream-Rückstand", text)
        self.assertIn("Publikationswarteschlange", text)
        self.assertIn("Blockierte/stehende Jobs: 1", text)
        self.assertIn("Ausstehende Verbesserungsfreigaben: 1", text)
        self.assertIn("Abgelaufene TTL-Einträge: 1", text)
        self.assertIn("Widersprüche: 1", text)
        self.assertLessEqual(len(text), 3900)
        self.assertNotIn("MUST-NOT-LEAK", text)

        output = io.StringIO()
        self.assertEqual(
            emit_morning_report(
                self.config,
                context_db=self.context_db,
                stdout=output,
                owner="morning-emitter",
                now=self.now,
            ),
            0,
        )
        # The earlier explicit claim is still leased and was not acknowledged,
        # so a competing emitter stays quiet rather than duplicating output.
        self.assertEqual(output.getvalue(), "")

        DreamStore(self.config.dream_state_db).release_report_claim(
            delivery.report_id, delivery.owner
        )
        self.assertEqual(
            emit_morning_report(
                self.config,
                context_db=self.context_db,
                stdout=output,
                owner="morning-emitter",
                now=self.now,
            ),
            0,
        )
        self.assertTrue(output.getvalue())
        after = io.StringIO()
        self.assertEqual(
            emit_morning_report(
                self.config,
                context_db=self.context_db,
                stdout=after,
                owner="morning-second",
                now=self.now,
            ),
            0,
        )
        self.assertEqual(after.getvalue(), "")

    def test_output_failure_releases_claim_for_retry(self) -> None:
        class Broken:
            def write(self, _value):
                raise OSError("delivery failed")

            def flush(self):
                pass

        self.assertEqual(
            emit_morning_report(
                self.config,
                context_db=self.context_db,
                stdout=Broken(),
                owner="broken",
                now=self.now,
            ),
            1,
        )
        retry = io.StringIO()
        self.assertEqual(
            emit_morning_report(
                self.config,
                context_db=self.context_db,
                stdout=retry,
                owner="retry",
                now=self.now,
            ),
            0,
        )
        self.assertTrue(retry.getvalue())

    def test_multiple_same_day_reports_supersede_older_trigger_atomically(self) -> None:
        dream = DreamStore(self.config.dream_state_db)
        newer_run = dream.start_run(
            mode="incremental", deadline=None, now=self.now.timestamp() + 60
        )
        dream.finish_run(
            newer_run,
            status="failed",
            rounds=3,
            now=self.now.timestamp() + 61,
        )
        newer_id = dream.record_report(
            run_id=newer_run,
            path=self.root / "newer-private-report.md",
            fingerprint="newer-morning-fingerprint",
            now=self.now.timestamp() + 61,
        )

        first = claim_morning_report(
            self.config, context_db=self.context_db, owner="first", now=self.now + timedelta(minutes=2)
        )
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.report_id, newer_id)
        self.assertIn("Dream-Gesundheit: failed", first.text)
        self.assertIn("Liefer-ID: " + newer_id, first.text)
        self.assertIsNone(claim_morning_report(
            self.config, context_db=self.context_db, owner="second", now=self.now + timedelta(minutes=2)
        ))

    def test_retry_uses_immutable_claim_snapshot_even_after_newer_run(self) -> None:
        first = claim_morning_report(
            self.config, context_db=self.context_db, owner="snapshot-first", now=self.now
        )
        self.assertIsNotNone(first)
        assert first is not None
        DreamStore(self.config.dream_state_db).release_report_claim(first.report_id, first.owner)

        dream = DreamStore(self.config.dream_state_db)
        later_run = dream.start_run(
            mode="incremental", deadline=None, now=self.now.timestamp() + 300
        )
        dream.finish_run(
            later_run, status="failed", rounds=9, now=self.now.timestamp() + 301
        )
        retry = claim_morning_report(
            self.config, context_db=self.context_db, owner="snapshot-retry", now=self.now
        )
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertEqual(retry.report_id, first.report_id)
        self.assertEqual(retry.text, first.text)

    def test_post_flush_ack_failure_retries_same_delivery_id_at_least_once(self) -> None:
        first_output = io.StringIO()
        with mock.patch.object(DreamStore, "ack_report", side_effect=RuntimeError("crash-after-flush")):
            self.assertEqual(emit_morning_report(
                self.config,
                context_db=self.context_db,
                stdout=first_output,
                owner="crashing",
                now=self.now,
            ), 1)
        self.assertTrue(first_output.getvalue())
        retry_output = io.StringIO()
        self.assertEqual(emit_morning_report(
            self.config,
            context_db=self.context_db,
            stdout=retry_output,
            owner="retry-after-flush",
            now=self.now,
        ), 0)
        self.assertEqual(retry_output.getvalue(), first_output.getvalue())
        self.assertIn("Liefer-ID: rpt_", retry_output.getvalue())

    def test_repository_wrapper_is_quiet_on_no_data(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "dream_morning_report.py"
        self.assertTrue(script.is_file())
        source = script.read_text(encoding="utf-8")
        compile(source, str(script), "exec")
        self.assertNotIn("shell=True", source)
        template = (
            Path(__file__).parents[1]
            / "templates"
            / "hermes-dream-morning-report.sh.example"
        )
        self.assertTrue(template.is_file())
        shell_source = template.read_text(encoding="utf-8")
        self.assertIn("__PROJECT_DIR__", shell_source)
        self.assertIn("__CONFIG_PATH__", shell_source)
        self.assertNotIn(str(Path.home()), shell_source)
        self.assertEqual(
            subprocess.run(
                ["bash", "-n", str(template)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode,
            0,
        )

        spec = importlib.util.spec_from_file_location("morning_wrapper_fixture", script)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        manifest = self.root / "validate-only.json"
        manifest.write_text(json.dumps({
            "context_inbox_db": str(self.root / "missing-context.sqlite3"),
            "dreaming": {
                "enabled": True,
                "timezone": "Europe/Berlin",
                "profiles": [{"name": "default", "state_db": str(self.root / "missing-profile.sqlite3")}],
                "dream_state_db": str(self.root / "missing-dream.sqlite3"),
                "reports_dir": str(self.root / "missing-reports"),
                "import_dir": str(self.root / "missing-imports"),
                "staging_dir": str(self.root / "missing-staging"),
                "dreams_md": str(self.root / "missing-DREAMS.md"),
                "lifecycle_db": str(self.root / "missing-lifecycle.sqlite3"),
                "improvement_db": str(self.root / "missing-improvements.sqlite3"),
            },
        }), encoding="utf-8")
        before = {path.name for path in self.root.iterdir()}
        self.assertEqual(module.main(["--config", str(manifest), "--validate-only"]), 0)
        self.assertEqual({path.name for path in self.root.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
