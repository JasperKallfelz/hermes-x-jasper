"""Deterministic installer tests; no network or vendor model is invoked."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "setup.sh"
PINNED = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
WRAPPERS = ("hermes-coder", "hermes-coder-flow")


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.fake_bin = self.base / "fake-bin"
        self.install_dir = self.base / "upstream"
        self.hermes_home = self.base / "hermes-home"
        self.coder_bin = self.base / "user-bin"
        self.home.mkdir()
        self.fake_bin.mkdir()
        (self.install_dir / ".git").mkdir(parents=True)
        hermes = self.install_dir / "venv" / "bin" / "hermes"
        hermes.parent.mkdir(parents=True)
        hermes.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hermes.chmod(0o755)

        git_stub = self.fake_bin / "git"
        git_stub.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                if [ "$1" = "-C" ]; then
                  shift 2
                fi
                case "$1" in
                  fetch) exit 0 ;;
                  rev-parse) printf '%s\\n' '{PINNED}'; exit 0 ;;
                  diff) exit 0 ;;
                  checkout) exit 0 ;;
                  apply) exit 0 ;;
                  clone) exit 90 ;;
                esac
                exit 91
                """
            ),
            encoding="utf-8",
        )
        git_stub.chmod(0o755)

        python_link = self.fake_bin / "python3"
        python_link.symlink_to(Path(sys.executable).resolve())
        self.env = os.environ.copy()
        for name in (
            "HERMES_INSTALL_DIR",
            "HERMES_HOME",
            "HERMES_CODER_BIN_DIR",
        ):
            self.env.pop(name, None)
        path_parts = [str(self.fake_bin)]
        for command in ("bash", "cmp", "install", "mkdir", "cp", "chmod", "date", "sed", "uname", "basename"):
            resolved = shutil.which(command)
            if resolved:
                directory = str(Path(resolved).parent)
                if directory not in path_parts:
                    path_parts.append(directory)
        self.env.update(HOME=str(self.home), PATH=os.pathsep.join(path_parts))

    def tearDown(self):
        self.temporary.cleanup()

    def run_setup(self, *extra):
        command = [
            "bash",
            str(SETUP),
            "--install-dir",
            str(self.install_dir),
            "--hermes-home",
            str(self.hermes_home),
            "--coder-bin-dir",
            str(self.coder_bin),
            "--skip-voice",
            *extra,
        ]
        return subprocess.run(
            command,
            cwd=REPO,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def tree_snapshot(self):
        snapshot = []
        for candidate in sorted(self.base.rglob("*")):
            relative = candidate.relative_to(self.base).as_posix()
            mode = stat.S_IMODE(candidate.lstat().st_mode)
            if candidate.is_symlink():
                payload = os.readlink(candidate)
            else:
                payload = candidate.read_bytes() if candidate.is_file() else None
            snapshot.append((relative, mode, payload))
        return snapshot

    def test_dry_run_is_write_free(self):
        before = self.tree_snapshot()
        result = self.run_setup("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertFalse(self.coder_bin.exists())
        self.assertFalse(self.hermes_home.exists())
        self.assertIn("dry run", result.stdout)

    def test_default_install_is_idempotent_and_executable(self):
        first = self.run_setup()
        self.assertEqual(first.returncode, 0, first.stderr)
        installed = {}
        for name in WRAPPERS:
            source = REPO / "coder-stack" / "bin" / name
            target = self.coder_bin / name
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            installed[name] = (target.read_bytes(), target.stat().st_mtime_ns)

        second = self.run_setup()
        self.assertEqual(second.returncode, 0, second.stderr)
        for name in WRAPPERS:
            target = self.coder_bin / name
            self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), installed[name])
            self.assertEqual(list(self.coder_bin.glob(name + ".bak-*")), [])
        self.assertIn("already installed and current", second.stdout)

    def test_existing_regular_config_targets_are_untouched(self):
        self.hermes_home.mkdir()
        env_target = self.hermes_home / ".env"
        config_target = self.hermes_home / "config.yaml"
        env_target.write_text("EXISTING=env\n", encoding="utf-8")
        config_target.write_text("existing: config\n", encoding="utf-8")
        env_target.chmod(0o640)
        before = {
            path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in (env_target, config_target)
        }

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        for path, expected in before.items():
            self.assertEqual(
                (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)), expected
            )

    def test_config_target_conflicts_fail_before_any_setup_mutation(self):
        cases = (
            "dangling-env-symlink",
            "env-symlink",
            "dangling-config-symlink",
            "config-directory",
        )
        for case in cases:
            with self.subTest(case=case):
                case_root = self.base / case
                case_root.mkdir()
                hermes_home = case_root / "hermes-home"
                coder_bin = case_root / "bin"
                hermes_home.mkdir()
                external = case_root / "external"
                if case == "dangling-env-symlink":
                    (hermes_home / ".env").symlink_to(external)
                elif case == "env-symlink":
                    external.write_text("external-owned\n", encoding="utf-8")
                    external.chmod(0o644)
                    (hermes_home / ".env").symlink_to(external)
                elif case == "dangling-config-symlink":
                    (hermes_home / "config.yaml").symlink_to(external)
                else:
                    (hermes_home / "config.yaml").mkdir()

                before = self.tree_snapshot()
                result = subprocess.run(
                    [
                        "bash", str(SETUP),
                        "--install-dir", str(self.install_dir),
                        "--hermes-home", str(hermes_home),
                        "--coder-bin-dir", str(coder_bin),
                        "--skip-voice",
                    ],
                    cwd=REPO, env=self.env, capture_output=True, text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn("not a regular file", result.stderr)
                self.assertEqual(self.tree_snapshot(), before)
                self.assertFalse(coder_bin.exists())
                self.assertFalse((hermes_home / "config.yaml").is_file())
                if external.exists():
                    self.assertEqual(external.read_text(), "external-owned\n")
                    self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o644)

    def test_custom_hermes_home_reaches_upstream_installer_and_start_command(self):
        hermes = self.install_dir / "venv" / "bin" / "hermes"
        hermes.unlink()
        capture = self.base / "installer-hermes-home"
        installer = self.install_dir / "setup-hermes.sh"
        installer.write_text(
            "#!/bin/sh\n"
            "printf '%s' \"$HERMES_HOME\" > \"$INSTALLER_CAPTURE\"\n"
            "mkdir -p \"$(dirname \"$0\")/venv/bin\"\n"
            "printf '#!/bin/sh\\nexit 0\\n' > \"$(dirname \"$0\")/venv/bin/hermes\"\n"
            "chmod 755 \"$(dirname \"$0\")/venv/bin/hermes\"\n",
            encoding="utf-8",
        )
        installer.chmod(0o755)
        self.env["INSTALLER_CAPTURE"] = str(capture)

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(capture.read_text(encoding="utf-8"), str(self.hermes_home))
        self.assertIn(
            f"HERMES_HOME='{self.hermes_home}' {self.install_dir}/venv/bin/hermes",
            result.stdout,
        )

    def test_same_content_with_wrong_mode_restores_executable_bit(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.coder_bin / "hermes-coder"
        target.chmod(0o600)
        repaired = self.run_setup()
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        self.assertEqual(target.read_bytes(), (REPO / "coder-stack" / "bin" / target.name).read_bytes())

    def test_conflict_refuses_without_overwriting_or_backup(self):
        self.coder_bin.mkdir()
        target = self.coder_bin / "hermes-coder"
        target.write_text("user-owned wrapper\n", encoding="utf-8")
        target.chmod(0o700)
        result = self.run_setup()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "user-owned wrapper\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(list(self.coder_bin.glob("hermes-coder.bak-*")), [])
        self.assertIn("--replace-coder-stack", result.stderr)
        self.assertFalse(self.hermes_home.exists())

    def test_explicit_replacement_backs_up_first(self):
        self.coder_bin.mkdir()
        target = self.coder_bin / "hermes-coder"
        target.write_text("user-owned wrapper\n", encoding="utf-8")
        target.chmod(0o700)
        result = self.run_setup("--replace-coder-stack")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(self.coder_bin.glob("hermes-coder.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "user-owned wrapper\n")
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o700)
        self.assertEqual(target.read_bytes(), (REPO / "coder-stack" / "bin" / target.name).read_bytes())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_opt_out_skips_wrapper_directory(self):
        result = self.run_setup("--skip-coder-stack")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.coder_bin.exists())
        self.assertIn("Skipping coder stack", result.stdout)

    def test_vendor_clis_are_detected_but_never_invoked_or_modified(self):
        invocation_log = self.base / "vendor-cli-invocations"
        original = {}
        for name in ("claude", "codex"):
            stub = self.fake_bin / name
            stub.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$0 $*\" >> \"$CLI_INVOCATION_LOG\"\n"
                "exit 99\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            original[name] = (stub.read_bytes(), stat.S_IMODE(stub.stat().st_mode))
        self.env["CLI_INVOCATION_LOG"] = str(invocation_log)

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(invocation_log.exists())
        for name, expected in original.items():
            stub = self.fake_bin / name
            self.assertEqual((stub.read_bytes(), stat.S_IMODE(stub.stat().st_mode)), expected)
        self.assertIn("Claude Code CLI found", result.stdout)
        self.assertIn("Codex CLI found", result.stdout)


@unittest.skipUnless(sys.platform == "darwin", "Darwin-only Git identity fixture")
class DarwinGitIdentityFixtureTest(unittest.TestCase):
    """The outer fixture must be transparent except for bounded Git lifetime."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo with spaces"
        self.repo.mkdir()
        self.fixture = REPO / "tests" / "fixtures" / "darwin-git-bin" / "git"
        initialized = subprocess.run(
            ["/usr/bin/git", "init", "--quiet"],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)

    def run_git(self, executable, *args):
        return subprocess.run(
            [str(executable), *args],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_forwards_bounded_argv_streams_and_failure_status(self):
        prefix = (
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=",
        )
        commands = (
            prefix + ("rev-parse", "HEAD"),
            prefix + ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            prefix + ("diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"),
            prefix + ("diff", "--binary", "--no-ext-diff", "--no-textconv", "--cached", "HEAD", "--"),
            prefix + ("ls-files", "--others", "--exclude-standard", "-z"),
            prefix + ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
            prefix + ("show", "refs/heads/definitely-missing"),
        )
        for argv in commands:
            with self.subTest(argv=argv):
                expected = self.run_git("/usr/bin/git", *argv)
                actual = self.run_git(self.fixture, *argv)
                self.assertEqual(actual.returncode, expected.returncode)
                self.assertEqual(actual.stdout, expected.stdout)
                self.assertEqual(actual.stderr, expected.stderr)

    def test_only_bounded_identity_calls_receive_a_scheduling_window(self):
        bounded = (
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=",
            "status", "--porcelain=v1", "-z", "--untracked-files=all",
        )

        def fastest(argv):
            samples = []
            for _ in range(5):
                started = time.monotonic()
                result = self.run_git(self.fixture, *argv)
                samples.append(time.monotonic() - started)
                self.assertEqual(result.returncode, 0, result.stderr)
            return min(samples)

        ordinary = (
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=",
            "status", "--porcelain", "--untracked-files=normal",
        )
        ordinary_seconds = fastest(ordinary)
        bounded_seconds = fastest(bounded)
        self.assertGreaterEqual(bounded_seconds, 0.025)
        self.assertGreater(
            bounded_seconds - ordinary_seconds,
            0.005,
            (ordinary_seconds, bounded_seconds),
        )


if __name__ == "__main__":
    unittest.main()
