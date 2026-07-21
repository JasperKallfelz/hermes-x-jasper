"""Enforce the deterministic Gitleaks release gate and its narrowness.

Two things are proven here whenever a pinned ``gitleaks`` is available:

1. The shipped ``.gitleaks.toml`` clears this repo's known-immutable historical
   false positives AND the current working tree — both scans exit clean.
2. The rule-bound, commit+path-scoped allowlist is *narrow*: a brand-new
   high-entropy secret committed to the very same path in a different commit is
   still reported. A global allowlist was shown (gitleaks 8.30.1) to hide such a
   canary even with ``condition = "AND"``; the rule-bound form does not.

No real-looking secret is stored in this file. Both canary tokens are generated
at runtime.
"""
import json
import os
import random
import shutil
import string
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
GITLEAKS = shutil.which("gitleaks")


def _high_entropy_token(tag: str) -> str:
    """A realistic, high-entropy secret built at runtime (never stored static)."""
    rng = random.Random("gitleaks-canary::" + tag)
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(40))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.email=test@example.com", "-c", "user.name=test",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, env=env, check=True,
    )


@unittest.skipUnless(GITLEAKS, "gitleaks not installed")
class GitleaksGateTest(unittest.TestCase):
    def test_release_gate_script_reports_current_tree_and_history_clean(self):
        proc = subprocess.run(
            ["bash", str(REPO / "scripts" / "gitleaks_scan.sh")],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("clean", proc.stdout)

    def test_rule_bound_allowlist_still_reports_a_new_same_path_canary(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _git(root, "init", "-q")
            fixture = root / "tests" / "test_audit_public.py"
            fixture.parent.mkdir(parents=True)

            # commit A: a fixture we will explicitly allowlist by commit + path
            allowlisted = _high_entropy_token("A")
            fixture.write_text('KEY = "' + allowlisted + '"\n')
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "A")
            commit_a = _git(root, "rev-parse", "HEAD").stdout.strip()

            # rule-bound (NOT global) allowlist scoped to commit A + this exact path
            (root / ".gitleaks.toml").write_text(
                "[extend]\nuseDefault = true\n\n"
                '[[rules]]\nid = "generic-api-key"\n\n'
                "  [[rules.allowlists]]\n"
                '  condition = "AND"\n'
                '  commits = ["' + commit_a + '"]\n'
                "  paths = ['''tests/test_audit_public\\.py''']\n"
            )
            config = str(root / ".gitleaks.toml")

            # commit A's fixture is suppressed -> history is clean
            rc_a = subprocess.run(
                ["gitleaks", "git", str(root), "--config", config, "--no-banner"],
            ).returncode
            self.assertEqual(rc_a, 0, "allowlisted historical fixture should be clean")

            # commit B: a NEW high-entropy canary at the SAME path, different commit.
            # Use a secret-keyword assignment so generic-api-key can fire on it.
            canary = _high_entropy_token("B")
            fixture.write_text('KEY = "' + allowlisted + '"\napi_key = "' + canary + '"\n')
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "B")
            commit_b = _git(root, "rev-parse", "HEAD").stdout.strip()

            report = root / "out.json"
            rc_b = subprocess.run(
                ["gitleaks", "git", str(root), "--config", config, "--no-banner",
                 "--report-format", "json", "--report-path", str(report)],
            ).returncode
            self.assertEqual(rc_b, 1, "a new same-path canary must NOT be hidden")

            findings = json.loads(report.read_text()) if report.exists() else []
            leak_commits = {f.get("Commit") for f in findings}
            self.assertIn(commit_b, leak_commits, "the new canary commit must be reported")
            self.assertNotIn(commit_a, leak_commits, "the allowlisted commit must stay suppressed")


if __name__ == "__main__":
    unittest.main()
