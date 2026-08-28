"""Adversarial tests for the exact pinned Gitleaks release binary and gate."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
from pathlib import Path
import shutil
import string
import subprocess
import tarfile
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
VERSION = "8.30.1"
DEFAULT_BINARY = REPO / ".tools" / "gitleaks" / VERSION / "gitleaks"
GITLEAKS = Path(os.environ.get("HERMES_TEST_GITLEAKS", DEFAULT_BINARY))


def token(tag: str) -> str:
    rng = random.Random("gitleaks-canary::" + tag)
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(48))


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


class GitleaksGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not GITLEAKS.is_file():
            raise AssertionError(
                f"pinned Gitleaks missing at {GITLEAKS}; run scripts/install_gitleaks.sh"
            )
        version = subprocess.run(
            [str(GITLEAKS), "version"], text=True, capture_output=True, check=False
        )
        if version.returncode or version.stdout.strip() != VERSION:
            raise AssertionError("adversarial tests require exactly Gitleaks 8.30.1")

    def make_gate_repo(self, root: Path) -> None:
        (root / "scripts").mkdir(parents=True)
        (root / "security").mkdir()
        shutil.copy2(REPO / "scripts/gitleaks_scan.sh", root / "scripts")
        shutil.copy2(REPO / "scripts/install_gitleaks.sh", root / "scripts")
        shutil.copy2(REPO / ".gitleaks.toml", root)
        git(root, "init", "--quiet")
        (root / "safe.txt").write_text("safe\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "safe")

        os_name = "darwin" if platform.system() == "Darwin" else "linux"
        arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"
        asset = f"gitleaks_{VERSION}_{os_name}_{arch}.tar.gz"
        tool_dir = root / ".tools" / "gitleaks" / VERSION
        tool_dir.mkdir(parents=True)
        binary = tool_dir / "gitleaks"
        shutil.copy2(GITLEAKS.resolve(), binary)
        binary.chmod(0o755)
        archive = tool_dir / asset
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(binary, arcname="gitleaks")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (root / "security/gitleaks-8.30.1.sha256").write_text(
            f"{digest}  {asset}\n", encoding="utf-8"
        )

    def run_gate(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(root / "scripts/gitleaks_scan.sh")],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_shipped_config_clears_current_tree_and_full_history(self) -> None:
        for mode, source in (("dir", REPO), ("git", REPO)):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [str(GITLEAKS), mode, str(source), "--config", str(REPO / ".gitleaks.toml"),
                     "--redact", "--no-banner", "--ignore-gitleaks-allow"],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_gate_rejects_inline_allow_canary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_gate_repo(root)
            (root / "inline.py").write_text(
                'api_key = "' + token("inline") + '"  # gitleaks:allow\n', encoding="utf-8"
            )
            result = self.run_gate(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("current-tree secrets found", result.stdout)

    def test_gate_rejects_any_unreviewed_ignore_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_gate_repo(root)
            (root / ".gitleaksignore").write_text(
                "deadbeef:fixture.py:generic-api-key:1\n", encoding="utf-8"
            )
            result = self.run_gate(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("unreviewed .gitleaksignore", result.stderr)

    def test_gate_rejects_an_ignored_unreviewed_gitleaksignore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_gate_repo(root)
            (root / ".gitignore").write_text(".gitleaksignore\n", encoding="utf-8")
            (root / ".gitleaksignore").write_text("ignored-fingerprint\n", encoding="utf-8")
            result = self.run_gate(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("unreviewed .gitleaksignore", result.stderr)

    def test_rule_bound_allowlist_does_not_hide_new_same_path_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            git(root, "init", "--quiet")
            fixture = root / "tests/test_audit_public.py"
            fixture.parent.mkdir()
            allowed = token("old")
            fixture.write_text('api_key = "' + allowed + '"\n', encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "--quiet", "-m", "old")
            old_commit = git(root, "rev-parse", "HEAD").stdout.strip()
            config = root / ".gitleaks.toml"
            config.write_text(
                "[extend]\nuseDefault = true\n\n"
                '[[rules]]\nid = "generic-api-key"\n\n'
                "  [[rules.allowlists]]\n  condition = \"AND\"\n"
                f'  commits = ["{old_commit}"]\n'
                "  paths = ['''tests/test_audit_public\\.py''']\n",
                encoding="utf-8",
            )
            fixture.write_text(
                'api_key = "' + allowed + '"\napi_key = "' + token("new") + '"\n',
                encoding="utf-8",
            )
            git(root, "add", "-A")
            git(root, "commit", "--quiet", "-m", "new")
            new_commit = git(root, "rev-parse", "HEAD").stdout.strip()
            report = root / "report.json"
            result = subprocess.run(
                [str(GITLEAKS), "git", str(root), "--config", str(config), "--no-banner",
                 "--ignore-gitleaks-allow", "--report-format", "json", "--report-path", str(report)],
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            findings = json.loads(report.read_text())
            commits = {item.get("Commit") for item in findings}
            self.assertIn(new_commit, commits)
            self.assertNotIn(old_commit, commits)

    def test_installer_is_workspace_local_checksum_pinned_and_sudo_free(self) -> None:
        script = (REPO / "scripts/install_gitleaks.sh").read_text(encoding="utf-8")
        self.assertIn('VERSION="8.30.1"', script)
        self.assertIn("security/gitleaks-8.30.1.sha256", script)
        self.assertIn("sha256_file", script)
        self.assertIn(".tools/gitleaks", script)
        self.assertNotIn("sudo", script)

    def test_version_mismatch_is_fatal_even_when_test_archive_checksum_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_gate_repo(root)
            tool_dir = root / ".tools/gitleaks" / VERSION
            binary = tool_dir / "gitleaks"
            binary.write_text("#!/bin/sh\necho not-a-version\n", encoding="utf-8")
            binary.chmod(0o755)
            archive = next(tool_dir.glob("*.tar.gz"))
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(binary, arcname="gitleaks")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (root / "security/gitleaks-8.30.1.sha256").write_text(
                f"{digest}  {archive.name}\n", encoding="utf-8"
            )
            result = subprocess.run(
                ["bash", str(root / "scripts/install_gitleaks.sh"), "--verify-only"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("version verification", result.stderr)


if __name__ == "__main__":
    unittest.main()
