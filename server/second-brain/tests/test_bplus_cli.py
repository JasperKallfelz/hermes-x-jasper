from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from hermes_second_brain.cli import main as cli_main
from hermes_second_brain.manifest import write_default_manifest
from hermes_second_brain.observatory import ObservatoryStore


class BPlusCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def call(self, argv: list[str]) -> dict:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli_main(argv), 0)
        return json.loads(output.getvalue())

    def test_lifecycle_mutations_return_metadata_only_and_status_counts(self) -> None:
        db = self.root / "lifecycle.sqlite3"
        captured = self.call([
            "lifecycle", "intent-capture", "--db", str(db),
            "--idempotency-key", "cli-intent", "--summary", "PRIVATE INTENT TEXT",
            "--source", "manual", "--json",
        ])
        self.assertEqual(captured["state"], "captured")
        self.assertNotIn("summary", captured)
        self.assertNotIn("PRIVATE", json.dumps(captured))
        status = self.call(["lifecycle", "status", "--db", str(db), "--json"])
        self.assertEqual(status["intents"]["captured"], 1)

    def test_observatory_and_improvement_cli_reports_are_aggregate_only(self) -> None:
        observatory_db = self.root / "observatory.sqlite3"
        ObservatoryStore(observatory_db).insert_event({
            "schema_version": 1,
            "event_id": "cli-event",
            "kind": "tool",
            "ts": "2026-07-21T03:00:00Z",
            "status": "error",
            "tool_family": "shell",
        })
        report = self.call([
            "process-observatory", "report", "--db", str(observatory_db), "--json"
        ])
        self.assertEqual(report["total"], 1)
        self.assertNotIn("event_id", json.dumps(report))

        improvement_db = self.root / "improvements.sqlite3"
        proposed = self.call([
            "improvement", "propose", "--db", str(improvement_db),
            "--idempotency-key", "cli-proposal", "--title", "PRIVATE PROPOSAL",
            "--risk-class", "low", "--target-kind", "test",
            "--intervention", "PRIVATE INTERVENTION", "--expected-metric", "error_rate",
            "--json",
        ])
        self.assertEqual(proposed["state"], "proposed")
        self.assertNotIn("title", proposed)
        self.assertNotIn("PRIVATE", json.dumps(proposed))
        status = self.call(["improvement", "status", "--db", str(improvement_db), "--json"])
        self.assertEqual(status["states"]["proposed"], 1)

    def test_real_gate_file_uses_required_python311_unittest_command(self) -> None:
        gate = json.loads((Path(__file__).parents[1] / ".hermes-gates.json").read_text())
        commands = [item["argv"] for item in gate["gates"]]
        self.assertIn(
            ["env", "PYTHONPATH=src", "python3.11", "-m", "unittest", "discover", "-s", "tests", "-v"],
            commands,
        )

    def test_manifest_scaffolding_exposes_safe_bplus_and_catchup_defaults(self) -> None:
        generated = self.root / "manifest.json"
        write_default_manifest(generated)
        manifest = json.loads(generated.read_text(encoding="utf-8"))
        self.assertEqual(manifest["dreaming"]["loop_interval_minutes"], 2)
        self.assertEqual(manifest["dreaming"]["max_rounds"], 64)
        self.assertEqual(manifest["dreaming"]["catchup_until_local"], "06:30")
        self.assertIn("lifecycle_db", manifest["dreaming"])
        self.assertIn("improvement_db", manifest["dreaming"])
        b_plus = manifest["b_plus"]
        self.assertEqual(b_plus["observatory_retention_days"], 90)
        self.assertIn("observatory_spool", b_plus)
        self.assertIn("improvement_db", b_plus)
        self.assertIn("claude_wrapper", b_plus["review"])
        self.assertIn("codex_cli", b_plus["review"])
        self.assertIn("codex_auth_file", b_plus["review"])
        self.assertNotIn("hermes_coder", b_plus["review"])
        self.assertNotIn("api_key", json.dumps(b_plus).lower())


if __name__ == "__main__":
    unittest.main()
