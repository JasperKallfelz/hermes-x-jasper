"""Publication and install-boundary checks for the sanitized server bundle."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_server", REPO / "scripts/setup_server.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ServerSnapshotTests(unittest.TestCase):
    def test_opt_in_plugins_are_enabled_in_native_config(self):
        names = ["hermes-lcm", "hermes-context-inbox", "process-observatory"]
        config = yaml.safe_load(setup.configuration(names))
        self.assertEqual(config["plugins"]["enabled"], names)
        self.assertEqual(config["context"]["engine"], "lcm")
        default = yaml.safe_load(setup.configuration([]))
        self.assertEqual(default["plugins"]["enabled"], [])
        self.assertEqual(default["context"]["engine"], "compressor")

    def test_checksums_cover_the_complete_public_snapshot(self):
        lock = setup.verify_snapshot()
        self.assertEqual(lock["upstream_repository"], "https://github.com/NousResearch/hermes-agent.git")

    def test_snapshot_has_no_runtime_state_or_private_config(self):
        forbidden = {"auth.json", "state.json", "config.yaml", "manifest.json", "USER.md", "MEMORY.md", "SOUL.md", ".env"}
        for path in (REPO / "server").rglob("*"):
            if path.is_file():
                self.assertNotIn(path.name, forbidden, str(path))
                self.assertNotIn(path.suffix, {".sqlite", ".sqlite3", ".db", ".session", ".pem", ".key"}, str(path))
            self.assertFalse(path.is_symlink(), str(path))

    def test_dry_run_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / "fresh"
            before = list(Path(temp).rglob("*"))
            result = subprocess.run([sys.executable, str(REPO / "scripts/setup_server.py"), "--root", str(dest), "--dry-run", "--with-lcm"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(temp).rglob("*")), before)
            self.assertIn("no network requests", result.stdout)

    def test_existing_install_is_preserved_before_network_or_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve()
            marker = dest / "keep.txt"
            marker.write_text("user-owned")
            result = subprocess.run([sys.executable, str(REPO / "scripts/setup_server.py"), "--root", str(dest)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr)
            self.assertEqual(marker.read_text(), "user-owned")
            self.assertEqual(list(dest.iterdir()), [marker])

    def test_launcher_preserves_quoted_paths_and_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "a path with 'quotes' and $literal"
            root.mkdir()
            probe = root / "probe.py"
            probe.write_text("import os,json,sys; print(json.dumps({'home':os.environ['HERMES_HOME'],'args':sys.argv[1:]}))")
            script = root / "launch"
            script.write_text(setup.launcher(root, [sys.executable, str(probe)]))
            args = ["space argument", "$(should-not-execute)", "'literal'"]
            result = subprocess.run(["bash", str(script), *args], capture_output=True, text=True, check=True)
            value = json.loads(result.stdout)
            self.assertEqual(value["home"], str(root / "home"))
            self.assertEqual(value["args"], args)

    def test_private_writer_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "private"
            setup.write_private(target, "first")
            with self.assertRaises(FileExistsError):
                setup.write_private(target, "second")
            self.assertEqual(target.read_text(), "first")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_generated_manifest_example_has_no_absolute_operator_home(self):
        content = (REPO / "server/second-brain/config/manifest.example.json").read_text()
        self.assertNotIn(str(Path.home()), content)
        self.assertFalse(json.loads(content)["dreaming"]["enabled"])


if __name__ == "__main__":
    unittest.main()
