from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class WatchMultisourceTests(unittest.TestCase):
    def test_completed_backfill_is_reimported_only_when_its_signature_changes(self) -> None:
        script = Path.cwd() / "scripts/context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_backfill_state", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "slack-backfill-complete.jsonl.gz"
            state_path = root / "context-watch-state.json"
            artifact.write_bytes(b"first")
            state: dict[str, list[int]] = {}

            self.assertTrue(module.should_import_artifact(artifact, state))
            module.record_artifact_signature(state_path, state, artifact)
            self.assertFalse(module.should_import_artifact(artifact, state))

            artifact.write_bytes(b"changed")
            self.assertTrue(module.should_import_artifact(artifact, state))

    def test_optional_scanner_failure_does_not_stop_ranking(self) -> None:
        script = Path.cwd() / "scripts/context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_multisource", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            calls = []

            def run_cli(python, package, env, args, **kwargs):
                calls.append(args[0])
                if args[0] == "context-scan":
                    raise RuntimeError("optional source unavailable")

            with (
                mock.patch.dict(os.environ, {
                    "CONTEXT_IMPORT_DIR": str(root / "imports"),
                    "CONTEXT_DB": str(root / "inbox.sqlite3"),
                    "CONTEXT_EXPORT": str(root / "export.txt"),
                    "CONTEXT_WATCH_STATE": str(root / "watch.json"),
                    "WHATSAPP_IMPORT_PATH": str(root / "missing.sqlite3"),
                }),
                mock.patch.object(module, "import_jsonl_spool", return_value=0),
                mock.patch.object(module, "import_if_exists"),
                mock.patch.object(module, "import_slack_live_if_exists"),
                mock.patch.object(module, "run_cli", side_effect=run_cli),
                mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as alerts,
            ):
                self.assertEqual(module.main(), 0)

            self.assertEqual(calls, ["context-scan", "context-export-openviking"])
            self.assertIn("--prepared", alerts.call_args.args[0])
