"""Exact vendored Coder Stack provenance and privacy checks."""

import hashlib
import json
from pathlib import Path
import stat
import sys
import unittest


REPO = Path(__file__).resolve().parents[1]
STACK = REPO / "coder-stack"
MANIFEST_PATH = REPO / "release" / "coder-stack-manifest.json"
sys.path.insert(0, str(REPO / "scripts"))

import audit_public  # noqa: E402


class CoderStackSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_snapshot_is_exactly_the_manifested_source_tree(self):
        expected = self.manifest["files"]
        present = {
            path.relative_to(STACK).as_posix()
            for path in STACK.rglob("*")
            if path.is_file()
        }
        self.assertEqual(present, set(expected))
        for relative, spec in sorted(expected.items()):
            with self.subTest(path=relative):
                path = STACK / relative
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    spec["sha256"],
                )
                expected_mode = 0o755 if spec["mode"] == "100755" else 0o644
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected_mode)

    def test_snapshot_contains_no_repository_or_runtime_state(self):
        self.assertFalse(any(path.name == ".git" for path in STACK.rglob("*")))
        forbidden_suffixes = {".jsonl", ".sqlite", ".sqlite3", ".db"}
        self.assertFalse(
            [
                path
                for path in STACK.rglob("*")
                if path.is_file() and path.suffix in forbidden_suffixes
            ]
        )

    def test_snapshot_contains_no_public_audit_findings_or_personal_homes(self):
        findings = []
        for path in STACK.rglob("*"):
            if not path.is_file():
                continue
            text = audit_public.read_text(path)
            if text is None:
                continue
            findings.extend(
                (path.relative_to(STACK), finding)
                for finding in audit_public.scan_text(text)
            )
            relative = path.relative_to(STACK)
            user_home_lines = [line for line in text.splitlines() if "/Users/" in line]
            if user_home_lines:
                self.assertEqual(
                    relative.as_posix(),
                    "tools/deep-chat/tests/test_deep_chat.sh",
                )
                self.assertTrue(
                    all('"/Users/"' in line for line in user_home_lines),
                    user_home_lines,
                )
            self.assertNotIn("/home/", text, path)
        self.assertEqual(findings, [])

    def test_provenance_license_and_entrypoints_are_documented(self):
        self.assertEqual(
            self.manifest["source_repository"],
            "https://github.com/JasperKallfelz/hermes-coder-stack",
        )
        self.assertEqual(
            self.manifest["source_commit"],
            "c7fe0ad0d15b26e08635dcac6dfa446a61f0c4fc",
        )
        self.assertEqual(self.manifest["license"], "MIT")
        parent_readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn(self.manifest["source_commit"], parent_readme)
        self.assertIn(self.manifest["source_repository"], parent_readme)
        self.assertIn("MIT License", parent_readme)
        self.assertTrue((REPO / "LICENSE").is_file())
        for relative in (
            "bin/hermes-coder",
            "bin/hermes-coder-flow",
            "bin/hermes-deep-work",
            "tools/deep-chat/hermes-deep-chat",
            "tools/deep-chat/install-local.sh",
        ):
            with self.subTest(path=relative):
                self.assertEqual(self.manifest["files"][relative]["mode"], "100755")


if __name__ == "__main__":
    unittest.main()
