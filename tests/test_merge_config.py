"""Tests for scripts/merge_config.py."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import merge_config  # noqa: E402


class TestDeepMerge(unittest.TestCase):
    def test_adds_missing_keys(self):
        merged = merge_config.deep_merge({"a": 1}, {"b": 2})
        self.assertEqual(merged, {"a": 1, "b": 2})

    def test_keep_existing_preserves_user_values(self):
        merged = merge_config.deep_merge(
            {"tts": {"provider": "elevenlabs"}},
            {"tts": {"provider": "edge", "edge": {"voice": "en-US-AriaNeural"}}},
        )
        self.assertEqual(merged["tts"]["provider"], "elevenlabs")
        self.assertEqual(merged["tts"]["edge"]["voice"], "en-US-AriaNeural")

    def test_overlay_wins_replaces_values(self):
        merged = merge_config.deep_merge(
            {"tts": {"provider": "elevenlabs"}},
            {"tts": {"provider": "edge"}},
            strategy=merge_config.OVERLAY_WINS,
        )
        self.assertEqual(merged["tts"]["provider"], "edge")

    def test_nested_merge_does_not_drop_siblings(self):
        merged = merge_config.deep_merge(
            {"discord": {"voice_fx": {"enabled": True, "mine": 1}}},
            {"discord": {"voice_fx": {"barge_in_enabled": True}}},
        )
        self.assertEqual(
            merged["discord"]["voice_fx"],
            {"enabled": True, "mine": 1, "barge_in_enabled": True},
        )

    def test_lists_are_leaves_not_concatenated(self):
        merged = merge_config.deep_merge(
            {"phrases": ["mine"]}, {"phrases": ["theirs"]},
            strategy=merge_config.OVERLAY_WINS,
        )
        self.assertEqual(merged["phrases"], ["theirs"])

    def test_none_base_takes_overlay(self):
        self.assertEqual(merge_config.deep_merge({"k": None}, {"k": "v"})["k"], "v")

    def test_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}}
        merge_config.deep_merge(base, {"a": {"c": 2}})
        self.assertEqual(base, {"a": {"b": 1}})


class TestYamlSafety(unittest.TestCase):
    def test_rejects_arbitrary_python_objects(self):
        with TemporaryDirectory() as td:
            bad = Path(td) / "bad.yaml"
            bad.write_text("!!python/object/apply:os.system ['echo pwned']\n")
            with self.assertRaises(Exception):
                merge_config.load_yaml(bad)

    def test_rejects_non_mapping_top_level(self):
        with TemporaryDirectory() as td:
            bad = Path(td) / "list.yaml"
            bad.write_text("- one\n- two\n")
            with self.assertRaises(ValueError):
                merge_config.load_yaml(bad)

    def test_missing_file_is_empty_mapping(self):
        self.assertEqual(merge_config.load_yaml(Path("/nonexistent/x.yaml")), {})


class TestApply(unittest.TestCase):
    def _files(self, td):
        base = Path(td) / "config.yaml"
        overlay = Path(td) / "overlay.yaml"
        base.write_text("memory:\n  provider: mem0\n")
        overlay.write_text("memory:\n  provider: holographic\n  write_approval: true\n")
        return base, overlay

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as td:
            base, overlay = self._files(td)
            before = base.read_text()
            rc = merge_config.main(["--base", str(base), "--overlay", str(overlay)])
            self.assertEqual(rc, 0)
            self.assertEqual(base.read_text(), before)

    def test_apply_writes_and_backs_up(self):
        with TemporaryDirectory() as td:
            base, overlay = self._files(td)
            rc = merge_config.main(["--base", str(base), "--overlay", str(overlay), "--apply"])
            self.assertEqual(rc, 0)

            merged = merge_config.load_yaml(base)
            # keep-existing: the user's provider survives, the new key is added.
            self.assertEqual(merged["memory"]["provider"], "mem0")
            self.assertTrue(merged["memory"]["write_approval"])

            backups = list(Path(td).glob("config.yaml.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn("mem0", backups[0].read_text())

    def test_apply_is_idempotent(self):
        with TemporaryDirectory() as td:
            base, overlay = self._files(td)
            merge_config.main(["--base", str(base), "--overlay", str(overlay), "--apply"])
            after_first = base.read_text()
            merge_config.main(["--base", str(base), "--overlay", str(overlay), "--apply"])
            self.assertEqual(base.read_text(), after_first)
            # Second run is a no-op, so it must not pile up more backups.
            self.assertEqual(len(list(Path(td).glob("config.yaml.bak-*"))), 1)

    def test_creates_config_when_base_absent(self):
        with TemporaryDirectory() as td:
            base = Path(td) / "new" / "config.yaml"
            overlay = Path(td) / "overlay.yaml"
            overlay.write_text("streaming:\n  enabled: true\n")
            rc = merge_config.main(["--base", str(base), "--overlay", str(overlay), "--apply"])
            self.assertEqual(rc, 0)
            self.assertTrue(merge_config.load_yaml(base)["streaming"]["enabled"])

    def test_missing_overlay_errors(self):
        with TemporaryDirectory() as td:
            rc = merge_config.main(
                ["--base", str(Path(td) / "c.yaml"), "--overlay", str(Path(td) / "nope.yaml")]
            )
            self.assertEqual(rc, 2)


class TestShippedOverlay(unittest.TestCase):
    def test_repo_overlay_is_valid_yaml_mapping(self):
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)
        for section in ("memory", "delegation", "browser", "code_execution",
                        "streaming", "tts", "stt", "second_brain"):
            self.assertIn(section, data, f"{section} missing from config.example.yaml")

    def test_overlay_carries_no_secrets(self):
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)
        self.assertEqual(data["memory"]["provider"], "")
        self.assertEqual(data["delegation"]["api_key"] if "api_key" in data["delegation"] else "", "")


if __name__ == "__main__":
    unittest.main()
