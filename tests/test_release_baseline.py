"""Cross-file release, patch, config, and disclosure contracts for v0.3.0."""

from pathlib import Path
import re


REPO = Path(__file__).resolve().parents[1]
PIN = "5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
TAG = "v2026.8.27"
UPSTREAM_VERSION = "v0.20.6"
STARTER_VERSION = "v0.3.0"

PIN_SOURCES = (
    "setup.sh",
    "verify.sh",
    ".github/workflows/ci.yml",
    "AGENTS.md",
    "README.md",
    "SECURITY.md",
    "docs/TROUBLESHOOTING.md",
    "CHANGELOG.md",
    "tests/test_setup.py",
)

PATCH_PATHS = {
    "hermes_cli/browser_connect.py",
    "hermes_cli/config_defaults.py",
    "plugins/platforms/telegram/adapter.py",
    "tests/cli/test_cli_browser_connect.py",
    "tools/browser_tool.py",
    "tools/tts_tool.py",
    "tests/gateway/test_telegram_location_keyboard_cleanup.py",
    "tests/hermes_cli/test_browser_connect_loopback_binding.py",
    "tests/tools/test_browser_auto_cdp.py",
    "tests/tools/test_tts_runtime_overrides.py",
}


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_exact_release_pin_is_consistent_across_every_source_of_truth():
    for relative in PIN_SOURCES:
        assert PIN in _read(relative), f"{relative} is missing the exact upstream pin"

    for relative in (
        "setup.sh",
        "verify.sh",
        ".github/workflows/ci.yml",
        "AGENTS.md",
        "README.md",
        "SECURITY.md",
        "docs/TROUBLESHOOTING.md",
        "CHANGELOG.md",
    ):
        text = _read(relative)
        assert TAG in text, f"{relative} is missing {TAG}"

    for relative in (
        "setup.sh",
        "AGENTS.md",
        "README.md",
        "SECURITY.md",
        "docs/TROUBLESHOOTING.md",
        "CHANGELOG.md",
    ):
        assert UPSTREAM_VERSION in _read(relative), f"{relative} is missing {UPSTREAM_VERSION}"

    assert STARTER_VERSION in _read("README.md")
    assert STARTER_VERSION in _read("CHANGELOG.md")


def test_retired_pin_and_tag_are_absent():
    retired = (
        "3ef6bbd201263d354fd83" + "ec55b3c306ded2eb72a",
        "v2026" + ".7.20",
    )
    offenders = []
    for path in REPO.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name in {".hermes-task.md", ".hermes-result.md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for value in retired:
            if value in text:
                offenders.append((path.relative_to(REPO).as_posix(), value))
    assert offenders == []


def test_patch_is_full_index_plain_diff_with_only_expected_paths():
    patch = _read("patches/voice-and-desktop-features.patch")
    paths = set(
        re.findall(r"^diff --git a/(.+) b/\1$", patch, flags=re.MULTILINE)
    )
    assert paths == PATCH_PATHS
    index_lines = re.findall(
        r"^index ([0-9a-f]{40})\.\.([0-9a-f]{40})(?: \d+)?$",
        patch,
        flags=re.MULTILINE,
    )
    assert len(index_lines) == len(PATCH_PATHS)
    assert "--3way" not in _read("setup.sh")
    assert "apply --check --whitespace=error-all" in _read("verify.sh")
    assert "apply --check --whitespace=error-all" in _read(
        ".github/workflows/ci.yml"
    )


def test_patch_features_and_upstream_boundaries_are_explicit():
    patch = _read("patches/voice-and-desktop-features.patch")
    for symbol in (
        "auto_launch_local_cdp",
        "--remote-debugging-address=127.0.0.1",
        "_apply_runtime_tts_overrides",
        "voice_override",
        "model_override",
        "_remove_location_request_keyboard",
        "ReplyKeyboardRemove",
    ):
        assert symbol in patch

    docs = _read("README.md") + _read("docs/FEATURES.md") + _read("AGENTS.md")
    assert "model-facing" in docs
    assert "provider" in docs and "speed" in docs
    assert "v0.20.6 already" in docs


def test_public_scope_and_opt_in_disclosures_remain_prominent():
    disclosures = _read("README.md") + _read("CHANGELOG.md") + _read("AGENTS.md")
    assert "unofficial community starter" in disclosures.lower()
    assert "does not reproduce the maintainer's private" in disclosures
    assert "nothing runs unless" in disclosures
    assert "Experimental opt-in Pi runtime" in disclosures
    assert "separate checkout" in disclosures
    assert "authenticated model E2E" in disclosures
    assert "coder-stack/" in disclosures and "unchanged" in disclosures
