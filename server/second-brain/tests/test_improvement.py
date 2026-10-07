"""Behavioral tests for the typed process-improvement queue and the B+
cross-vendor review runner.

Findings are proposals only: the store never applies any file/config/skill
change. The state machine fails closed on invalid transitions. The review
runner consumes only an aggregate, redacted, bounded metadata report, invokes a
primary (Claude subscription) and a second opposite-vendor reviewer through
argv subprocesses (no shell, bounded I/O, timeouts, process-group cleanup), and
persists a proposal only if schema validation passes and the second reviewer
explicitly accepts or downgrades it. A reviewer can never upgrade risk or bypass
approval. A no-data run is a quiet healthy no-op; a reviewer failure leaves
prior state intact and is safely retryable.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from hermes_second_brain.improvement import (
    ImprovementError,
    ImprovementStore,
    InvalidTransition,
)
from hermes_second_brain.review_runner import (
    PRIMARY_OUTPUT_SCHEMA, ReviewerCommand, commands_from_manifest, run_review,
)


class StoreBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "improvements.sqlite3"
        self.store = ImprovementStore(self.db_path)

    def _propose(self, **kw) -> dict:
        params = dict(
            idempotency_key="p1",
            title="Reduce shell tool error rate",
            risk_class="low",
            target_kind="workflow",
            proposed_intervention="Add a retry guard around the shell family",
            expected_metric="error_rate",
        )
        params.update(kw)
        return self.store.propose(**params)


class ImprovementQueueTests(StoreBase):
    def test_proposed_to_kept_happy_path(self) -> None:
        proposal = self._propose()
        pid = proposal["proposal_id"]
        self.assertEqual(proposal["state"], "proposed")
        self.assertEqual(self.store.review(pid)["state"], "reviewed")
        self.assertEqual(self.store.approve(pid)["state"], "approved")
        self.assertEqual(self.store.apply(pid)["state"], "applied")
        canary = self.store.start_canary(pid)
        self.assertEqual(canary["state"], "canary")
        self.assertEqual(canary["canary_state"], "running")
        kept = self.store.keep(pid)
        self.assertEqual(kept["state"], "kept")
        self.assertEqual(kept["canary_state"], "passed")
        self.assertTrue(kept["terminal_at"])

    def test_invalid_transition_fails_closed(self) -> None:
        pid = self._propose()["proposal_id"]
        # Cannot jump straight to applied, bypassing review + approval.
        with self.assertRaises(InvalidTransition):
            self.store.apply(pid)

    def test_rollback_records_failed_canary(self) -> None:
        pid = self._propose()["proposal_id"]
        self.store.review(pid)
        self.store.approve(pid)
        self.store.apply(pid)
        self.store.start_canary(pid)
        rolled = self.store.roll_back(pid, rollback_note="p95 regressed")
        self.assertEqual(rolled["state"], "rolled_back")
        self.assertEqual(rolled["canary_state"], "failed")

    def test_propose_is_idempotent(self) -> None:
        first = self._propose()
        second = self._propose()
        self.assertEqual(first["proposal_id"], second["proposal_id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

    def test_reject_and_expire_are_terminal(self) -> None:
        pid = self._propose()["proposal_id"]
        self.store.reject(pid)
        with self.assertRaises(InvalidTransition):
            self.store.review(pid)

    def test_expire_stale_only_hits_nonterminal(self) -> None:
        pid = self._propose(idempotency_key="stale")["proposal_id"]
        kept_id = self._propose(idempotency_key="fresh")["proposal_id"]
        self.store.review(kept_id)
        self.store.approve(kept_id)
        self.store.apply(kept_id)
        self.store.start_canary(kept_id)
        self.store.keep(kept_id)
        result = self.store.expire_stale(now=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=8)).isoformat(), ttl_days=7)
        self.assertIn(pid, result["expired"])
        self.assertNotIn(kept_id, result["expired"])

    def test_unknown_risk_or_target_is_rejected(self) -> None:
        with self.assertRaises(ImprovementError):
            self._propose(idempotency_key="bad", risk_class="catastrophic")
        with self.assertRaises(ImprovementError):
            self._propose(idempotency_key="bad2", target_kind="production_database")


class PrimaryReviewerMetricContractTests(StoreBase):
    """The primary reviewer's generation contract (JSON schema + prompt) must
    not offer expected_metric values that the durable store validator
    (_bounded_token, exercised here through ImprovementStore.propose) always
    rejects. That mismatch -- an arbitrary-string schema paired with a
    machine-safe-token-only store -- is what made the cross-vendor review
    repeatedly fail.
    """

    def _metric_pattern(self) -> re.Pattern:
        schema = PRIMARY_OUTPUT_SCHEMA["properties"]["proposals"]["items"]["properties"]
        return re.compile(schema["expected_metric"]["pattern"])

    def test_pattern_is_anchored_and_machine_safe(self) -> None:
        pattern = self._metric_pattern()
        self.assertTrue(pattern.fullmatch("error_rate"))
        self.assertTrue(pattern.fullmatch("task.success-rate_1:v2"))
        self.assertIsNone(pattern.fullmatch(""))
        for unsafe in ("error rate", "error/rate", "error,rate", "a\nb", "error_rate "):
            self.assertIsNone(pattern.fullmatch(unsafe), unsafe)

    def test_schema_pattern_agrees_with_store_validation(self) -> None:
        pattern = self._metric_pattern()
        for index, token in enumerate(("error_rate", "task.success-rate_1:v2", "A1")):
            self.assertTrue(pattern.fullmatch(token), token)
            proposal = self._propose(idempotency_key=f"ok-{index}", expected_metric=token)
            self.assertEqual(proposal["expected_metric"], token)

        # This human-readable phrase is exactly the shape that used to pass
        # the generation schema (an unconstrained string) but always failed
        # durable validation -- it must now be excluded by the schema too.
        unsafe = "reduce shell error rate"
        self.assertIsNone(pattern.fullmatch(unsafe))
        with self.assertRaises(ImprovementError):
            self._propose(idempotency_key="bad", expected_metric=unsafe)

    def test_primary_reviewer_prompt_states_machine_safe_metric_contract(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "claude_improvement_reviewer.py"
        text = script.read_text(encoding="utf-8")
        self.assertIn("machine-safe token", text)
        self.assertIn("no spaces", text)


def _fake_reviewer(path: Path, body: str) -> Path:
    script = path
    script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    return script


def _seatbelt_available() -> bool:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


PRIMARY_ONE_PROPOSAL = """
    import json, sys
    json.load(sys.stdin)
    print(json.dumps({"proposals": [{
        "title": "Cut shell error rate",
        "risk_class": "high",
        "target_kind": "workflow",
        "proposed_intervention": "Wrap the shell family in a bounded retry",
        "expected_metric": "error_rate",
        "observation_count": 12,
        "evidence_count": 8,
        "counterevidence_count": 1,
        "rollback_note": "Remove the retry guard"
    }]}))
"""


class ReviewRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = ImprovementStore(self.root / "improvements.sqlite3")
        self.report = {
            "total": 40,
            "small_sample": False,
            "error_rate": 0.2,
            "retry_rate": 0.05,
            "stall_rate": 0.02,
            "unverified_completion_rate": 0.0,
            "duration_ms": {"median": 10.0, "p90": 30.0, "p95": 40.0},
            "top_anomalous_tool_families": [{"tool_family": "shell", "total": 8}],
        }

    def _cmd(self, script: Path, timeout: float = 10.0) -> ReviewerCommand:
        return ReviewerCommand(argv=["python3", str(script)], timeout_seconds=timeout)

    def test_no_data_is_quiet_noop(self) -> None:
        primary = self._cmd(_fake_reviewer(self.root / "p.py", PRIMARY_ONE_PROPOSAL))
        secondary = self._cmd(_fake_reviewer(self.root / "s.py", "print('{}')"))
        result = run_review({"total": 0}, store=self.store, primary=primary, secondary=secondary)
        self.assertEqual(result["status"], "noop")
        self.assertEqual(self.store.count(), 0)

    def test_second_reviewer_downgrade_is_persisted_at_lower_risk(self) -> None:
        primary = self._cmd(_fake_reviewer(self.root / "p.py", PRIMARY_ONE_PROPOSAL))
        secondary = self._cmd(_fake_reviewer(self.root / "s.py", """
            import json, sys
            data = json.load(sys.stdin)
            assert data["proposals"], "secondary must receive primary proposals"
            print(json.dumps({"verdicts": [{"index": 0, "decision": "downgrade", "risk_class": "low"}]}))
        """))
        result = run_review(self.report, store=self.store, primary=primary, secondary=secondary)
        self.assertEqual(result["persisted"], 1)
        proposals = self.store.list()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["risk_class"], "low")
        self.assertEqual(proposals[0]["state"], "reviewed")  # never auto-approved

    def test_second_reviewer_reject_persists_nothing(self) -> None:
        primary = self._cmd(_fake_reviewer(self.root / "p.py", PRIMARY_ONE_PROPOSAL))
        secondary = self._cmd(_fake_reviewer(self.root / "s.py", """
            import json, sys
            json.load(sys.stdin)
            print(json.dumps({"verdicts": [{"index": 0, "decision": "reject"}]}))
        """))
        result = run_review(self.report, store=self.store, primary=primary, secondary=secondary)
        self.assertEqual(result["persisted"], 0)
        self.assertEqual(self.store.count(), 0)

    def test_reviewer_cannot_upgrade_risk(self) -> None:
        # Primary proposes low; secondary tries to upgrade to high -> refused.
        low_primary = PRIMARY_ONE_PROPOSAL.replace('"risk_class": "high"', '"risk_class": "low"')
        primary = self._cmd(_fake_reviewer(self.root / "p.py", low_primary))
        secondary = self._cmd(_fake_reviewer(self.root / "s.py", """
            import json, sys
            json.load(sys.stdin)
            print(json.dumps({"verdicts": [{"index": 0, "decision": "downgrade", "risk_class": "high"}]}))
        """))
        result = run_review(self.report, store=self.store, primary=primary, secondary=secondary)
        self.assertEqual(result["persisted"], 0)
        self.assertEqual(self.store.count(), 0)

    def test_reviewer_failure_leaves_state_intact_and_is_retryable(self) -> None:
        primary = self._cmd(_fake_reviewer(self.root / "p.py", "import sys; sys.exit(3)"))
        secondary = self._cmd(_fake_reviewer(self.root / "s.py", "print('{}')"))
        result = run_review(self.report, store=self.store, primary=primary, secondary=secondary)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.count(), 0)
        # Retry with a working primary now succeeds -- prior state was intact.
        primary_ok = self._cmd(_fake_reviewer(self.root / "p2.py", PRIMARY_ONE_PROPOSAL))
        secondary_ok = self._cmd(_fake_reviewer(self.root / "s2.py", """
            import json, sys
            json.load(sys.stdin)
            print(json.dumps({"verdicts": [{"index": 0, "decision": "accept"}]}))
        """))
        retry = run_review(self.report, store=self.store, primary=primary_ok, secondary=secondary_ok)
        self.assertEqual(retry["persisted"], 1)

    def test_timeout_is_handled_as_failure(self) -> None:
        primary = self._cmd(
            _fake_reviewer(self.root / "p.py", "import time; time.sleep(5)"),
            timeout=0.3,
        )
        secondary = self._cmd(_fake_reviewer(self.root / "s.py", "print('{}')"))
        result = run_review(self.report, store=self.store, primary=primary, secondary=secondary)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.count(), 0)

    def test_default_commands_are_subscription_only_and_read_only(self) -> None:
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"b_plus": {"review": {}}}), encoding="utf-8")
        primary, secondary = commands_from_manifest(manifest)
        primary_text = " ".join(primary.argv)
        secondary_text = " ".join(secondary.argv)
        self.assertIn("claude_improvement_reviewer.py", primary_text)
        self.assertIn("claude-subscription", primary_text)
        self.assertIn("hermes_coder_review_adapter.py", secondary_text)
        self.assertIn("codex", secondary_text)
        self.assertNotIn("hermes-coder", secondary_text)
        self.assertNotIn("--workdir", secondary.argv)
        for script_name in (
            "claude_improvement_reviewer.py",
            "hermes_coder_review_adapter.py",
            "process_improvement_review.py",
        ):
            path = Path(__file__).parents[1] / "scripts" / script_name
            self.assertTrue(path.is_file(), script_name)
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_manifest_primary_is_argv_but_secondary_cannot_bypass_adapter(self) -> None:
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({
            "b_plus": {"review": {
                "primary_argv": ["/fake/claude", "--safe"],
                "primary_timeout_seconds": 12,
                "secondary_timeout_seconds": 13,
            }}
        }), encoding="utf-8")
        primary, secondary = commands_from_manifest(manifest)
        self.assertEqual(primary.argv, ("/fake/claude", "--safe"))
        self.assertIn("hermes_coder_review_adapter.py", " ".join(secondary.argv))
        self.assertEqual(primary.timeout_seconds, 12)
        self.assertEqual(secondary.timeout_seconds, 13)

        manifest.write_text(json.dumps({
            "b_plus": {"review": {"primary_argv": "fake --shell"}}
        }), encoding="utf-8")
        with self.assertRaises(Exception):
            commands_from_manifest(manifest)

        manifest.write_text(json.dumps({
            "b_plus": {"review": {"secondary_argv": ["/fake/codex", "--read-only"]}}
        }), encoding="utf-8")
        with self.assertRaises(Exception):
            commands_from_manifest(manifest)

    def test_review_wrapper_validate_only_is_quiet_and_write_free(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "process_improvement_review.py"
        spec = importlib.util.spec_from_file_location("review_wrapper_fixture", script)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        manifest = self.root / "validate.json"
        manifest.write_text(json.dumps({
            "b_plus": {
                "observatory_db": str(self.root / "missing-observatory.sqlite3"),
                "observatory_spool": str(self.root / "missing-spool.d"),
                "observatory_quarantine": str(self.root / "missing-quarantine.d"),
                "improvement_db": str(self.root / "missing-improvements.sqlite3"),
                "observatory_retention_days": 90,
                "review": {
                    "primary_argv": ["/missing/claude"],
                    "codex_cli": "/missing/codex",
                    "codex_auth_file": "/missing/auth.json",
                },
            }
        }), encoding="utf-8")
        before = {item.name for item in self.root.iterdir()}
        output = io.StringIO()
        with redirect_stdout(output):
            result = module.main(["--config", str(manifest), "--validate-only"])
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual({item.name for item in self.root.iterdir()}, before)


@unittest.skipUnless(_seatbelt_available(), "an applicable macOS seatbelt sandbox is required")
class SubscriptionAdapterIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.adapter = Path(__file__).parents[1] / "scripts" / "hermes_coder_review_adapter.py"
        self.auth = self.root / "auth.json"
        self.auth.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
        os.chmod(self.auth, 0o600)

    def _fake_codex(
        self, *, login: str = "Logged in using ChatGPT", login_to_stderr: bool = False
    ) -> Path:
        sentinel = self.root / "outside-sandbox-sentinel.txt"
        sentinel.write_text("REPOSITORY-PRIVATE-SENTINEL", encoding="utf-8")
        return _fake_reviewer(self.root / "codex", f'''
            import json, os, pathlib, sys
            args = sys.argv[1:]
            if args == ["login", "status"]:
                print({login!r}, file=sys.stderr if {login_to_stderr!r} else sys.stdout)
                raise SystemExit(0)
            if len(args) >= 2 and args[-2:] == ["features", "list"]:
                for name in (
                    "apps", "browser_use", "browser_use_external",
                    "browser_use_full_cdp_access", "code_mode", "code_mode_host",
                    "computer_use", "enable_mcp_apps", "image_generation",
                    "multi_agent", "multi_agent_v2", "plugins", "shell_tool",
                    "tool_suggest", "unified_exec",
                ):
                    print(f"{{name}} stable false")
                raise SystemExit(0)
            if "exec" not in args:
                raise SystemExit(4)
            prompt = sys.stdin.read()
            if "REVIEW_PACKET (JSON):" not in prompt:
                raise SystemExit(5)
            try:
                pathlib.Path({str(sentinel)!r}).read_text(encoding="utf-8")
            except (OSError, PermissionError):
                pass
            else:
                raise SystemExit(6)
            forbidden = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL",
                         "AWS_SECRET_ACCESS_KEY", "REVIEW_PRIVATE_SENTINEL")
            if any(name in os.environ for name in forbidden):
                raise SystemExit(7)
            if list(pathlib.Path.cwd().iterdir()):
                raise SystemExit(8)
            print(json.dumps({{"verdicts": [{{"index": 0, "decision": "accept"}}]}}))
        ''')

    def _run(self, runner: Path, packet: dict, *, env: dict[str, str] | None = None):
        command = [
            sys.executable,
            str(self.adapter),
            "--runner", str(runner),
            "--auth-file", str(self.auth),
            "--sandbox-exec", "/usr/bin/sandbox-exec",
        ]
        return subprocess.run(
            command,
            input=json.dumps(packet),
            text=True,
            capture_output=True,
            timeout=10,
            env=env,
            check=False,
        )

    def _packet(self, proposals: list[dict]) -> dict:
        return {
            "schema_version": 1,
            "mode": "independent_read_only_review",
            "constraints": {
                "may_upgrade_risk": False,
                "may_approve_application": False,
                "allowed_decisions": ["accept", "downgrade", "reject"],
            },
            "report": {
                "total": 1,
                "small_sample": True,
                "sample_size": 1,
                "by_kind": {"tool": 1},
                "by_status": {"ok": 1},
                "error_rate": 0.0,
                "retry_rate": 0.0,
                "stall_rate": 0.0,
                "unverified_completion_rate": 0.0,
                "duration_ms": {"count": 1, "median": 1.0, "p90": 1.0, "p95": 1.0},
                "top_anomalous_tool_families": [],
                "top_anomalous_task_classes": [],
            },
            "proposals": proposals,
        }

    def test_fake_subscription_reviewer_is_read_isolated_and_environment_scrubbed(self) -> None:
        runner = self._fake_codex()
        env = dict(os.environ)
        env.update({
            "OPENAI_API_KEY": "sk-" + "direct-billing-must-not-leak",
            "ANTHROPIC_API_KEY": "anthropic-secret-must-not-leak",
            "OPENAI_BASE_URL": "https://override.invalid",
            "AWS_SECRET_ACCESS_KEY": "cloud-secret-must-not-leak",
            "REVIEW_PRIVATE_SENTINEL": "environment-secret-must-not-leak",
        })
        result = self._run(runner, self._packet([{
                "title": "bounded aggregate only",
                "risk_class": "low",
                "target_kind": "workflow",
                "proposed_intervention": "Add a bounded retry guard",
                "expected_metric": "error_rate",
                "observation_count": 1,
                "evidence_count": 1,
                "counterevidence_count": 0,
                "rollback_note": "Remove the guard",
            }]), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"verdicts": [{"index": 0, "decision": "accept"}]},
        )
        self.assertNotIn("SENTINEL", result.stdout + result.stderr)

    def test_subscription_auth_attestation_accepts_real_codex_stderr_contract(self) -> None:
        runner = self._fake_codex(login_to_stderr=True)
        result = self._run(runner, self._packet([]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"verdicts": [{"index": 0, "decision": "accept"}]})

    def test_non_subscription_auth_fails_closed_without_output(self) -> None:
        runner = self._fake_codex(login="Logged in using an API key")
        result = self._run(runner, self._packet([]))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class SubscriptionAdapterContractTests(unittest.TestCase):
    def test_valid_fake_subscription_review_succeeds_without_leaked_environment(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "unreadable-sentinel"
            sentinel.write_text("PRIVATE-SENTINEL", encoding="utf-8")
            os.chmod(sentinel, 0)
            auth = root / "auth.json"
            auth.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
            os.chmod(auth, 0o600)
            runner = _fake_reviewer(root / "codex", f'''
                import json, os, pathlib, sys
                args = sys.argv[1:]
                if args == ["login", "status"]:
                    print("Logged in using ChatGPT")
                    raise SystemExit(0)
                if len(args) >= 2 and args[-2:] == ["features", "list"]:
                    for name in (
                        "apps", "browser_use", "browser_use_external",
                        "browser_use_full_cdp_access", "code_mode", "code_mode_host",
                        "computer_use", "enable_mcp_apps", "image_generation",
                        "multi_agent", "multi_agent_v2", "plugins", "shell_tool",
                        "tool_suggest", "unified_exec",
                    ):
                        print(f"{{name}} stable false")
                    raise SystemExit(0)
                if "exec" not in args or "OPENAI_API_KEY" in os.environ:
                    raise SystemExit(3)
                if list(pathlib.Path.cwd().iterdir()):
                    raise SystemExit(4)
                try:
                    pathlib.Path({str(sentinel)!r}).read_text(encoding="utf-8")
                except OSError:
                    pass
                else:
                    raise SystemExit(5)
                if "REVIEW_PACKET (JSON):" not in sys.stdin.read():
                    raise SystemExit(6)
                print(json.dumps({{"verdicts": [{{"index": 0, "decision": "accept"}}]}}))
            ''')
            harness = _fake_reviewer(root / "test-seatbelt-harness", """
                import os, sys
                if len(sys.argv) < 4 or sys.argv[1] != "-p":
                    raise SystemExit(2)
                os.execv(sys.argv[3], sys.argv[3:])
            """)
            script = Path(__file__).parents[1] / "scripts" / "hermes_coder_review_adapter.py"
            spec = importlib.util.spec_from_file_location("subscription_adapter_contract", script)
            adapter = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(adapter)

            packet = {
                "schema_version": 1,
                "mode": "independent_read_only_review",
                "constraints": {
                    "may_upgrade_risk": False,
                    "may_approve_application": False,
                    "allowed_decisions": ["accept", "downgrade", "reject"],
                },
                "report": {
                    "total": 1,
                    "small_sample": True,
                    "sample_size": 1,
                    "by_kind": {"tool": 1},
                    "by_status": {"ok": 1},
                    "error_rate": 0.0,
                    "retry_rate": 0.0,
                    "stall_rate": 0.0,
                    "unverified_completion_rate": 0.0,
                    "duration_ms": {
                        "count": 1, "median": 1.0, "p90": 1.0, "p95": 1.0
                    },
                    "top_anomalous_tool_families": [],
                    "top_anomalous_task_classes": [],
                },
                "proposals": [{
                    "title": "Bounded proposal",
                    "risk_class": "low",
                    "target_kind": "workflow",
                    "proposed_intervention": "Add a bounded guard",
                    "expected_metric": "error_rate",
                    "observation_count": 1,
                    "evidence_count": 1,
                    "counterevidence_count": 0,
                    "rollback_note": "Remove the guard",
                }],
            }

            class BinaryStream:
                def __init__(self, initial: bytes = b"") -> None:
                    self.buffer = io.BytesIO(initial)

            stdin = BinaryStream(json.dumps(packet).encode("utf-8"))
            stdout = BinaryStream()
            with (
                # This harness tests the contract, not host sandbox isolation.
                mock.patch.object(adapter.sys, "platform", "darwin"),
                mock.patch.object(adapter, "_safe_sandbox_exec", return_value=harness),
                mock.patch.object(adapter.sys, "stdin", stdin),
                mock.patch.object(adapter.sys, "stdout", stdout),
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}, clear=False),
            ):
                result = adapter.main([
                    "--runner", str(runner),
                    "--auth-file", str(auth),
                    "--sandbox-exec", str(harness),
                ])
            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(stdout.buffer.getvalue()),
                {"verdicts": [{"index": 0, "decision": "accept"}]},
            )


_PRODUCTION_CODEX = Path("/opt/homebrew/bin/codex")


def _production_codex_login_is_chatgpt() -> bool:
    """True only when a real Homebrew Codex is installed and ChatGPT-authenticated.

    Uses only the read-only ``login status`` probe, so it never consumes model
    quota.  Any failure (missing binary, API-key auth, logged out, timeout)
    returns False so the probe suite skips instead of asserting on an
    environment where the subscription contract cannot be positively proven.
    """

    # Portable package tests must not inspect a developer's authenticated CLI.
    # Keep the original live contract probe available as an explicit check.
    if os.environ.get("HERMES_TEST_LIVE_CODEX") != "1" or not _PRODUCTION_CODEX.is_file():
        return False
    try:
        result = subprocess.run(
            [str(_PRODUCTION_CODEX), "login", "status"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and b"Logged in using ChatGPT" in (
        result.stdout.strip() + b"\n" + result.stderr.strip()
    )


@unittest.skipUnless(
    _production_codex_login_is_chatgpt(),
    "set HERMES_TEST_LIVE_CODEX=1 with a ChatGPT-authenticated Homebrew Codex CLI",
)
class ProductionCodexContractProbeTests(unittest.TestCase):
    """Probe the live Homebrew Codex CLI contract the isolation adapter relies on.

    These tests only run Codex's ``login status`` and feature-inspection
    subcommands -- never ``exec`` -- so they cannot consume model quota.  They
    fail if the installed Codex can no longer prove the subscription/no-tools
    contract, which mirrors the adapter's runtime fail-closed behaviour and
    warns before a Codex upgrade silently breaks isolation.
    """

    def setUp(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "hermes_coder_review_adapter.py"
        spec = importlib.util.spec_from_file_location("production_codex_probe_adapter", script)
        self.adapter = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(self.adapter)

    def _codex(self, *args: str) -> bytes:
        return subprocess.run(
            [str(_PRODUCTION_CODEX), *args],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout

    def test_login_status_matches_adapter_subscription_expectation(self) -> None:
        result = subprocess.run(
            [str(_PRODUCTION_CODEX), "login", "status"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            b"Logged in using ChatGPT",
            result.stdout.strip() + b"\n" + result.stderr.strip(),
        )

    def test_feature_inventory_and_disable_attestation_prove_no_tools(self) -> None:
        inventory = self.adapter._parse_features(self._codex("features", "list"))
        # Every tool surface the adapter guards is really advertised by Codex.
        self.assertTrue(self.adapter._REQUIRED_TOOL_FEATURES.issubset(inventory))
        # At least one tool feature ships enabled, so the disable step is load-bearing.
        self.assertTrue(
            any(enabled for stage, enabled in inventory.values() if stage != "removed")
        )
        disable_names = sorted(
            name for name, (stage, _) in inventory.items() if stage != "removed"
        )
        disable_args = [part for name in disable_names for part in ("--disable", name)]
        attested = self.adapter._parse_features(self._codex(*disable_args, "features", "list"))
        # The inventory is stable and every non-removed feature is now disabled;
        # otherwise the adapter must (and does) refuse to run the reviewer.
        self.assertEqual(set(attested), set(inventory))
        self.assertFalse(
            any(enabled for stage, enabled in attested.values() if stage != "removed"),
            "Codex features could not be fully disabled; adapter must fail closed",
        )


if __name__ == "__main__":
    unittest.main()
