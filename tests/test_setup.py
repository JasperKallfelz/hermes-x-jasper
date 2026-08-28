"""Real-Git installer contract tests; no network or model is invoked."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[1]
# Production release tuple remains asserted cross-file even though these real
# Git fixtures substitute their own deterministic commit at runtime.
PRODUCTION_PIN = "5fc308a70719a83cccdbba4c0e39c23f5a8239d5"


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def tree_digest(root: Path, exclude_git: bool = False) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "absent"
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if exclude_git and relative.parts and relative.parts[0] == ".git":
            continue
        metadata = path.lstat()
        digest.update(relative.as_posix().encode() + b"\0")
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode() + b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class SetupTest(unittest.TestCase):
    VERSION = "v9.9.9"
    TAG = "fixture-v1"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "home"
        self.source = self.base / "source"
        self.install = self.base / "install"
        self.hermes_home = self.base / "hermes-home"
        self.coder_bin = self.base / "bin"
        self.starter = self.base / "starter"
        self.home.mkdir()
        self._make_starter()
        self._make_upstream()
        self._clone_at_pin()
        self.install_log = self.base / "installer.log"
        self.pip_log = self.base / "pip.log"
        self.tools_bin = self.base / "test-tools"
        self.tools_bin.mkdir()
        for name in ("python3", "python3.11"):
            (self.tools_bin / name).symlink_to(Path(sys.executable).resolve())
        self.env = os.environ.copy()
        self.env.update(
            HOME=str(self.home),
            PATH=str(self.tools_bin) + os.pathsep + self.env.get("PATH", ""),
            HERMES_SETUP_TESTING="1",
            HERMES_SETUP_TEST_REPO=str(self.source),
            HERMES_SETUP_TEST_TAG=self.TAG,
            HERMES_SETUP_TEST_PIN=self.pin,
            HERMES_SETUP_TEST_VERSION=self.VERSION,
            INSTALL_LOG=str(self.install_log),
            PIP_LOG=str(self.pip_log),
        )

    def _make_starter(self) -> None:
        for directory in ("scripts", "patches", "coder-stack/bin"):
            (self.starter / directory).mkdir(parents=True, exist_ok=True)
        for relative in (
            "setup.sh",
            "scripts/verify_upstream_checkout.py",
            "scripts/install_state.py",
            "scripts/merge_config.py",
            ".env.example",
        ):
            shutil.copy2(REPO / relative, self.starter / relative)
        for name in ("hermes-coder", "hermes-coder-flow"):
            shutil.copy2(REPO / "coder-stack/bin" / name, self.starter / "coder-stack/bin" / name)
        (self.starter / "config.example.yaml").write_text(
            "model:\n  provider: starter-default\nstarter:\n  enabled: true\n",
            encoding="utf-8",
        )
        (self.starter / "README.md").write_text("fixture docs\n", encoding="utf-8")
        (self.starter / "docs").mkdir()
        (self.starter / "docs/TROUBLESHOOTING.md").write_text("fixture\n", encoding="utf-8")

    def _make_upstream(self) -> None:
        self.source.mkdir()
        run_git(self.source, "init", "--quiet")
        run_git(self.source, "config", "user.name", "Fixture")
        run_git(self.source, "config", "user.email", "fixture@example.invalid")
        (self.source / ".gitignore").write_text("venv/\n", encoding="utf-8")
        (self.source / "app.txt").write_text("base\n", encoding="utf-8")
        (self.source / "pyproject.toml").write_text(
            "[project]\nname='fixture-hermes'\nversion='9.9.9'\n", encoding="utf-8"
        )
        installer = self.source / "setup-hermes.sh"
        installer.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "cd \"$(dirname \"$0\")\"\n"
            "printf 'install\\n' >> \"$INSTALL_LOG\"\n"
            "python3 -m venv --system-site-packages venv\n"
            "mkdir -p venv/bin\n"
            "cat > venv/bin/hermes <<'SH'\n"
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then echo 'Hermes 9.9.9'; exit 0; fi\n"
            "exit 0\n"
            "SH\n"
            "chmod 755 venv/bin/hermes\n"
            "cat > venv/bin/pip <<'SH'\n"
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = check ]; then exit 0; fi\n"
            "printf '%s\\n' \"$*\" >> \"$PIP_LOG\"\n"
            "exit 0\n"
            "SH\n"
            "chmod 755 venv/bin/pip\n"
            "if [ \"${INTERRUPT_INSTALL:-0}\" = 1 ]; then exit 7; fi\n"
            "if [ -n \"${WIZARD_PROVIDER:-}\" ] && [ ! -e \"$HERMES_HOME/config.yaml\" ]; then\n"
            "  mkdir -p \"$HERMES_HOME\"\n"
            "  printf 'model:\\n  provider: %s\\n' \"$WIZARD_PROVIDER\" > \"$HERMES_HOME/config.yaml\"\n"
            "fi\n",
            encoding="utf-8",
        )
        installer.chmod(0o755)
        run_git(self.source, "add", "-A")
        run_git(self.source, "commit", "--quiet", "-m", "fixture pin")
        self.pin = run_git(self.source, "rev-parse", "HEAD").stdout.strip()
        run_git(self.source, "tag", self.TAG)

        (self.source / "app.txt").write_text("patched\n", encoding="utf-8")
        (self.source / "patch-owned.txt").write_text("new patch file\n", encoding="utf-8")
        patch = run_git(self.source, "diff", "--binary", "--full-index").stdout
        # Untracked patch-owned paths need an explicit no-index fragment.
        added = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--full-index", "/dev/null", "patch-owned.txt"],
            cwd=self.source,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        ).stdout
        added = added.replace("b/patch-owned.txt", "b/patch-owned.txt")
        (self.starter / "patches/voice-and-desktop-features.patch").write_text(
            patch + added, encoding="utf-8"
        )
        (self.source / "app.txt").write_text("base\n", encoding="utf-8")
        (self.source / "patch-owned.txt").unlink()

    def _clone_at_pin(self) -> None:
        subprocess.run(
            ["git", "clone", "--quiet", str(self.source), str(self.install)], check=True
        )
        run_git(self.install, "checkout", "--quiet", "--detach", self.pin)

    def run_setup(self, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        selected = self.env.copy()
        if env:
            selected.update(env)
        return subprocess.run(
            [
                "/bin/bash",
                str(self.starter / "setup.sh"),
                "--install-dir", str(self.install),
                "--hermes-home", str(self.hermes_home),
                "--coder-bin-dir", str(self.coder_bin),
                *extra,
            ],
            cwd=self.starter,
            env=selected,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def git_contract_snapshot(self) -> tuple[str, str, str, str]:
        return (
            run_git(self.install, "rev-parse", "HEAD").stdout,
            tree_digest(self.install / ".git"),
            tree_digest(self.install, exclude_git=True),
            run_git(self.install, "show-ref", check=False).stdout,
        )

    def test_dry_run_is_write_free_and_exact(self) -> None:
        before = tree_digest(self.base)
        result = self.run_setup("--dry-run", "--skip-voice", "--skip-coder-stack")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree_digest(self.base), before)
        self.assertIn("exact local checkout state: clean", result.stdout)

    def test_source_and_patch_proof_precede_wrapper_installation(self) -> None:
        source = (self.starter / "setup.sh").read_text(encoding="utf-8")
        self.assertLess(
            source.index('info "Proving pinned upstream checkout"'),
            source.index('info "Installing subscription-backed coding wrappers"'),
        )

    def test_relative_targets_are_reported_as_absolute_physical_paths(self) -> None:
        relative_home = os.path.relpath(self.hermes_home, self.starter)
        result = self.run_setup(
            "--dry-run", "--skip-voice", "--skip-coder-stack",
            "--hermes-home", relative_home,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"hermes home : {self.hermes_home}", result.stdout)

    def test_clean_wrong_commit_rejects_without_git_or_wrapper_mutation(self) -> None:
        (self.install / "later.txt").write_text("later\n", encoding="utf-8")
        run_git(self.install, "add", "later.txt")
        run_git(
            self.install, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--quiet", "-m", "wrong commit",
        )
        before = self.git_contract_snapshot()
        result = self.run_setup("--skip-voice")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HEAD is not the pinned commit", result.stderr)
        self.assertEqual(self.git_contract_snapshot(), before)
        self.assertFalse(self.coder_bin.exists())

    def test_assume_unchanged_edit_is_detected_without_mutation(self) -> None:
        run_git(self.install, "update-index", "--assume-unchanged", "app.txt")
        (self.install / "app.txt").write_text("hidden edit\n", encoding="utf-8")
        before = self.git_contract_snapshot()
        result = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(result.returncode, 1)
        self.assertIn("content differs", result.stderr)
        self.assertEqual(self.git_contract_snapshot(), before)

    def test_info_exclude_cannot_hide_untracked_file(self) -> None:
        exclude = self.install / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\nhidden-local\n", encoding="utf-8")
        (self.install / "hidden-local").write_text("unexpected\n", encoding="utf-8")
        before = self.git_contract_snapshot()
        result = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(result.returncode, 1)
        self.assertIn("content differs", result.stderr)
        self.assertEqual(self.git_contract_snapshot(), before)

    def test_skip_worktree_edit_is_detected_without_mutation(self) -> None:
        run_git(self.install, "update-index", "--skip-worktree", "app.txt")
        (self.install / "app.txt").write_text("hidden edit\n", encoding="utf-8")
        before = self.git_contract_snapshot()
        result = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(result.returncode, 1)
        self.assertIn("content differs", result.stderr)
        self.assertEqual(self.git_contract_snapshot(), before)

    def test_dirty_tracked_untracked_and_staged_states_are_rejected_read_only(self) -> None:
        mutations = ("tracked", "untracked", "staged")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                shutil.rmtree(self.install)
                self._clone_at_pin()
                if mutation == "tracked":
                    (self.install / "app.txt").write_text("dirty\n", encoding="utf-8")
                else:
                    (self.install / "local.txt").write_text("dirty\n", encoding="utf-8")
                    if mutation == "staged":
                        run_git(self.install, "add", "local.txt")
                before = self.git_contract_snapshot()
                result = self.run_setup("--skip-voice", "--skip-coder-stack")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.git_contract_snapshot(), before)

    def test_interrupted_install_has_no_marker_and_rerun_repairs(self) -> None:
        failed = self.run_setup(
            "--skip-voice", "--skip-coder-stack", env={"INTERRUPT_INSTALL": "1"}
        )
        self.assertNotEqual(failed.returncode, 0)
        marker = self.install / "venv/.hermes-starter-complete.json"
        self.assertFalse(marker.exists())
        self.assertTrue((self.install / "venv/bin/hermes").is_file())
        repaired = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertTrue(marker.is_file())
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_stale_pin_marker_triggers_repair(self) -> None:
        first = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        marker = self.install / "venv/.hermes-starter-complete.json"
        state = json.loads(marker.read_text(encoding="utf-8"))
        state["upstream_commit"] = "0" * 40
        marker.write_text(json.dumps(state), encoding="utf-8")
        marker.chmod(0o600)
        repaired = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertEqual(self.install_log.read_text().count("install\n"), 2)

    def test_executable_exit_42_triggers_repair(self) -> None:
        self.assertEqual(self.run_setup("--skip-voice", "--skip-coder-stack").returncode, 0)
        binary = self.install / "venv/bin/hermes"
        binary.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
        binary.chmod(0o755)
        repaired = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertEqual(self.install_log.read_text().count("install\n"), 2)

    def test_unchanged_rerun_does_not_run_installer_or_voice_pip(self) -> None:
        first = self.run_setup("--skip-coder-stack")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        counts = (
            self.install_log.read_text().count("install\n"),
            self.pip_log.read_text().count("install"),
        )
        second = self.run_setup("--skip-coder-stack")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(
            (self.install_log.read_text().count("install\n"), self.pip_log.read_text().count("install")),
            counts,
        )
        self.assertIn("verified environment marker", second.stdout)

    def test_wizard_created_config_is_merged_and_wizard_values_win(self) -> None:
        result = self.run_setup(
            "--skip-voice", "--skip-coder-stack", env={"WIZARD_PROVIDER": "wizard-choice"}
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = yaml.safe_load((self.hermes_home / "config.yaml").read_text())
        self.assertEqual(config["model"]["provider"], "wizard-choice")
        self.assertTrue(config["starter"]["enabled"])
        self.assertIn("wizard values preserved", result.stdout)

    def test_genuinely_preexisting_config_is_byte_and_mode_preserved(self) -> None:
        self.hermes_home.mkdir()
        config = self.hermes_home / "config.yaml"
        config.write_text("user: owned\n", encoding="utf-8")
        config.chmod(0o640)
        before = (config.read_bytes(), stat.S_IMODE(config.stat().st_mode))
        result = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((config.read_bytes(), stat.S_IMODE(config.stat().st_mode)), before)

    def test_exact_patched_checkout_reruns_offline(self) -> None:
        first = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        shutil.rmtree(self.source)
        second = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("offline-safe", second.stdout)

    def test_path_alias_symlink_file_and_overlap_rejections_precede_mutation(self) -> None:
        cases: list[tuple[str, list[str]]] = []
        cases.append(("trailing", ["--hermes-home", str(self.hermes_home) + "/"]))
        linked_parent = self.base / "linked-parent"
        linked_parent.symlink_to(self.base / "real-parent", target_is_directory=True)
        (self.base / "real-parent").mkdir()
        cases.append(("symlink", ["--hermes-home", str(linked_parent / "home")]))
        file_leaf = self.base / "not-directory"
        file_leaf.write_text("x", encoding="utf-8")
        cases.append(("file", ["--hermes-home", str(file_leaf)]))
        cases.append(("overlap", ["--hermes-home", str(self.install / "state")]))
        for label, args in cases:
            with self.subTest(label=label):
                before = self.git_contract_snapshot()
                result = self.run_setup("--skip-voice", "--skip-coder-stack", *args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.git_contract_snapshot(), before)

    def test_rejected_origin_is_redacted(self) -> None:
        unsafe = "https://user:credential@example.invalid/repo?token=credential"
        run_git(self.install, "remote", "set-url", "origin", unsafe)
        helper = self.starter / "scripts/verify_upstream_checkout.py"
        result = subprocess.run(
            [sys.executable, str(helper), "--checkout", str(self.install),
             "--patch", str(self.starter / "patches/voice-and-desktop-features.patch"),
             "--pin", self.pin],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("credential", result.stdout + result.stderr)
        self.assertNotIn(unsafe, result.stdout + result.stderr)

    def test_missing_target_below_unwritable_parent_is_rejected(self) -> None:
        parent = self.base / "locked-parent"
        parent.mkdir()
        parent.chmod(0o555)
        try:
            before = self.git_contract_snapshot()
            result = self.run_setup(
                "--skip-voice", "--skip-coder-stack",
                "--hermes-home", str(parent / "missing-home"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("writable safe parent", result.stderr)
            self.assertEqual(self.git_contract_snapshot(), before)
        finally:
            parent.chmod(0o755)

    def test_absent_clone_verifies_exact_tag_before_install(self) -> None:
        shutil.rmtree(self.install)
        result = self.run_setup("--skip-voice", "--skip-coder-stack")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(run_git(self.install, "rev-parse", "HEAD").stdout.strip(), self.pin)

    def test_wrong_tag_peel_never_installs_target(self) -> None:
        (self.source / "later.txt").write_text("later\n", encoding="utf-8")
        run_git(self.source, "add", "later.txt")
        run_git(self.source, "commit", "--quiet", "-m", "later")
        run_git(self.source, "tag", "wrong-tag")
        shutil.rmtree(self.install)
        result = self.run_setup(
            "--skip-voice", "--skip-coder-stack",
            env={"HERMES_SETUP_TEST_TAG": "wrong-tag"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.install.exists())


if __name__ == "__main__":
    unittest.main()
