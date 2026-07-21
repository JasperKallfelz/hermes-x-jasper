"""Public-snapshot provenance and privacy checks."""
import hashlib
from pathlib import Path
import stat
import sys
import unittest


REPO = Path(__file__).resolve().parents[1]
STACK = REPO / "coder-stack"
sys.path.insert(0, str(REPO / "scripts"))

import audit_public  # noqa: E402


SOURCE_COMMIT = "2a74f958cc1eb226584fdc51dfe72cebfc22ddab"


# Files copied BYTE-FOR-BYTE from coder-stack commit
# 2a74f958cc1eb226584fdc51dfe72cebfc22ddab: the wrappers, the gate policy, the
# plain test-support files, and the two operator docs that needed no public-only
# adaptation. This is NOT every file in the snapshot — the files intentionally
# adapted for public release (PUBLIC_MARKER_ADAPTATIONS and
# PUBLIC_DOC_ADAPTATIONS below) are pinned and proven-reversible separately.
SOURCE_HASHES = {
    ".gitignore": "890a03061bab8dbcd0f8462fa33684371516906db98edd20fe908c582b07a7aa",
    ".hermes-gates.json": "cb179a3ba0f3ff6410926155bf9a04c426d72407e3348010b2b19b1ebc8401aa",
    "bin/hermes-coder": "7b74fde8fe4456fb52b87400b36daab034e1a48d4df5839104f0ccba6903b186",
    "bin/hermes-coder-flow": "f6d1ff65094af32c2c998bf05188e079bf8fa0699f374b468ee29a101dd4a8a5",
    "tests/__init__.py": "3728b1e6b5ce1abbdf80f6cb36a9907474592908b18117f4ef1ab5e0cfb25111",
    "tests/test_security_hardening.py": "a7a0215f8d68ac7deafdb2c9aedb33439e7c57be3903f782b1c3949e220aed11",
    "docs/hermes-coder-flow.md": "b65a0b608aaf08a494cc5fbadfd28eed8d362d6d3d525ad5bc158dca293cea42",
    "docs/reliability-and-gates.md": "c22c43a3d244d7f461c2efb09562c2c9a2318450c5e55fa6308e5762443facbd",
}


# Deliberate, public-only fixture adaptation. Two self-test files carried privacy
# markers of the form `secret = "GATE_RAW_SECRET_..."` that a full-history gitleaks
# scan flags as generic-api-key hits. To keep the release gate green WITHOUT a
# blanket allowlist, those markers are renamed here (SECRET -> MARKER, and the
# `secret*` locals -> `*_marker`). The rename is semantics-preserving (the tests
# still assert the value never leaks into argv/journal/state) and provably narrow:
# reverting the token map below reproduces the authoritative source bytes exactly.
PUBLIC_MARKER_ADAPTATIONS = {
    "tests/test_hermes_coder.py": {
        "source_sha256": "942a65e42b57019f1aefcd0535470790c9539ec52c78a3765fffea977a3a4f0d",
        "public_sha256": "963e8ecff7ebb483618b6e685bc929e3bda7cc527112b9f500a76cc94b9e0c3b",
        # (public token -> authoritative source token)
        "revert": [("raw_marker", "secret"), ("GATE_RAW_MARKER_18a2", "GATE_RAW_SECRET_18a2")],
    },
    "tests/test_hermes_coder_flow.py": {
        "source_sha256": "a21d3a62c6455d819f715df4574204130d9493e521a5a1c60bf0fad3274dfb71",
        "public_sha256": "cc05e1b3aafdc824cbc1940cacf6119ef153bee07667a95e00db3e8e15b55fe8",
        "revert": [("prompt_marker", "secret_prompt"), ("PROMPT_MARKER_c41f9", "PROMPT_SECRET_c41f9")],
    },
}


# Deliberate, public-only documentation adaptation. Unlike the marker rename
# above (a token substitution), these two docs had public-only prose/formatting
# inserted or a portable-path convention substituted. Each entry pins the public
# text and gives an exact, bounded transform (never a broad deletion or a
# source-repo read at test time) that reproduces the authoritative source bytes.
PUBLIC_DOC_ADAPTATIONS = {
    "README.md": {
        "source_sha256": "0b071654d8eee01d5239f2560554d1390c09c484f4f0a530acd913a5968d2442",
        "public_sha256": "a020ce9bafbf8fe893130d5ddfb5ff1069a02d57e8e0f6652d19e5ee9153236e",
    },
    "docs/phase-a-plan.md": {
        "source_sha256": "8592aac555f2fbadaa8cf8861513f99d1b5f350af738da2fa21f5f5024b21632",
        "public_sha256": "8748631f62536ad73ec4a5cab2f4d336aa7a7e6436919839f9be61dafd2d103e",
    },
}

# The public README adds two blocks not present in the source: a pointer to the
# retained historical Phase A note, and a "License and provenance" section
# (plus its lead-in sentence) appended after the existing "## Tests" example.
# Both are pinned verbatim; removing them exactly reproduces the source bytes.
_README_HISTORICAL_NOTE = (
    "The original historical Phase A design note is retained at\n"
    "[`docs/phase-a-plan.md`](docs/phase-a-plan.md). It is background, not the\n"
    "current operating contract; the two documents above describe the implemented\n"
    "behavior.\n\n"
)
_README_TESTS_MARKER = "git diff --check\n```\n"

# The public phase-a-plan.md adds a 3-line public-snapshot header after the
# title, and substitutes the portable `$HOME/...` form for the source's
# `~/...` form throughout. Both are pinned and reversed exactly below.
_PHASE_A_PLAN_HEADER = (
    "> Öffentliche Snapshot-Kopie aus dem unten genannten Quell-Commit; portable\n"
    "> Pfade verwenden `$HOME`. Keine lokale Installation oder Laufzeitdaten wurden übernommen.\n"
    ">\n"
)


class CoderStackSnapshotTest(unittest.TestCase):
    def test_runnable_policy_and_tests_match_the_authoritative_commit(self):
        for relative, expected in SOURCE_HASHES.items():
            with self.subTest(path=relative):
                payload = (STACK / relative).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected)

    def test_public_only_marker_adaptation_is_narrow_and_reversible(self):
        for relative, spec in PUBLIC_MARKER_ADAPTATIONS.items():
            with self.subTest(path=relative):
                text = (STACK / relative).read_text(encoding="utf-8")
                # the shipped public file is exactly what we recorded
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), spec["public_sha256"])
                reverted = text
                for public_token, source_token in spec["revert"]:
                    # the public marker is actually present (adaptation applied)
                    self.assertIn(public_token, text)
                    reverted = reverted.replace(public_token, source_token)
                # reverting the documented map reproduces the authoritative source
                # bytes byte-for-byte: the ONLY divergence is the marker rename.
                self.assertEqual(
                    hashlib.sha256(reverted.encode()).hexdigest(),
                    spec["source_sha256"],
                    f"{relative} diverges from source beyond the documented marker rename",
                )

    def test_public_only_doc_adaptation_is_narrow_and_reversible(self):
        readme = (STACK / "README.md").read_text(encoding="utf-8")
        spec = PUBLIC_DOC_ADAPTATIONS["README.md"]
        self.assertEqual(hashlib.sha256(readme.encode()).hexdigest(), spec["public_sha256"])
        self.assertIn(_README_HISTORICAL_NOTE, readme)
        reverted = readme.replace(_README_HISTORICAL_NOTE, "", 1)
        marker_end = reverted.index(_README_TESTS_MARKER) + len(_README_TESTS_MARKER)
        # everything after the pinned "## Tests" example is the appended
        # License-and-provenance block; the source has nothing past that marker.
        reverted = reverted[:marker_end]
        self.assertEqual(
            hashlib.sha256(reverted.encode()).hexdigest(),
            spec["source_sha256"],
            "README.md diverges from source beyond the documented historical-note "
            "and License-and-provenance insertions",
        )

        plan = (STACK / "docs/phase-a-plan.md").read_text(encoding="utf-8")
        spec = PUBLIC_DOC_ADAPTATIONS["docs/phase-a-plan.md"]
        self.assertEqual(hashlib.sha256(plan.encode()).hexdigest(), spec["public_sha256"])
        self.assertIn(_PHASE_A_PLAN_HEADER, plan)
        reverted = plan.replace(_PHASE_A_PLAN_HEADER, "", 1)
        self.assertIn("$HOME", reverted)
        reverted = reverted.replace("$HOME", "~")
        self.assertEqual(
            hashlib.sha256(reverted.encode()).hexdigest(),
            spec["source_sha256"],
            "docs/phase-a-plan.md diverges from source beyond the documented "
            "header insertion and $HOME/~ substitution",
        )

    def test_wrapper_modes_are_executable(self):
        for name in ("hermes-coder", "hermes-coder-flow"):
            with self.subTest(name=name):
                self.assertEqual(stat.S_IMODE((STACK / "bin" / name).stat().st_mode), 0o755)

    def test_snapshot_has_expected_public_files_and_no_repository_or_runtime_state(self):
        expected_docs = {
            "README.md",
            "docs/hermes-coder-flow.md",
            "docs/phase-a-plan.md",
            "docs/reliability-and-gates.md",
        }
        present = {
            path.relative_to(STACK).as_posix()
            for path in STACK.rglob("*")
            if path.is_file()
        }
        self.assertTrue(expected_docs <= present)
        self.assertFalse(any(path.name == ".git" for path in STACK.rglob("*")))
        forbidden_suffixes = {".jsonl", ".sqlite", ".sqlite3", ".db"}
        self.assertFalse(
            [path for path in STACK.rglob("*") if path.is_file() and path.suffix in forbidden_suffixes]
        )

    def test_snapshot_contains_no_public_audit_findings_or_absolute_home_paths(self):
        findings = []
        for path in STACK.rglob("*"):
            if not path.is_file():
                continue
            text = audit_public.read_text(path)
            if text is None:
                continue
            findings.extend((path.relative_to(STACK), finding) for finding in audit_public.scan_text(text))
            self.assertNotIn("/Users/", text, path)
            self.assertNotIn("/home/", text, path)
        self.assertEqual(findings, [])

    def test_provenance_and_license_are_documented(self):
        stack_readme = (STACK / "README.md").read_text(encoding="utf-8")
        parent_readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn(SOURCE_COMMIT, stack_readme)
        self.assertIn(SOURCE_COMMIT, parent_readme)
        self.assertIn("MIT License", stack_readme)
        self.assertTrue((REPO / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
