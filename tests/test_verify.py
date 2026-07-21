"""Fail-closed verifier control-flow tests without running the real suites."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]


class VerifyControlFlowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "coder-stack").mkdir()
        shutil.copy2(REPO / "verify.sh", self.root)
        (self.root / "scripts" / "gitleaks_scan.sh").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8"
        )
        self.log = self.root / "calls.log"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._stub("shellcheck", "exit 0")
        self._stub("gitleaks", "exit 0")
        self._stub("uname", "printf 'Linux\\n'")

    def _stub(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def run_verify(self, pytest_status=0, include_gitleaks=True):
        if not include_gitleaks:
            (self.bin / "gitleaks").unlink()
        self._stub(
            "python3",
            textwrap.dedent(
                f"""\
                printf '%s::%s\\n' "$PWD" "$*" >> "$VERIFY_CALL_LOG"
                if [ "$1 $2" = "-m pytest" ]; then exit {pytest_status}; fi
                exit 0
                """
            ),
        )
        env = os.environ.copy()
        env.update(
            PATH=f"{self.bin}:/usr/bin:/bin",
            VERIFY_CALL_LOG=str(self.log),
        )
        return subprocess.run(
            ["/bin/bash", str(self.root / "verify.sh"), "--offline"],
            cwd=self.root, env=env, capture_output=True, text=True, check=False,
        )

    def test_pytest_failure_cannot_fall_back_to_root_unittest(self):
        result = self.run_verify(pytest_status=1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("::-m pytest tests second-brain/tests -q" in c for c in calls))
        root_unittest = [
            c for c in calls
            if c.startswith(str(self.root) + "::") and "-m unittest" in c
        ]
        self.assertEqual(root_unittest, [])

    def test_missing_gitleaks_is_a_failure_not_a_skip(self):
        result = self.run_verify(include_gitleaks=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("gitleaks missing", result.stdout)


if __name__ == "__main__":
    unittest.main()
