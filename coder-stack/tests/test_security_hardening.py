"""Adversarial regression tests for the Phase A+B+C hardening pass.

Each test here reproduces one concrete finding from the final security review.
They are deliberately hostile: the model stub, the gate stub, and the inherited
environment all behave like an attacker who controls the writer being judged.
"""
import binascii
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from tests.test_hermes_coder_flow import (
    FLOW,
    PASS_REVIEW,
    ROOT,
    RUNNER,
    FlowTestCase,
    classify_block,
    doctor_json,
)


def reap_process(proc):
    """Stop and reap the exact subprocess object created by a test."""
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


_process_identity_reader = None


def current_process_identity(pid):
    """Use the production identity reader before any test-owned PID signal."""
    global _process_identity_reader
    if _process_identity_reader is None:
        _process_identity_reader = load_flow_module().process_identity
    return _process_identity_reader(pid)


def read_process_record(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        pid = document["pid"]
        identity = document["identity"]
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(pid, int) or pid <= 1 or not isinstance(identity, str):
        return None
    return pid, identity


def wait_for_recorded_exit(record, timeout):
    pid, identity = record
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if current_process_identity(pid) != identity:
            return True
        time.sleep(0.05)
    return current_process_identity(pid) != identity


def reap_record(record, process_group=False):
    """Signal only while the recorded kernel identity owns the exact number."""
    if record is None:
        return
    pid, identity = record
    if current_process_identity(pid) != identity:
        return
    try:
        if process_group and os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


PROCESS_RECORD_SOURCE = r'''
def process_identity(pid):
    if sys.platform.startswith("linux"):
        try:
            raw = open("/proc/{}/stat".format(pid), "rb").read()
        except OSError:
            return None
        fields = raw.rsplit(b")", 1)[-1].split()
        return "linux:" + fields[19].decode("ascii") if len(fields) >= 20 else None
    if sys.platform == "darwin":
        class ProcBSDInfo(ctypes.Structure):
            _fields_ = [
                ("head", ctypes.c_uint32 * 12),
                ("comm", ctypes.c_char * 16),
                ("name", ctypes.c_char * 32),
                ("tail", ctypes.c_uint32 * 6),
                ("start_sec", ctypes.c_uint64),
                ("start_usec", ctypes.c_uint64),
            ]
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib")
            function = library.proc_pidinfo
            function.argtypes = (
                ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                ctypes.c_void_p, ctypes.c_int,
            )
            function.restype = ctypes.c_int
            info = ProcBSDInfo()
            size = function(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        except (AttributeError, OSError):
            return None
        if size < ctypes.sizeof(info):
            return None
        return "darwin:{}:{}".format(info.start_sec, info.start_usec)
    return None

def write_process_record(path, pid):
    identity = process_identity(pid)
    if identity is None:
        raise SystemExit("cannot record stable process identity")
    with open(path, "w") as handle:
        json.dump({"pid": pid, "identity": identity}, handle)
        handle.flush()
        os.fsync(handle.fileno())
'''


def reap_recorded_model_group(leader_file, descendant_file):
    """Kill only identities recorded by this exact test invocation."""
    recorded = {}
    for name, path in (
        ("leader", Path(leader_file)),
        ("descendant", Path(descendant_file)),
    ):
        record = read_process_record(path)
        if record is not None:
            recorded[name] = record
    if not recorded:
        return
    leader = recorded.get("leader")
    if leader is not None:
        reap_record(leader, process_group=True)
    for record in recorded.values():
        reap_record(record)
    for record in recorded.values():
        wait_for_recorded_exit(record, 5)


class VendorAttestationTest(FlowTestCase):
    """HIGH 1 -- the implementation vendor must not be forgeable."""

    def test_gate_cannot_forge_the_implementation_vendor_via_the_stage_journal(self):
        # Claude really implements. A gate appends a forged "codex succeeded"
        # record to the stage journal before the genuine run_end is written.
        self.env["GATE_CHECK"] = "forge_vendor"
        self.write_plan({
            "implement": {"claude": {"touch": ["implemented.txt"]}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = self.state_documents()[0]
        # The forgery must not move attribution away from the real writer.
        self.assertEqual(document["implementation_vendor"], "claude")
        self.assertEqual(document["reviewer_vendor"], "codex")
        self.assertEqual([c["vendor"] for c in self.stages("review")], ["codex"])

    def test_model_and_gate_environment_hides_the_attestation_channel(self):
        self.passing_plan()
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertEqual(result.returncode, 0, result.stderr)
        implement = self.stages("implement")
        self.assertTrue(implement)
        for call in implement:
            # Neither the result FD/token nor the private state paths leak.
            self.assertEqual(call["coder_paths"], [], call)
            self.assertEqual(call["inherited_fds"], [], call)

    def test_a_prepopulated_stage_journal_cannot_determine_the_vendor(self):
        # The runner never starts, so no attestation is produced; a journal
        # planted at the expected path must not stand in for one.
        stage_dir = self.state_dir / "stages"
        stage_dir.mkdir(parents=True)
        self.write_plan({
            "implement": {"touch": ["implemented.txt"], "attest": False},
            "review": {"stdout": PASS_REVIEW},
        })
        result = self.run_flow(
            "--runner", str(self.fake_runner), "--lane", "normal", "add a feature",
        )
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("determine the implementation vendor", result.stderr)

    def test_attestation_with_the_wrong_run_identity_is_rejected(self):
        self.write_plan({
            "implement": {
                "vendor": "claude",
                "attestation_overrides": {"flow_run_id": "0" * 32},
            },
        })
        result = self.run_flow(
            "--runner", str(self.fake_runner), "--lane", "normal", "add a feature",
        )
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("secure runner attestation", result.stderr)


class FrozenGateIntegrityTest(FlowTestCase):
    """HIGH 2 -- the writer must not be able to weaken the gates judging it."""

    def test_writer_replacing_the_gate_executable_is_detected(self):
        self.write_plan({
            "implement": {"*": {
                "touch": ["implemented.txt"],
                "write_abs": [[str(self.gate_binary), "#!/bin/sh\nexit 0\n"]],
            }},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("gate", result.stderr.lower())
        self.assertIn("integrity", result.stderr.lower())

    def test_writer_replacing_a_configured_interpreter_script_is_detected(self):
        gate_script = self.base / "policy-gate.py"
        gate_script.write_text("raise SystemExit(9)\n", encoding="utf-8")
        (self.source / ".hermes-gates.json").write_text(
            json.dumps({
                "version": 1,
                "gates": [{"name": "policy", "argv": [sys.executable, str(gate_script)]}],
            }),
            encoding="utf-8",
        )
        committed = self.git("add", ".hermes-gates.json")
        self.assertEqual(committed.returncode, 0, committed.stderr)
        committed = self.git("commit", "--quiet", "-m", "use interpreter gate")
        self.assertEqual(committed.returncode, 0, committed.stderr)
        self.write_plan({
            "implement": {"*": {
                "touch": ["implemented.txt"],
                "write_abs": [[str(gate_script), "raise SystemExit(0)\n"]],
            }},
        })
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("integrity", result.stderr.lower())

    def test_writer_replacing_the_frozen_gate_policy_is_detected(self):
        self.write_plan({
            "implement": {"*": {"touch": ["implemented.txt"], "weaken_gates": True}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("frozen gate", result.stderr.lower())

    def test_gate_that_modifies_the_worktree_fails_closed(self):
        self.env["GATE_CHECK"] = "mutate"
        self.write_plan({
            "implement": {"*": {"touch": ["implemented.txt"]}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("modified the worktree", result.stderr.lower())

    def test_frozen_gate_file_is_not_writable_through_the_model_environment(self):
        self.passing_plan()
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertEqual(result.returncode, 0, result.stderr)
        frozen = sorted(self.state_dir.glob("gates-*.json"))
        self.assertEqual(len(frozen), 1, frozen)
        self.assertEqual(frozen[0].stat().st_mode & 0o777, 0o400)


class ProcessGroupCleanupTest(FlowTestCase):
    """HIGH 3 -- timeout cleanup must not leave descendants running."""

    def test_runner_kills_a_descendant_that_ignores_sigterm(self):
        leader_file = self.base / "runner-leader.pid"
        survivor = self.base / "runner-survivor.pid"
        self.addCleanup(reap_recorded_model_group, leader_file, survivor)
        stub = self.bin / "surviving-model"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import ctypes, json, os, signal, sys, time\n"
            + PROCESS_RECORD_SOURCE
            + "\nwrite_process_record({!r}, os.getpid())\n"
            "for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
            "    signal.signal(signum, signal.SIG_IGN)\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(120)\n"
            "    os._exit(0)\n"
            "write_process_record({!r}, child)\n"
            "time.sleep(120)\n".format(str(leader_file), str(survivor)),
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = dict(self.env)
        env["HERMES_CODER_CLAUDE"] = str(stub)
        env["HERMES_CODER_CODEX"] = str(stub)
        workdir = self.base / "runner-workdir"
        workdir.mkdir()
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--workdir", str(workdir),
             "--lane", "fast", "--no-escalate", "--max-attempts", "1",
             "--attempt-timeout", "2", "--wall-timeout", "20",
             "--no-journal", "--no-circuit", "task"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=90, check=False,
        )
        self.assertTrue(leader_file.exists(), result.stderr)
        self.assertTrue(survivor.exists(), result.stderr)
        leader = read_process_record(leader_file)
        descendant = read_process_record(survivor)
        self.assertIsNotNone(leader)
        self.assertIsNotNone(descendant)
        self.assertTrue(wait_for_recorded_exit(leader, 15), "model leader {} survived timeout".format(leader[0]))
        self.assertTrue(
            wait_for_recorded_exit(descendant, 15),
            "descendant {} survived the runner timeout cleanup".format(descendant[0]),
        )

    def test_flow_kills_a_descendant_that_ignores_sigterm(self):
        self.addCleanup(
            reap_recorded_model_group, self.model_pid_file, self.survivor_pid_file
        )
        self.write_plan({"implement": {"*": {"survivor": True}}})
        result = self.run_flow(
            "--lane", "normal", "--stage-timeout", "3", "--wall-timeout", "60",
            "--attempt-timeout", "3", "add a feature",
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertTrue(self.model_pid_file.exists(), result.stderr)
        self.assertTrue(self.survivor_pid_file.exists(), result.stderr)
        leader = read_process_record(self.model_pid_file)
        descendant = read_process_record(self.survivor_pid_file)
        self.assertIsNotNone(leader)
        self.assertIsNotNone(descendant)
        self.assertTrue(wait_for_recorded_exit(leader, 20), "model leader survived flow timeout")
        self.assertTrue(
            wait_for_recorded_exit(descendant, 20),
            "descendant {} survived the flow stage timeout cleanup".format(descendant[0]),
        )

    def test_flow_cleans_registered_group_after_runner_is_sigkilled(self):
        runner_file = self.base / "abrupt-runner.json"
        group_file = self.base / "abrupt-group.json"
        descendant_file = self.base / "abrupt-descendant.json"
        wrapper = self.bin / "recording-runner"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            + "import binascii, ctypes, json, os, subprocess, sys, time\n"
            + PROCESS_RECORD_SOURCE
            + "\nif '--doctor' in sys.argv:\n"
            + "    print({!r})\n".format(doctor_json())
            + "    raise SystemExit(0)\n"
            + "env = dict(os.environ)\n"
            + "for name in ('HERMES_CODER_SHUTDOWN_FD', 'HERMES_CODER_RESULT_FD',\n"
            + "             'HERMES_CODER_RESULT_TOKEN'):\n"
            + "    env.pop(name, None)\n"
            + "model = subprocess.Popen([os.environ['ABRUPT_MODEL']], env=env,\n"
            + "                         start_new_session=True, close_fds=True)\n"
            + "identity = process_identity(model.pid)\n"
            + "if identity is None:\n"
            + "    raise SystemExit('model identity unavailable')\n"
            + "frame = b'H1 P %d %s\\n' % (\n"
            + "    model.pid, binascii.hexlify(identity.encode('utf-8')))\n"
            + "fd = int(os.environ['HERMES_CODER_SHUTDOWN_FD'])\n"
            + "os.write(fd, frame)\n"
            + "write_process_record({!r}, os.getpid())\n".format(str(runner_file))
            + "time.sleep(120)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        model = self.bin / "abrupt-model"
        model.write_text(
            "#!/usr/bin/env python3\n"
            + "import ctypes, json, os, signal, sys, time\n"
            + PROCESS_RECORD_SOURCE
            + "\nwrite_process_record({!r}, os.getpgrp())\n".format(str(group_file))
            + "for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
            + "    signal.signal(signum, signal.SIG_IGN)\n"
            + "child = os.fork()\n"
            + "if child == 0:\n"
            + "    write_process_record({!r}, os.getpid())\n".format(str(descendant_file))
            + "    time.sleep(120)\n"
            + "    os._exit(0)\n"
            + "time.sleep(120)\n",
            encoding="utf-8",
        )
        model.chmod(0o755)
        env = dict(self.env)
        env["ABRUPT_MODEL"] = str(model)
        flow = subprocess.Popen(
            [sys.executable, str(FLOW), "--source", str(self.source),
             "--runner", str(wrapper), "--lane", "normal", "--no-gates",
             "--wall-timeout", "120", "abrupt runner cleanup"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(reap_process, flow)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if runner_file.exists() and group_file.exists() and descendant_file.exists():
                break
            if flow.poll() is not None:
                break
            time.sleep(0.05)
        if not runner_file.exists():
            stdout, stderr = flow.communicate(timeout=5)
            self.fail("runner was not recorded (exit {}): {} {}".format(
                flow.returncode, stdout, stderr
            ))
        group = read_process_record(group_file)
        descendant = read_process_record(descendant_file)
        runner = read_process_record(runner_file)
        self.assertIsNotNone(group)
        self.assertIsNotNone(descendant)
        self.assertIsNotNone(runner)
        self.addCleanup(reap_record, group, True)
        self.addCleanup(reap_record, descendant)
        self.addCleanup(reap_record, runner)
        # The runner record follows the complete P frame, fixing SIGKILL/EOF
        # ordering without ever discovering processes by name or global scan.
        reap_record(runner)
        _stdout, stderr = flow.communicate(timeout=45)
        self.assertNotEqual(flow.returncode, 0, stderr)
        self.assertTrue(wait_for_recorded_exit(group, 15))
        self.assertTrue(wait_for_recorded_exit(descendant, 15))

    def _assert_direct_runner_signal_cleanup(self, signum, label):
        leader_file = self.base / "direct-{}-leader.pid".format(label)
        descendant_file = self.base / "direct-{}-descendant.pid".format(label)
        self.addCleanup(reap_recorded_model_group, leader_file, descendant_file)
        stub = self.bin / "direct-signal-model-{}".format(label)
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import ctypes, json, os, signal, sys, time\n"
            + PROCESS_RECORD_SOURCE
            + "\nwrite_process_record({!r}, os.getpid())\n"
            "for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
            "    signal.signal(signum, signal.SIG_IGN)\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(120)\n"
            "    os._exit(0)\n"
            "write_process_record({!r}, child)\n"
            "time.sleep(120)\n".format(str(leader_file), str(descendant_file)),
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = dict(self.env)
        env["HERMES_CODER_CLAUDE"] = str(stub)
        env["HERMES_CODER_CODEX"] = str(stub)
        workdir = self.base / "direct-workdir-{}".format(label)
        workdir.mkdir()
        proc = subprocess.Popen(
            [sys.executable, str(RUNNER), "--workdir", str(workdir),
             "--lane", "fast", "--no-escalate", "--max-attempts", "1",
             "--attempt-timeout", "120", "--wall-timeout", "120",
             "--no-journal", "--no-circuit", "task"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(reap_process, proc)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if leader_file.exists() and descendant_file.exists():
                break
            time.sleep(0.05)
        self.assertTrue(leader_file.exists(), "direct model leader never appeared")
        self.assertTrue(descendant_file.exists(), "direct model descendant never appeared")
        leader = read_process_record(leader_file)
        descendant = read_process_record(descendant_file)
        self.assertIsNotNone(leader)
        self.assertIsNotNone(descendant)
        proc.send_signal(signum)
        _stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 130, stderr)
        self.assertTrue(wait_for_recorded_exit(leader, 15), "model leader {} survived {}".format(leader[0], label))
        self.assertTrue(
            wait_for_recorded_exit(descendant, 15),
            "model descendant {} survived {}".format(descendant[0], label),
        )

    def test_direct_runner_sigterm_cleans_current_model_group(self):
        self._assert_direct_runner_signal_cleanup(signal.SIGTERM, "sigterm")

    def test_direct_runner_sighup_cleans_current_model_group(self):
        self._assert_direct_runner_signal_cleanup(signal.SIGHUP, "sighup")


class RelativeRootTest(FlowTestCase):
    """HIGH 4 -- relative roots must resolve once, against the launch cwd."""

    def test_relative_worktree_and_state_roots_resolve_against_the_launch_cwd(self):
        launch = self.base / "launch"
        launch.mkdir()
        env = dict(self.env)
        env.pop("HERMES_FLOW_STATE_DIR", None)
        env.pop("HERMES_FLOW_WORKTREE_ROOT", None)
        env["HERMES_FLOW_LOG"] = str(launch / "logs" / "flow.jsonl")
        self.passing_plan()
        result = subprocess.run(
            [sys.executable, str(FLOW), "--source", str(self.source),
             "--worktree-root", "relative-worktrees",
             "--state-dir", "relative-state",
             "--journal", "relative-logs/flow.jsonl",
             "--lane", "normal", "add a feature"],
            cwd=str(launch), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        created = sorted((launch / "relative-worktrees").glob("flow-*"))
        self.assertEqual(len(created), 1, created)
        self.assertTrue((created[0] / "implemented.txt").exists())
        # Nothing resolved against the source checkout or a stage workdir.
        self.assertFalse((self.source / "relative-worktrees").exists())
        self.assertFalse((created[0] / "relative-state").exists())
        self.assertTrue((launch / "relative-state").is_dir())
        self.assertTrue((launch / "relative-logs" / "flow.jsonl").is_file())
        self.assertFalse((created[0] / "relative-logs").exists())


class ClassificationAttemptTest(FlowTestCase):
    """HIGH 5 -- a failed attempt must never supply the accepted verdict."""

    def test_failed_attempt_marker_is_not_accepted_over_a_failsafe(self):
        # Codex is primary for the read-only inspect task. It emits a valid
        # "fast" block and then fails; Claude would succeed with garbage.
        self.write_plan({
            "classify": {
                "codex": {"stdout": classify_block("fast"), "exit": 4},
                "claude": {"stdout": "no marker here at all"},
            },
            "implement": {"*": {"touch": ["implemented.txt"]}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("add a feature")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = next(r for r in self.journal_records() if r["event"] == "lane_selected")
        self.assertEqual(record["lane"], "complex")
        # Classification is a single attempt: the fallback vendor never runs.
        self.assertEqual([c["vendor"] for c in self.stages("classify")], ["codex"])


class InheritedGitEnvironmentTest(FlowTestCase):
    """HIGH 6 -- inherited Git control variables must not redirect any stage."""

    def test_hostile_git_control_variables_are_stripped_from_subprocesses(self):
        env = dict(self.env)
        env["GIT_DIR"] = str(self.source / ".git")
        env["GIT_WORK_TREE"] = str(self.source)
        env["GIT_INDEX_FILE"] = str(self.source / ".git" / "index")
        env["GIT_OBJECT_DIRECTORY"] = str(self.source / ".git" / "objects")
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(self.source / ".git" / "objects")
        env["GIT_COMMON_DIR"] = str(self.source / ".git")
        self.passing_plan()
        result = self.run_flow("--lane", "normal", "add a feature", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(calls)
        for call in calls:
            self.assertEqual(call["git_control"], [], call)
        self.assertFalse(self.gate_env_leaks.exists())
        worktrees = self.worktree_dirs()
        self.assertEqual(len(worktrees), 1, worktrees)
        self.assertTrue((self.worktrees / worktrees[0] / "implemented.txt").exists())
        self.assertFalse((self.source / "implemented.txt").exists())


class QuotaClassificationTest(unittest.TestCase):
    """MEDIUM 7 -- ordinary output must not poison the persistent circuit."""

    def setUp(self):
        self.module = load_runner_module()

    def classify(self, text):
        classifier = self.module.StreamClassifier()
        classifier.feed(text.encode("utf-8"))
        return classifier.reason_id

    def test_ordinary_task_and_test_output_is_not_quota_or_auth(self):
        benign = (
            "assert response.status_code == 429",
            "status code: 429",
            "HTTP 401 Unauthorized is returned for bad input",
            "error 401",
            "test_authentication_failed ... ok",
            "authentication failed",
            "authorization required",
            "FAIL: test_login_required",
            "not logged in",
            "raise PermissionError('authorization failed')",
        )
        for line in benign:
            self.assertIsNone(self.classify(line), line)

    def test_real_cli_quota_and_auth_signatures_still_classify(self):
        signatures = {
            "Claude usage limit reached. Your limit resets at 4pm.": "quota_usage_limit",
            "quota has been exhausted for this subscription": "quota_exhausted",
            "insufficient_quota: please add credits": "quota_insufficient",
            "invalid_grant: refresh token is no longer valid": "auth_invalid_grant",
            "OAuth token authentication failed for your account": "auth_failed",
            "Invalid API key provided": "auth_login_required",
            "Ihre Abos sind derzeit ausgeschoepft": "quota_wrapper_exhausted",
            "API rate limit exceeded, retry after 60s": "quota_rate_limit",
        }
        for line, expected in signatures.items():
            self.assertEqual(self.classify(line), expected, line)


class ReviewConsistencyTest(unittest.TestCase):
    """MEDIUM 9 -- a pass must not carry medium or worse correctness findings."""

    def setUp(self):
        self.module = load_flow_module()

    def test_pass_with_a_medium_finding_is_rejected(self):
        payload = {
            "verdict": "pass",
            "severity": "medium",
            "summary": "mostly fine",
            "findings": [{"severity": "medium", "title": "wrong result",
                          "detail": "the computed total is off by one"}],
        }
        self.assertIsNone(self.module.validate_review(payload))

    def test_pass_with_only_low_findings_is_accepted(self):
        payload = {
            "verdict": "pass",
            "severity": "low",
            "summary": "nit only",
            "findings": [{"severity": "low", "title": "naming",
                          "detail": "prefer a clearer variable name"}],
        }
        report = self.module.validate_review(payload)
        self.assertIsNotNone(report)
        self.assertEqual(report.verdict, "pass")

    def test_declared_severity_must_match_the_findings(self):
        payload = {
            "verdict": "fail",
            "severity": "low",
            "summary": "understated",
            "findings": [{"severity": "critical", "title": "data loss",
                          "detail": "the migration drops the table"}],
        }
        self.assertIsNone(self.module.validate_review(payload))


class DirtySourceClassifierTest(FlowTestCase):
    """MEDIUM 10 -- --allow-dirty must still reject classifier mutation."""

    def test_classifier_mutation_is_rejected_even_when_the_source_is_dirty(self):
        (self.source / "scratch.txt").write_text("uncommitted", encoding="utf-8")
        self.write_plan({
            "classify": {"*": {"stdout": classify_block("normal"),
                               "touch": ["classifier-wrote-this.txt"]}},
            "implement": {"*": {"touch": ["implemented.txt"]}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("--allow-dirty", "add a feature")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("changed the source checkout", result.stderr)
        self.assertEqual(self.flow_branches(), [])
        self.assertEqual(self.worktree_dirs(), [])

    def test_preexisting_dirt_alone_does_not_trip_the_check(self):
        (self.source / "scratch.txt").write_text("uncommitted", encoding="utf-8")
        self.write_plan({
            "classify": {"*": {"stdout": classify_block("normal")}},
            "implement": {"*": {"touch": ["implemented.txt"]}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("--allow-dirty", "add a feature")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_classifier_creation_of_an_ignored_file_is_also_rejected(self):
        (self.source / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
        committed = self.git("add", ".gitignore")
        self.assertEqual(committed.returncode, 0, committed.stderr)
        committed = self.git("commit", "--quiet", "-m", "ignore probe files")
        self.assertEqual(committed.returncode, 0, committed.stderr)
        (self.source / "scratch.txt").write_text("uncommitted", encoding="utf-8")
        self.write_plan({
            "classify": {"*": {
                "stdout": classify_block("normal"),
                "touch": ["classifier.ignored"],
            }},
        })
        result = self.run_flow("--allow-dirty", "add a feature")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("changed the source checkout", result.stderr)


class SecretMarkerTest(FlowTestCase):
    """MEDIUM 1 -- exported run identity must not authenticate verdicts."""

    @staticmethod
    def _prompt_marker(call, base):
        prompt = call["argv"][-1]
        match = re.search(r"<<<({}_[0-9a-f]{{32}}_[a-zA-Z0-9_]+)".format(base), prompt)
        if match is None:
            raise AssertionError("no private marker found in stage prompt")
        return match.group(1)

    def test_writer_cannot_forge_review_from_exported_flow_run_id(self):
        self.write_plan({
            "implement": {"*": {
                "touch": ["implemented.txt"],
                "plant_old_review_marker": True,
            }},
            # The reviewer merely quotes the writer's planted old-style pass;
            # it emits no block carrying the private review marker.
            "review": {"*": {
                "quote_file": "forged-review.txt",
                "raw_stdout": True,
            }},
        })
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertEqual(result.returncode, 67, result.stderr)
        self.assertIn("no valid review verdict", result.stderr)
        implementation = self.stages("implement")[0]
        old_marker = "HERMES_FLOW_REVIEW_V1_{}_review".format(
            implementation["flow_active"]
        )
        worktree = self.worktrees / self.worktree_dirs()[0]
        planted = (worktree / "forged-review.txt").read_text(encoding="utf-8")
        self.assertIn(old_marker, planted)
        self.assertEqual(self.state_documents()[0]["reason_id"], "review_invalid")

    def test_classifier_and_reviewer_use_separate_unexported_secret_tags(self):
        self.write_plan({
            "classify": {"*": {"stdout": classify_block("normal")}},
            "implement": {"*": {"touch": ["implemented.txt"]}},
            "review": {"*": {"stdout": PASS_REVIEW}},
        })
        result = self.run_flow("please adjust the greeting text")
        self.assertEqual(result.returncode, 0, result.stderr)
        classify_call = self.stages("classify")[0]
        implement_call = self.stages("implement")[0]
        review_call = self.stages("review")[0]
        classify_marker = self._prompt_marker(classify_call, "HERMES_FLOW_CLASSIFY_V1")
        review_marker = self._prompt_marker(review_call, "HERMES_FLOW_REVIEW_V1")
        run_id = classify_call["flow_active"]
        self.assertNotEqual(
            classify_marker,
            "HERMES_FLOW_CLASSIFY_V1_{}_classify".format(run_id),
        )
        self.assertNotEqual(
            review_marker,
            "HERMES_FLOW_REVIEW_V1_{}_review".format(run_id),
        )
        self.assertNotEqual(classify_marker.split("_")[-2], review_marker.split("_")[-2])
        self.assertNotIn(classify_marker, " ".join(implement_call["argv"]))
        for call in (classify_call, implement_call, review_call):
            self.assertEqual(call["marker_environment"], [], call)


class GitControlSurfaceTest(FlowTestCase):
    """MEDIUM 2 -- classifier writes under the common Git dir are mutations."""

    def common_dir(self, source=None):
        source = source or self.source
        completed = self.git("rev-parse", "--git-common-dir", cwd=source)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        common = Path(completed.stdout.strip())
        if not common.is_absolute():
            common = source / common
        return common.resolve()

    def test_classifier_common_config_mutation_is_rejected_without_running_fsmonitor(self):
        common = self.common_dir()
        config = common / "config"
        sentinel = self.base / "fsmonitor-ran"
        monitor = self._executable(
            "hostile-fsmonitor",
            "#!/bin/sh\nprintf ran > {!r}\n".format(str(sentinel)),
        )
        hostile_config = config.read_text(encoding="utf-8") + (
            "\n[core]\n\tfsmonitor = {}\n".format(monitor)
        )
        self.write_plan({
            "classify": {"*": {
                "stdout": classify_block("normal"),
                "write_abs": [[str(config), hostile_config]],
            }},
        })
        result = self.run_flow("please adjust the greeting text")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("changed the source checkout", result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.flow_branches(), [])
        self.assertEqual(self.worktree_dirs(), [])

    def test_linked_worktree_classifier_cannot_plant_common_post_checkout_hook(self):
        linked = self.base / "linked-source"
        created = self.git("worktree", "add", "-b", "linked-source", str(linked))
        self.assertEqual(created.returncode, 0, created.stderr)
        self.source = linked
        common = self.common_dir(linked)
        config = common / "config"
        hook = common / "hooks" / "post-checkout"
        sentinel = self.base / "post-checkout-ran"
        hostile_config = config.read_text(encoding="utf-8") + (
            "\n[core]\n\thooksPath = {}\n".format(common / "hooks")
        )
        hook_source = "#!/bin/sh\nprintf ran > {!r}\n".format(str(sentinel))
        self.write_plan({
            "classify": {"*": {
                "stdout": classify_block("normal"),
                "write_abs": [
                    [str(config), hostile_config],
                    [str(hook), hook_source],
                ],
                "chmod_abs": [[str(hook), 0o755]],
            }},
        })
        result = self.run_flow("please adjust the greeting text")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("changed the source checkout", result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.worktree_dirs(), [])

    def test_worktree_add_disables_preexisting_hooks_path(self):
        hooks = self.base / "hostile-hooks"
        hooks.mkdir()
        sentinel = self.base / "preexisting-post-checkout-ran"
        hook = hooks / "post-checkout"
        hook.write_text(
            "#!/bin/sh\nprintf ran > {!r}\n".format(str(sentinel)),
            encoding="utf-8",
        )
        hook.chmod(0o755)
        configured = self.git("config", "core.hooksPath", str(hooks))
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.passing_plan()
        result = self.run_flow("--lane", "normal", "add a feature")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists(), "git worktree add executed post-checkout")

    def test_control_fingerprint_covers_name_type_mode_content_and_deletion(self):
        module = load_flow_module()
        common = self.common_dir()

        def snapshot():
            return module.source_status_snapshot(self.source, common)

        config = common / "config"
        original_config = config.read_bytes()
        baseline = snapshot()
        config.write_bytes(original_config + b"\n# classifier mutation\n")
        self.assertNotEqual(snapshot(), baseline)
        config.write_bytes(original_config)

        exclude = common / "info" / "exclude"
        original_exclude = exclude.read_bytes()
        before_exclude = snapshot()
        exclude.write_bytes(original_exclude + b"\nprobe.tmp\n")
        self.assertNotEqual(snapshot(), before_exclude)
        exclude.write_bytes(original_exclude)

        nested = common / "hooks" / "nested"
        nested.mkdir(exist_ok=True)
        hook = nested / "post-checkout"
        absent = snapshot()
        hook.write_bytes(b"one\n")
        hook.chmod(0o600)
        created = snapshot()
        self.assertNotEqual(created, absent)
        hook.write_bytes(b"two\n")
        content_changed = snapshot()
        self.assertNotEqual(content_changed, created)
        hook.chmod(0o700)
        mode_changed = snapshot()
        self.assertNotEqual(mode_changed, content_changed)
        hook.unlink()
        self.assertEqual(snapshot(), absent)
        hook.symlink_to("/dev/zero")
        symlink = snapshot()
        self.assertNotEqual(symlink, absent)
        hook.unlink()
        hook.write_bytes(b"two\n")
        self.assertNotEqual(snapshot(), symlink)


class GitVisibleSnapshotTest(FlowTestCase):
    """Git-visible hashing is framed, bounded, and race safe."""

    def test_adjacent_regular_file_record_collision_has_distinct_digests(self):
        module = load_flow_module()
        runner_module = load_runner_module()
        first_path = self.source / "a"
        second_path = self.source / "b"
        first_path.write_bytes(b"")
        second_path.write_bytes(b"")
        mode = str(stat.S_IMODE(second_path.lstat().st_mode)).encode("ascii")
        # Under the old raw concatenation, these two states were identical:
        # A: a=b"", b=(b's record prefix)+payload
        # B: a=(b's record prefix), b=payload
        adjacent_record = b"b\0" + mode + b"\0"
        payload = b"payload"

        first_path.write_bytes(b"")
        second_path.write_bytes(adjacent_record + payload)
        _head, first_digest = module.git_visible_snapshot(
            self.source, time.monotonic() + 30
        )

        first_path.write_bytes(adjacent_record)
        second_path.write_bytes(payload)
        _head, second_digest = module.git_visible_snapshot(
            self.source, time.monotonic() + 30
        )
        self.assertNotEqual(first_digest, second_digest)

        # The gate snapshot had the same boundary ambiguity with its own
        # explicit path label; exercise that concrete prefix independently.
        gate_record = b"path\0b\0" + mode + b"\0"
        first_path.write_bytes(b"")
        second_path.write_bytes(gate_record + payload)
        first_gate_digest = runner_module.snapshot_worktree(
            self.source, time.monotonic() + 30
        )
        first_path.write_bytes(gate_record)
        second_path.write_bytes(payload)
        second_gate_digest = runner_module.snapshot_worktree(
            self.source, time.monotonic() + 30
        )
        self.assertNotEqual(first_gate_digest, second_gate_digest)

    def test_lstat_oserrors_keep_the_baseline_privacy_safe_errno_frame(self):
        candidate = self.base / "private-snapshot-entry"
        raw_path = b"private-snapshot-entry"
        for label, module, arguments in (
            (
                "flow",
                load_flow_module(),
                lambda digest, loaded: loaded._digest_git_visible_entry(
                    digest,
                    raw_path,
                    candidate,
                    time.monotonic() + 30,
                    "snapshot timeout",
                ),
            ),
            (
                "runner",
                load_runner_module(),
                lambda digest, loaded: loaded._digest_git_visible_entry(
                    digest, raw_path, candidate, time.monotonic() + 30
                ),
            ),
        ):
            for error_number in (errno.EACCES, errno.ELOOP):
                with self.subTest(binary=label, errno=error_number):
                    digest = hashlib.sha256()
                    with mock.patch.object(
                        Path,
                        "lstat",
                        side_effect=OSError(error_number, "private path detail"),
                    ):
                        arguments(digest, module)

                    expected = hashlib.sha256()
                    module._digest_frame(expected, b"path", raw_path)
                    module._digest_frame(expected, b"file_type", b"missing")
                    module._digest_frame(
                        expected,
                        b"metadata_errno",
                        str(error_number).encode("ascii"),
                    )
                    self.assertEqual(digest.hexdigest(), expected.hexdigest())

    def test_regular_file_to_fifo_swap_is_nonblocking_and_rejected(self):
        for label, module in (
            ("flow", load_flow_module()),
            ("runner", load_runner_module()),
        ):
            with self.subTest(binary=label):
                candidate = self.base / ("swap-target-" + label)
                candidate.write_bytes(b"regular")
                expected = candidate.lstat()
                real_open = os.open
                observed_flags = []

                def swap_then_open(path, flags, *args, **kwargs):
                    observed_flags.append(flags)
                    candidate.unlink()
                    os.mkfifo(str(candidate))
                    return real_open(path, flags, *args, **kwargs)

                started = time.monotonic()
                with mock.patch.object(module.os, "open", side_effect=swap_then_open):
                    with self.assertRaises(OSError):
                        module._open_snapshot_regular_file(candidate, expected)
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertTrue(observed_flags[0] & getattr(os, "O_NONBLOCK", 0))
                if hasattr(os, "O_NOFOLLOW"):
                    self.assertTrue(observed_flags[0] & os.O_NOFOLLOW)

    def test_regular_file_to_symlink_swap_is_rejected_before_content_read(self):
        outside = self.base / "outside-secret"
        outside.write_bytes(b"must-not-be-hashed")
        for label, module in (
            ("flow", load_flow_module()),
            ("runner", load_runner_module()),
        ):
            with self.subTest(binary=label):
                candidate = self.base / ("symlink-swap-target-" + label)
                candidate.write_bytes(b"regular")
                expected = candidate.lstat()
                real_open = os.open

                def swap_then_open(path, flags, *args, **kwargs):
                    candidate.unlink()
                    candidate.symlink_to(outside)
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(module.os, "open", side_effect=swap_then_open):
                    with self.assertRaises(OSError):
                        module._open_snapshot_regular_file(candidate, expected)

    def test_git_bytes_stops_an_unbounded_producer_at_the_read_cap(self):
        module = load_flow_module()
        fake_bin = self.base / "fake-git-bin"
        fake_bin.mkdir()
        pid_file = self.base / "fake-git.pid"
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!{}\n".format(sys.executable)
            + "import ctypes, json, os, sys\n"
            + PROCESS_RECORD_SOURCE
            + "\nwrite_process_record({!r}, os.getpid())\n".format(str(pid_file))
            + "while True:\n"
            + "    os.write(1, b'x' * 65536)\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        path = str(fake_bin) + os.pathsep + os.environ.get("PATH", "")
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"PATH": path}):
            with self.assertRaises(module.FlowError) as raised:
                module.git_bytes(
                    ["status"], self.source, 1024, time.monotonic() + 20
                )
        self.assertEqual(raised.exception.reason_id, "git_output_too_large")
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(pid_file.exists())
        record = read_process_record(pid_file)
        self.assertIsNotNone(record)
        self.addCleanup(reap_record, record, True)
        self.assertTrue(wait_for_recorded_exit(record, 5))


class StageSnapshotTelemetryTest(FlowTestCase):
    """Snapshot failures retain bounded start/end/state stage telemetry."""

    def unit_flow(self, module):
        args = types.SimpleNamespace(
            wall_timeout=60.0,
            stage_timeout=30.0,
            max_model_stages=6,
            journal_max_bytes=1024 * 1024,
            journal_backups=0,
        )
        flow = module.Flow(args)
        flow.runner = Path(sys.executable)
        flow.state_dir = self.base / "unit-stage-state"
        flow.worktree = self.source
        flow.gate_plan = module.GatePlan("disabled", "none", 0, None, None)
        flow.state = module.FlowState(None)

        class CaptureJournal:
            def __init__(self):
                self.records = []

            def write(self, record):
                self.records.append(dict(record))

        flow.journal = CaptureJournal()
        return flow

    def assert_paired_stage(self, flow, stage_name, exit_code, reason_id):
        starts = [
            record for record in flow.journal.records
            if record["event"] == "stage_start" and record["stage"] == stage_name
        ]
        ends = [
            record for record in flow.journal.records
            if record["event"] == "stage_end" and record["stage"] == stage_name
        ]
        state_entries = [
            entry for entry in flow.state.document["stages"]
            if entry.get("stage") == stage_name and "stage_idx" in entry
        ]
        self.assertEqual(len(starts), 1, starts)
        self.assertEqual(len(ends), 1, ends)
        self.assertEqual(len(state_entries), 1, state_entries)
        self.assertEqual(starts[0]["stage_idx"], ends[0]["stage_idx"])
        self.assertEqual(ends[0]["stage_idx"], state_entries[0]["stage_idx"])
        self.assertEqual(ends[0]["exit_code"], exit_code)
        self.assertEqual(ends[0]["reason_id"], reason_id)
        self.assertEqual(state_entries[0]["exit_code"], exit_code)
        self.assertEqual(state_entries[0]["reason_id"], reason_id)

    def test_pre_snapshot_failure_records_stage_without_execution(self):
        module = load_flow_module()
        flow = self.unit_flow(module)
        snapshot_failure = module.FlowError(
            module.EXIT_HARNESS, "stage_snapshot_failed", "private path detail"
        )
        with mock.patch.object(
            module, "git_visible_snapshot", side_effect=snapshot_failure
        ), mock.patch.object(flow, "_execute") as execute:
            with self.assertRaises(module.FlowError) as raised:
                flow.run_stage(
                    "implement",
                    ["--workdir", str(self.source), "--task", "implement"],
                    "top secret prompt",
                    False,
                    "normal",
                )
        self.assertEqual(raised.exception.reason_id, "stage_snapshot_failed")
        execute.assert_not_called()
        events = [record["event"] for record in flow.journal.records]
        self.assertEqual(events, ["stage_start", "stage_end"])
        stage = flow.state.document["stages"][0]
        self.assertEqual(stage["status"], "harness_error")
        self.assertEqual(stage["reason_id"], "stage_snapshot_failed")
        self.assertEqual(stage["snapshot_status"], "failed")
        self.assertEqual(stage["snapshot_reason_id"], "pre_stage_snapshot_failed")
        self.assert_paired_stage(
            flow, "implement", module.EXIT_HARNESS, "stage_snapshot_failed"
        )
        self.assertEqual(list(flow.state_dir.rglob("*.prompt")), [])
        retained = json.dumps({
            "journal": flow.journal.records,
            "state": flow.state.document,
        })
        self.assertNotIn("top secret prompt", retained)
        self.assertNotIn("private path detail", retained)

    def test_post_snapshot_failure_preserves_completed_stage_outcome(self):
        module = load_flow_module()
        flow = self.unit_flow(module)
        completed = module.StageOutcome(
            "implement", 0, 0.0, "claude", "private model output", "success", "success"
        )
        snapshot_failure = module.FlowError(
            module.EXIT_HARNESS, "stage_snapshot_failed", "private post path"
        )
        before = ("a" * 40, "b" * 64)
        with mock.patch.object(
            module, "git_visible_snapshot", side_effect=(before, snapshot_failure)
        ), mock.patch.object(flow, "_execute", return_value=completed) as execute:
            with self.assertRaises(module.FlowError) as raised:
                flow.run_stage(
                    "implement",
                    ["--workdir", str(self.source), "--task", "implement"],
                    "another top secret prompt",
                    False,
                    "normal",
                )
        self.assertEqual(raised.exception.reason_id, "stage_snapshot_failed")
        execute.assert_called_once()
        events = [record["event"] for record in flow.journal.records]
        self.assertEqual(events, ["stage_start", "stage_end"])
        stage = flow.state.document["stages"][0]
        self.assertEqual(stage["exit_code"], 0)
        self.assertEqual(stage["vendor"], "claude")
        self.assertEqual(stage["status"], "success")
        self.assertEqual(stage["reason_id"], "success")
        self.assertEqual(stage["snapshot_status"], "failed")
        self.assertEqual(stage["snapshot_reason_id"], "post_stage_snapshot_failed")
        self.assertEqual(stage["git_head_before"], "a" * 40)
        self.assertIsNone(stage["git_head_after"])
        self.assert_paired_stage(flow, "implement", 0, "success")
        self.assertEqual(list(flow.state_dir.rglob("*.prompt")), [])
        retained = json.dumps({
            "journal": flow.journal.records,
            "state": flow.state.document,
        })
        self.assertNotIn("another top secret prompt", retained)
        self.assertNotIn("private model output", retained)
        self.assertNotIn("private post path", retained)

    def test_runner_launch_flow_error_is_paired_and_beats_cleanup_failure(self):
        module = load_flow_module()
        flow = self.unit_flow(module)
        before = ("a" * 40, "b" * 64)
        launch_error = module.FlowError(
            module.EXIT_HARNESS, "runner_launch_failed", "private executable path"
        )
        real_unlink = Path.unlink

        def fail_prompt_cleanup(path, *args, **kwargs):
            if path.suffix == ".prompt":
                raise OSError(errno.EACCES, "private prompt path")
            return real_unlink(path, *args, **kwargs)

        try:
            with mock.patch.object(
                module, "git_visible_snapshot", return_value=before
            ), mock.patch.object(
                flow, "_execute", side_effect=launch_error
            ), mock.patch.object(Path, "unlink", fail_prompt_cleanup):
                with self.assertRaises(module.FlowError) as raised:
                    flow.run_stage(
                        "implement",
                        ["--workdir", str(self.source), "--task", "implement"],
                        "private task prompt",
                        False,
                        "normal",
                    )
            self.assertIs(raised.exception, launch_error)
            self.assert_paired_stage(
                flow, "implement", module.EXIT_HARNESS, "runner_launch_failed"
            )
        finally:
            for prompt_path in list(flow.active_prompts):
                try:
                    real_unlink(prompt_path)
                except FileNotFoundError:
                    pass
                flow.active_prompts.discard(prompt_path)

    def test_prompt_cleanup_failure_after_success_is_paired(self):
        module = load_flow_module()
        flow = self.unit_flow(module)
        before = ("a" * 40, "b" * 64)
        completed = module.StageOutcome(
            "implement", 0, 0.0, "claude", "private output", "success", "success"
        )
        real_unlink = Path.unlink

        def fail_prompt_cleanup(path, *args, **kwargs):
            if path.suffix == ".prompt":
                raise OSError(errno.EACCES, "private prompt path")
            return real_unlink(path, *args, **kwargs)

        try:
            with mock.patch.object(
                module, "git_visible_snapshot", return_value=before
            ), mock.patch.object(
                flow, "_execute", return_value=completed
            ), mock.patch.object(Path, "unlink", fail_prompt_cleanup):
                with self.assertRaises(module.FlowError) as raised:
                    flow.run_stage(
                        "implement",
                        ["--workdir", str(self.source), "--task", "implement"],
                        "private task prompt",
                        False,
                        "normal",
                    )
            self.assertEqual(raised.exception.reason_id, "prompt_cleanup_failed")
            self.assert_paired_stage(
                flow, "implement", module.EXIT_HARNESS, "prompt_cleanup_failed"
            )
        finally:
            for prompt_path in list(flow.active_prompts):
                try:
                    real_unlink(prompt_path)
                except FileNotFoundError:
                    pass
                flow.active_prompts.discard(prompt_path)

    def test_post_execution_frozen_gate_failure_is_paired(self):
        module = load_flow_module()
        flow = self.unit_flow(module)
        before = ("a" * 40, "b" * 64)
        completed = module.StageOutcome(
            "implement", 0, 0.0, "claude", "private output", "success", "success"
        )
        frozen_error = module.FlowError(
            module.EXIT_HARNESS, "frozen_gate_integrity", "private gate path"
        )
        with mock.patch.object(
            module, "git_visible_snapshot", return_value=before
        ), mock.patch.object(
            module, "verify_frozen_gate_policy", side_effect=(None, frozen_error)
        ), mock.patch.object(flow, "_execute", return_value=completed):
            with self.assertRaises(module.FlowError) as raised:
                flow.run_stage(
                    "implement",
                    ["--workdir", str(self.source), "--task", "implement"],
                    "private task prompt",
                    False,
                    "normal",
                )
        self.assertIs(raised.exception, frozen_error)
        self.assert_paired_stage(
            flow, "implement", module.EXIT_HARNESS, "frozen_gate_integrity"
        )

    def test_signal_path_is_paired_before_the_original_signal_is_reraised(self):
        module = load_flow_module()
        flow = self.unit_flow(module)
        before = ("a" * 40, "b" * 64)
        termination = module.FlowTermination(signal.SIGTERM)
        with mock.patch.object(
            module, "git_visible_snapshot", return_value=before
        ), mock.patch.object(flow, "_execute", side_effect=termination):
            with self.assertRaises(module.FlowTermination) as raised:
                flow.run_stage(
                    "implement",
                    ["--workdir", str(self.source), "--task", "implement"],
                    "private task prompt",
                    False,
                    "normal",
                )
        self.assertIs(raised.exception, termination)
        self.assert_paired_stage(
            flow, "implement", module.EXIT_ABORT, "termination_signal"
        )

    def test_timeout_and_abort_outcomes_each_have_one_paired_end(self):
        module = load_flow_module()
        before = ("a" * 40, "b" * 64)
        cases = (
            ("timeout", module.EXIT_TIMEOUT, "timeout", "stage_timeout"),
            ("abort", module.EXIT_ABORT, "abort", "user_abort"),
        )
        for stage_name, exit_code, status, reason_id in cases:
            with self.subTest(status=status):
                flow = self.unit_flow(module)
                completed = module.StageOutcome(
                    stage_name, exit_code, 0.0, None, "", status, reason_id
                )
                with mock.patch.object(
                    module, "git_visible_snapshot", return_value=before
                ), mock.patch.object(flow, "_execute", return_value=completed):
                    returned = flow.run_stage(
                        stage_name,
                        ["--workdir", str(self.source), "--task", "implement"],
                        "private task prompt",
                        False,
                        "normal",
                    )
                self.assertIs(returned, completed)
                self.assert_paired_stage(flow, stage_name, exit_code, reason_id)


class AdjacentLowHardeningTest(unittest.TestCase):
    """Low-risk parity fixes accepted while touching the same control paths."""

    def test_flow_ack_budget_covers_the_runner_bounded_teardown(self):
        module = load_flow_module()
        # Runner: up to four seconds of process-group teardown plus two
        # two-second output-pump joins.
        self.assertGreaterEqual(module.RUNNER_ACK_GRACE_SECONDS, 8.0)

    def test_flow_doctor_budget_covers_both_vendor_cleanups(self):
        module = load_flow_module()
        self.assertGreaterEqual(
            module.DOCTOR_GRACE_SECONDS,
            2.0 * module.DOCTOR_VENDOR_CLEANUP_SECONDS,
        )

    def test_remaining_git_helpers_use_devnull_stdin(self):
        flow_module = load_flow_module()
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(
            flow_module.subprocess, "run", return_value=completed
        ) as run:
            flow_module.git(["status"], Path.cwd())
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

        runner_module = load_runner_module()
        with mock.patch.object(
            runner_module.subprocess, "run", return_value=completed
        ) as run:
            runner_module._git_snapshot_command(
                ["status"], Path.cwd(), time.monotonic() + 30
            )
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_doctor_reader_oserror_fails_closed_without_thread_traceback(self):
        module = load_flow_module()
        args = types.SimpleNamespace(
            wall_timeout=60.0,
            doctor_timeout=1.0,
            journal_max_bytes=1024 * 1024,
            journal_backups=0,
        )
        flow = module.Flow(args)
        flow.runner = Path(sys.executable)
        flow.source_root = Path.cwd()
        flow.state = module.FlowState(None)

        class FakeStdout:
            @staticmethod
            def fileno():
                return 123

            @staticmethod
            def close():
                return None

        class FakeProcess:
            pid = 12345
            stdout = FakeStdout()
            returncode = 0

            @staticmethod
            def poll():
                return 0

        payload = doctor_json().encode("utf-8")
        with mock.patch.object(
            module.subprocess, "Popen", return_value=FakeProcess()
        ), mock.patch.object(
            module, "process_identity", return_value="test:doctor"
        ), mock.patch.object(
            module.os, "read", side_effect=(payload, OSError(errno.EIO, "private pipe detail"))
        ), mock.patch.object(
            module, "_process_group_exists", return_value=False
        ), mock.patch.object(module.threading, "excepthook") as excepthook:
            with self.assertRaises(module.FlowError) as raised:
                flow.doctor_preflight()
        self.assertEqual(raised.exception.reason_id, "preflight_doctor_malformed")
        self.assertEqual(
            flow.state.document["preflight_reason_id"],
            "preflight_doctor_malformed",
        )
        excepthook.assert_not_called()

    def test_gate_process_groups_are_registered_with_the_shutdown_handoff(self):
        module = load_runner_module()
        registrations = []

        class Shutdown:
            @staticmethod
            def requested():
                return False

        def fake_run_process(
            _cmd, _workdir, _timeout, _shutdown=None, report_model_group=False,
            environment=None,
        ):
            registrations.append(report_model_group)
            return module.RunResult(0, b"", b"", 0.0)

        gate = module.Gate("probe", (sys.executable, "-c", "pass"), 1.0)
        with mock.patch.object(module, "run_process", side_effect=fake_run_process), \
                mock.patch.object(module, "snapshot_worktree", return_value="stable"), \
                mock.patch.object(module, "verify_gate_integrity", return_value=None):
            failure, harness_failure = module.run_gates(
                (gate,), Path.cwd(), time.monotonic() + 30.0, (), Shutdown()
            )
        self.assertIsNone(failure)
        self.assertFalse(harness_failure)
        self.assertEqual(registrations, [True])

    def test_flow_journal_rotation_rejects_a_foreign_owned_backup_source(self):
        module = load_flow_module()
        with tempfile.TemporaryDirectory() as temporary:
            journal_path = Path(temporary) / "flow.jsonl"
            backup = Path(str(journal_path) + ".1")
            journal_path.write_bytes(b"x")
            backup.write_bytes(b"old")
            journal = module.FlowJournal(journal_path, max_bytes=1, backups=2)
            real_lstat = Path.lstat

            def foreign_backup_lstat(candidate):
                info = real_lstat(candidate)
                if candidate == backup:
                    class ForeignInfo:
                        st_mode = info.st_mode
                        st_uid = info.st_uid + 1
                        st_size = info.st_size
                    return ForeignInfo()
                return info

            with mock.patch.object(Path, "lstat", foreign_backup_lstat):
                with self.assertRaises(OSError):
                    journal._rotate(1)


class FlowLockTest(FlowTestCase):
    """MEDIUM 11 -- a durable lock must survive environment stripping."""

    def test_nested_flow_cannot_bypass_recursion_by_clearing_the_environment(self):
        stripper = self.bin / "strip-and-nest"
        stripper.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, subprocess, sys\n"
            "from pathlib import Path\n"
            "env = dict(os.environ)\n"
            "for key in ('HERMES_FLOW_ACTIVE', 'HERMES_CODER_ACTIVE'):\n"
            "    env.pop(key, None)\n"
            "done = subprocess.run(\n"
            "    [sys.executable, os.environ['FLOW_BINARY'], '--source',\n"
            "     os.environ['NESTED_SOURCE'], '--lane', 'fast', 'nested task'],\n"
            "    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)\n"
            "Path(os.environ['RECURSION_RESULT']).write_text(\n"
            "    json.dumps({'exit': done.returncode, 'stderr': done.stderr}))\n"
            "print('done')\n",
            encoding="utf-8",
        )
        stripper.chmod(0o755)
        env = dict(self.env)
        env["HERMES_CODER_CLAUDE"] = str(stripper)
        env["HERMES_CODER_CODEX"] = str(stripper)
        env["NESTED_SOURCE"] = str(self.source)
        self.write_plan({})
        result = self.run_flow("--lane", "normal", "--no-gates", "add a feature", env=env)
        self.assertTrue(self.recursion_result.exists(), result.stderr)
        nested = json.loads(self.recursion_result.read_text(encoding="utf-8"))
        self.assertEqual(nested["exit"], 69, nested)
        self.assertIn("lock", nested["stderr"].lower())


class SignalCleanupTest(FlowTestCase):
    """MEDIUM 12 -- flow signals clean nested model groups and plaintext prompts."""

    def _assert_flow_signal_cleanup(self, signum, label, repeat=False):
        leader_file = self.base / "flow-{}-leader.pid".format(label)
        descendant_file = self.base / "flow-{}-descendant.pid".format(label)
        self.addCleanup(reap_recorded_model_group, leader_file, descendant_file)
        self.write_plan({"implement": {"*": {"survivor": True}}})
        env = dict(self.env)
        env["MODEL_PID_FILE"] = str(leader_file)
        env["SURVIVOR_PID_FILE"] = str(descendant_file)
        command = [sys.executable, str(FLOW), "--source", str(self.source),
                   "--lane", "normal", "--wall-timeout", "120",
                   "a secret task prompt {}".format(label)]
        proc = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(reap_process, proc)
        deadline = time.monotonic() + 30
        stage_dir = self.state_dir / "stages"
        while time.monotonic() < deadline:
            if (
                list(stage_dir.glob("*/*.prompt"))
                and leader_file.exists()
                and descendant_file.exists()
            ):
                break
            time.sleep(0.05)
        self.assertTrue(list(stage_dir.glob("*/*.prompt")), "stage prompt never appeared")
        self.assertTrue(leader_file.exists(), "model leader never appeared")
        self.assertTrue(descendant_file.exists(), "stubborn descendant never appeared")
        leader = read_process_record(leader_file)
        descendant = read_process_record(descendant_file)
        self.assertIsNotNone(leader)
        self.assertIsNotNone(descendant)
        self.assertEqual(os.getpgid(leader[0]), leader[0], "model leader must own its isolated group")
        self.assertEqual(os.getpgid(descendant[0]), leader[0], "descendant must share the model group")
        proc.send_signal(signum)
        if repeat:
            time.sleep(0.1)
            self.assertIsNone(proc.poll(), "flow exited before repeated signal exercised teardown")
            proc.send_signal(signum)
        _stdout, stderr = proc.communicate(timeout=45)
        self.assertEqual(proc.returncode, 130, stderr)
        if repeat:
            self.assertIn("terminated by signal {}".format(signum), stderr)
            self.assertNotIn("Traceback", stderr)
        self.assertEqual(list(stage_dir.glob("*/*.prompt")), [])
        self.assertTrue(
            wait_for_recorded_exit(leader, 15),
            "model leader {} survived {} flow cleanup".format(leader[0], label),
        )
        self.assertTrue(
            wait_for_recorded_exit(descendant, 15),
            "stubborn descendant {} survived {} flow cleanup".format(descendant[0], label),
        )

    def test_sigterm_during_a_stage_removes_prompt_and_model_group(self):
        self._assert_flow_signal_cleanup(signal.SIGTERM, "sigterm")

    def test_sighup_during_a_stage_removes_prompt_and_model_group(self):
        self._assert_flow_signal_cleanup(signal.SIGHUP, "sighup")

    def test_repeated_sigint_cannot_interrupt_teardown(self):
        self._assert_flow_signal_cleanup(signal.SIGINT, "double-sigint", repeat=True)


class ProcessIdentityTest(unittest.TestCase):
    """A recycled PID/PGID must never be signalled as a registered group."""

    def setUp(self):
        self.module = load_flow_module()

    def test_identity_is_stable_per_process_and_absent_once_it_exits(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(reap_process, proc)
        identity = self.module.process_identity(proc.pid)
        self.assertIsNotNone(identity, "no stable process identity on this host")
        self.assertEqual(identity, self.module.process_identity(proc.pid))
        self.assertNotEqual(identity, self.module.process_identity(os.getpid()))
        proc.kill()
        proc.wait(timeout=10)
        self.assertIsNone(self.module.process_identity(proc.pid))

    def test_exact_group_stop_refuses_an_unrecognised_owner(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        self.addCleanup(reap_process, proc)
        self.assertFalse(
            self.module._stop_exact_process_group(proc.pid, "stale-identity")
        )
        time.sleep(0.2)
        self.assertIsNone(proc.poll())

    def test_exact_group_stop_kills_a_group_whose_owner_matches(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        self.addCleanup(reap_process, proc)
        identity = self.module.process_identity(proc.pid)
        reaper = threading.Thread(target=proc.wait)
        reaper.start()
        self.assertTrue(self.module._stop_exact_process_group(proc.pid, identity))
        reaper.join(timeout=10)
        self.assertFalse(reaper.is_alive())
        self.assertEqual(proc.returncode, -signal.SIGTERM)

    def test_exact_group_stop_refuses_escalation_after_owner_disappears(self):
        with mock.patch.object(
            self.module, "_exact_process_group_exists", return_value=True
        ), mock.patch.object(
            self.module,
            "process_identity",
            side_effect=["registered-owner", None],
        ), mock.patch.object(
            self.module, "_wait_for_exact_process_group", return_value=False
        ), mock.patch.object(self.module.os, "killpg") as kill_group:
            stopped = self.module._stop_exact_process_group(
                4242, "registered-owner"
            )
        self.assertFalse(stopped)
        kill_group.assert_called_once_with(4242, signal.SIGTERM)

    def test_runner_registration_binds_group_to_kernel_identity(self):
        runner = load_runner_module()
        parent, child = socket.socketpair()
        self.addCleanup(parent.close)
        handoff = runner.ShutdownHandoff(child.detach())
        self.addCleanup(handoff.close)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        self.addCleanup(reap_process, proc)
        identity = runner.process_identity(proc.pid)
        self.assertIsNotNone(identity)
        self.assertTrue(handoff.model_started(proc.pid, identity))
        fields = parent.recv(4096).strip().split(b" ")
        self.assertEqual(fields[:3], [b"H1", b"P", str(proc.pid).encode("ascii")])
        self.assertEqual(binascii.unhexlify(fields[3]).decode("utf-8"), identity)

    def test_direct_runner_group_stop_refuses_a_stale_owner_identity(self):
        runner = load_runner_module()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        self.addCleanup(reap_process, proc)
        with mock.patch.object(runner, "_process_group_exists", return_value=True), \
                mock.patch.object(runner.os, "killpg") as kill_group:
            stopped = runner._stop_process_group(
                proc, signal.SIGTERM, "stale-owner-identity"
            )
        self.assertFalse(stopped)
        kill_group.assert_not_called()


class DirectRunnerIdentityPreflightTest(unittest.TestCase):
    """POSIX group control fails closed before or immediately after launch."""

    def setUp(self):
        self.module = load_runner_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workdir = Path(self.temp.name)

    def test_identity_unavailable_refuses_before_launching_any_child(self):
        marker = self.workdir / "marker"
        cmd = [
            sys.executable, "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ran')",
            str(marker),
        ]
        with mock.patch.object(self.module, "process_identity", return_value=None), \
                mock.patch.object(self.module.subprocess, "Popen") as popen:
            result = self.module.run_process(
                cmd, self.workdir, 5.0, shutdown=None, report_model_group=True,
            )
        popen.assert_not_called()
        self.assertFalse(marker.exists())
        self.assertTrue(result.launch_failed)
        self.assertEqual(result.exit_code, self.module.EXIT_HARNESS)
        self.assertEqual(result.reason_id, "process_identity_unavailable")

    def test_identity_capture_failure_uses_only_the_direct_popen_child(self):
        pid_file = self.workdir / "child.pid"
        script = self.workdir / "child.py"
        script.write_text(
            "import os, time\n"
            "open({!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(30)\n".format(str(pid_file)),
            encoding="utf-8",
        )
        real_pid = os.getpid()
        real_popen = self.module.subprocess.Popen

        def fake_identity(pid):
            return "self-identity-token" if pid == real_pid else None

        def started_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            deadline = time.monotonic() + 5.0
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            return proc

        class RecordingShutdown:
            def __init__(self):
                self.started = []
                self.failed = False

            def requested(self):
                return False

            def model_started(self, process_group, identity):
                self.started.append(identity)
                return True

            def registration_failed(self):
                self.failed = True

        shutdown = RecordingShutdown()
        with mock.patch.object(self.module, "process_identity", side_effect=fake_identity), \
                mock.patch.object(self.module.subprocess, "Popen", side_effect=started_popen), \
                mock.patch.object(self.module.os, "killpg") as kill_group:
            result = self.module.run_process(
                [sys.executable, str(script)], self.workdir, 5.0,
                shutdown=shutdown, report_model_group=True,
            )
        kill_group.assert_not_called()
        self.assertEqual(shutdown.started, [])
        self.assertTrue(shutdown.failed)
        self.assertTrue(result.launch_failed)
        self.assertEqual(result.reason_id, "process_identity_unavailable")
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and current_process_identity(child_pid) is not None:
            time.sleep(0.05)
        self.assertIsNone(current_process_identity(child_pid))

    def test_windows_fallback_keeps_live_popen_identity(self):
        class RecordingShutdown:
            def __init__(self):
                self.started = []

            def requested(self):
                return False

            def model_started(self, process_group, identity):
                self.started.append(identity)
                return True

            def model_stopped(self, process_group, identity):
                pass

            def registration_failed(self):
                raise AssertionError("Windows registration should not fail")

        shutdown = RecordingShutdown()
        with mock.patch.object(self.module.os, "name", "nt"):
            result = self.module.run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                self.workdir, 0.1, shutdown=shutdown, report_model_group=True,
            )
        self.assertEqual(len(shutdown.started), 1)
        self.assertTrue(shutdown.started[0].startswith("direct:"))
        self.assertFalse(result.launch_failed)

    def test_flow_refuses_runner_launch_when_identity_capability_is_missing(self):
        flow_module = load_flow_module()
        args = types.SimpleNamespace(
            wall_timeout=60.0,
            journal_max_bytes=1024 * 1024,
            journal_backups=0,
        )
        flow = flow_module.Flow(args)
        with mock.patch.object(flow_module, "process_identity", return_value=None), \
                mock.patch.object(flow_module.subprocess, "Popen") as popen:
            with self.assertRaises(flow_module.FlowError) as raised:
                flow._execute(
                    "implement",
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    {},
                    False,
                    5.0,
                    self.workdir,
                )
        popen.assert_not_called()
        self.assertEqual(raised.exception.reason_id, "process_identity_unavailable")

    def test_flow_child_identity_capture_failure_uses_direct_popen_only(self):
        flow_module = load_flow_module()
        args = types.SimpleNamespace(
            wall_timeout=60.0,
            journal_max_bytes=1024 * 1024,
            journal_backups=0,
        )
        flow = flow_module.Flow(args)
        real_pid = os.getpid()
        real_popen = flow_module.subprocess.Popen
        children = []

        def fake_identity(pid):
            return "flow-self-token" if pid == real_pid else None

        def record_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            children.append(proc)
            return proc

        with mock.patch.object(flow_module, "process_identity", side_effect=fake_identity), \
                mock.patch.object(flow_module.subprocess, "Popen", side_effect=record_popen), \
                mock.patch.object(flow_module.os, "killpg") as kill_group:
            with self.assertRaises(flow_module.FlowError) as raised:
                flow._execute(
                    "implement",
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    {},
                    False,
                    5.0,
                    self.workdir,
                )
        self.assertEqual(raised.exception.reason_id, "process_identity_unavailable")
        kill_group.assert_not_called()
        self.assertEqual(len(children), 1)
        self.addCleanup(reap_process, children[0])
        self.assertIsNotNone(children[0].returncode)


class HandoffDrainTest(unittest.TestCase):
    """Lifecycle writes are continuously drained and bounded."""

    def setUp(self):
        self.module = load_flow_module()

    def _socketpair(self):
        parent, child = socket.socketpair()
        for sock in (parent, child):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
            except OSError:
                pass
        self.addCleanup(child.close)
        return parent, child

    def test_many_lifecycle_frames_do_not_block_before_shutdown(self):
        parent, child = self._socketpair()
        handoff = self.module.RunnerHandoff(parent)
        self.addCleanup(handoff.close)
        errors = []

        def writer():
            try:
                for index in range(2000):
                    group = 100000 + index
                    identity = "id-{}".format(group)
                    child.sendall(self.module.encode_group_frame(b"P", group, identity))
                    child.sendall(self.module.encode_group_frame(b"C", group, identity))
            except OSError as exc:
                errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "lifecycle sender blocked on a full socket")
        self.assertEqual(errors, [])
        child.sendall(b"H1 A\n")
        self.assertTrue(handoff.request_and_wait(10.0))
        self.assertEqual(handoff.registered_groups(), {})

    def test_shutdown_returns_boundedly_with_a_registered_group(self):
        parent, child = self._socketpair()
        handoff = self.module.RunnerHandoff(parent)
        self.addCleanup(handoff.close)
        child.sendall(self.module.encode_group_frame(b"P", 4242, "leader-token"))
        child.sendall(b"H1 F\n")
        started = time.monotonic()
        self.assertFalse(handoff.request_and_wait(10.0))
        self.assertLess(time.monotonic() - started, 10.0)
        self.assertEqual(handoff.registered_groups(), {4242: "leader-token"})

    def test_exact_group_cleanup_failure_is_a_bounded_harness_error(self):
        parent, child = self._socketpair()
        handoff = self.module.RunnerHandoff(parent)
        self.addCleanup(handoff.close)
        child.sendall(self.module.encode_group_frame(b"P", 4242, "leader-token"))
        child.shutdown(socket.SHUT_WR)
        with mock.patch.object(
            self.module, "_stop_exact_process_group", return_value=False
        ):
            with self.assertRaises(self.module.FlowError) as caught:
                self.module._finish_runner_handoff(handoff, 2.0)
        self.assertEqual(caught.exception.exit_code, 70)
        self.assertEqual(caught.exception.reason_id, "runner_cleanup_failed")


class TimeoutExitCodeTest(unittest.TestCase):
    """MEDIUM 8 -- an expired wall clock is 124, not vendor-unavailable 75."""

    def setUp(self):
        self.module = load_flow_module()

    def test_flow_maps_coder_timeout_to_timeout_not_unavailable(self):
        self.assertEqual(self.module.stage_status(124), ("timeout", "stage_timeout"))
        self.assertEqual(self.module.terminal_exit_for("timeout"), 124)


class PreflightTimeoutTest(FlowTestCase):
    """MEDIUM 8 -- preflight Git work is bounded by the flow deadline."""

    def test_global_deadline_bounds_preflight_git(self):
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        slow_bin = self.base / "slow-bin"
        slow_bin.mkdir()
        slow_git = slow_bin / "git"
        slow_git.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys, time\n"
            "time.sleep(2)\n"
            "os.execv({!r}, [{!r}] + sys.argv[1:])\n".format(real_git, real_git),
            encoding="utf-8",
        )
        slow_git.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = str(slow_bin) + os.pathsep + env.get("PATH", "")
        result = self.run_flow(
            "--wall-timeout", "0.2", "--lane", "normal", "add a feature", env=env,
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(self.worktree_dirs(), [])


def _load_module(path, name):
    import importlib.machinery
    import importlib.util

    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path))
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_runner_module():
    return _load_module(RUNNER, "hermes_coder_under_test")


def load_flow_module():
    return _load_module(FLOW, "hermes_coder_flow_under_test")


HARDENING_CASES = (
    VendorAttestationTest,
    FrozenGateIntegrityTest,
    ProcessGroupCleanupTest,
    RelativeRootTest,
    ClassificationAttemptTest,
    InheritedGitEnvironmentTest,
    QuotaClassificationTest,
    ReviewConsistencyTest,
    DirtySourceClassifierTest,
    SecretMarkerTest,
    GitControlSurfaceTest,
    GitVisibleSnapshotTest,
    StageSnapshotTelemetryTest,
    AdjacentLowHardeningTest,
    FlowLockTest,
    SignalCleanupTest,
    ProcessIdentityTest,
    DirectRunnerIdentityPreflightTest,
    HandoffDrainTest,
    TimeoutExitCodeTest,
    PreflightTimeoutTest,
)


def load_tests(loader, _standard_tests, _pattern):
    """Select only hardening methods, not inherited Phase C test methods."""
    suite = unittest.TestSuite()
    for case in HARDENING_CASES:
        for name in loader.getTestCaseNames(case):
            if name in case.__dict__:
                suite.addTest(case(name))
    return suite


if __name__ == "__main__":
    unittest.main()
