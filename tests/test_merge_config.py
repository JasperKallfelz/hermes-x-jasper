"""Tests for scripts/merge_config.py."""
import sys
import unittest
from unittest import mock
import datetime
import os
import stat
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

    def test_replacement_and_backup_preserve_private_source_modes_under_umask(self):
        for source_mode in (0o600, 0o640):
            with self.subTest(source_mode=oct(source_mode)), TemporaryDirectory() as td:
                base, overlay = self._files(td)
                base.chmod(source_mode)
                old_umask = os.umask(0o022)
                try:
                    rc = merge_config.main(
                        ["--base", str(base), "--overlay", str(overlay), "--apply"]
                    )
                finally:
                    os.umask(old_umask)
                self.assertEqual(rc, 0)
                backup = next(Path(td).glob("config.yaml.bak-*"))
                self.assertEqual(stat.S_IMODE(base.stat().st_mode), source_mode)
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), source_mode)

    def test_fixed_clock_backups_use_exclusive_collision_counter(self):
        class FixedDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 8, 28, 12, 0, 0, tzinfo=tz)

        with TemporaryDirectory() as td:
            source = Path(td) / "config.yaml"
            source.write_text("one: 1\n", encoding="utf-8")
            source.chmod(0o600)
            with (
                mock.patch.object(merge_config._dt, "datetime", FixedDateTime),
                mock.patch.object(merge_config.time, "time_ns", return_value=123456789),
            ):
                first = merge_config.backup(source)
                source.write_text("two: 2\n", encoding="utf-8")
                second = merge_config.backup(source)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(), "one: 1\n")
            self.assertEqual(second.read_text(), "two: 2\n")
            self.assertTrue(second.name.endswith("-1"))

    def test_new_config_is_private_even_with_permissive_umask(self):
        with TemporaryDirectory() as td:
            base = Path(td) / "new.yaml"
            overlay = Path(td) / "overlay.yaml"
            overlay.write_text("safe: true\n", encoding="utf-8")
            old_umask = os.umask(0)
            try:
                rc = merge_config.main(
                    ["--base", str(base), "--overlay", str(overlay), "--apply"]
                )
            finally:
                os.umask(old_umask)
            self.assertEqual(rc, 0)
            self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o600)


class TestShippedOverlay(unittest.TestCase):
    def test_repo_overlay_is_valid_yaml_mapping(self):
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)
        for section in ("memory", "context", "delegation", "browser",
                        "code_execution", "streaming", "tts", "stt", "voice"):
            self.assertIn(section, data, f"{section} missing from config.example.yaml")

    def test_overlay_has_no_non_upstream_second_brain_block(self):
        # `second_brain:` is not an upstream Hermes config section; v0.20.6 does not
        # retain unknown top-level keys. It must not leak into the Hermes overlay.
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)
        self.assertNotIn("second_brain", data)

    def test_overlay_carries_no_secrets(self):
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)
        self.assertEqual(data["memory"]["provider"], "")
        self.assertEqual(data["delegation"]["api_key"] if "api_key" in data["delegation"] else "", "")

    def test_overlay_contains_only_the_reviewed_v0206_leaf_keys(self):
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)

        def leaves(value, prefix=""):
            found = set()
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(child, dict):
                    found.update(leaves(child, path))
                else:
                    found.add(path)
            return found

        self.assertEqual(
            leaves(data),
            {
                "memory.memory_enabled",
                "memory.user_profile_enabled",
                "memory.write_approval",
                "memory.provider",
                "context.engine",
                "delegation.orchestrator_enabled",
                "delegation.model",
                "delegation.provider",
                "delegation.max_concurrent_children",
                "delegation.max_spawn_depth",
                "delegation.subagent_auto_approve",
                "browser.engine",
                "browser.cdp_url",
                "browser.auto_launch_local_cdp",
                "browser.allow_private_urls",
                "browser.command_timeout",
                "code_execution.mode",
                "code_execution.timeout",
                "code_execution.max_tool_calls",
                "streaming.enabled",
                "streaming.transport",
                "streaming.edit_interval",
                "tts.provider",
                "tts.edge.voice",
                "tts.providers.jarvis.type",
                "tts.providers.jarvis.command",
                "tts.providers.jarvis.output_format",
                "tts.providers.jarvis.timeout",
                "tts.providers.jarvis.voice_compatible",
                "stt.enabled",
                "stt.provider",
                "stt.local.model",
                "stt.local.language",
                "voice.auto_tts",
                "voice.max_recording_seconds",
            },
        )

    def test_overlay_matches_v0206_schema_and_intentional_overrides(self):
        overlay = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = merge_config.load_yaml(overlay)
        # v0.20.6: max_concurrent_children is the single unified cap; the
        # deprecated max_async_children is gone (`hermes config migrate` drops it).
        self.assertEqual(
            data["delegation"],
            {
                "orchestrator_enabled": True,
                "model": "",
                "provider": "",
                "max_concurrent_children": 8,
                "max_spawn_depth": 3,
                "subagent_auto_approve": False,
            },
        )
        self.assertNotIn("max_async_children", data["delegation"])
        self.assertEqual(data["code_execution"]["timeout"], 300)
        self.assertEqual(data["code_execution"]["max_tool_calls"], 50)
        # v0.20.6 code_execution.mode accepts only "project" or "strict".
        self.assertIn(data["code_execution"]["mode"], ("project", "strict"))


if __name__ == "__main__":
    unittest.main()
