"""Static checks for the opt-in messaging/ module.

Stdlib only. These never execute the installers or contact any network / device;
they validate that the shipped templates, scripts, and docs are well-formed and
strictly sanitized for public release.
"""
from __future__ import annotations

import plistlib
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MESSAGING = REPO / "messaging"
LAUNCHD = MESSAGING / "launchd"

# The full set of placeholders the launchd templates must expose between them.
REQUIRED_PLACEHOLDERS = {
    "__HOME__",
    "__NODE__",
    "__SIGNAL_CLI__",
    "__SIGNAL_E164_ACCOUNT__",
    "__BRIDGE_DIR__",
}
# Which placeholders each template must carry.
PER_TEMPLATE_PLACEHOLDERS = {
    "com.example.hermes-messaging.whatsapp.plist.template": {
        "__HOME__", "__NODE__", "__BRIDGE_DIR__",
    },
    "com.example.hermes-messaging.signal.plist.template": {
        "__HOME__", "__SIGNAL_CLI__", "__SIGNAL_E164_ACCOUNT__",
    },
}

LABEL_PREFIX = "com.example.hermes-messaging."


def _templates() -> list[Path]:
    return sorted(LAUNCHD.glob("*.plist.template"))


def _scripts() -> list[Path]:
    return sorted(MESSAGING.glob("*.sh"))


def _text_files() -> list[Path]:
    return sorted(p for p in MESSAGING.rglob("*") if p.is_file())


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_module_layout_exists():
    assert MESSAGING.is_dir(), "messaging/ directory is missing"
    assert (MESSAGING / "setup_whatsapp.sh").is_file()
    assert (MESSAGING / "setup_signal.sh").is_file()
    assert (MESSAGING / "README.md").is_file()
    assert len(_templates()) == 2, "expected exactly two launchd templates"


def test_plist_templates_are_well_formed_and_configured():
    seen_placeholders: set[str] = set()
    for template in _templates():
        raw = _read(template)
        # plistlib.loads proves the file is well-formed XML *and* a valid plist;
        # the __PLACEHOLDER__ tokens are ordinary <string> values.
        parsed = plistlib.loads(raw.encode("utf-8"))

        label = parsed.get("Label", "")
        assert label.startswith(LABEL_PREFIX), f"{template.name}: bad Label {label!r}"
        assert parsed.get("RunAtLoad") is True, f"{template.name}: RunAtLoad must be true"
        assert parsed.get("KeepAlive") is True, f"{template.name}: KeepAlive must be true"
        assert parsed.get("Umask") == 63, f"{template.name}: Umask must be 63 (0o077)"
        args = parsed.get("ProgramArguments")
        assert isinstance(args, list) and args, f"{template.name}: ProgramArguments missing"

        # Logs must land under the documented second-brain logs directory.
        assert "__HOME__/.hermes/second-brain/logs/" in parsed.get("StandardOutPath", "")
        assert "__HOME__/.hermes/second-brain/logs/" in parsed.get("StandardErrorPath", "")

        required = PER_TEMPLATE_PLACEHOLDERS[template.name]
        missing = {p for p in required if p not in raw}
        assert not missing, f"{template.name}: missing placeholders {missing}"
        seen_placeholders.update(p for p in REQUIRED_PLACEHOLDERS if p in raw)

    assert seen_placeholders == REQUIRED_PLACEHOLDERS, (
        f"templates do not cover all placeholders: "
        f"missing {REQUIRED_PLACEHOLDERS - seen_placeholders}"
    )


def test_whatsapp_template_runs_self_chat_on_3000():
    raw = _read(LAUNCHD / "com.example.hermes-messaging.whatsapp.plist.template")
    parsed = plistlib.loads(raw.encode("utf-8"))
    args = parsed["ProgramArguments"]
    assert "--mode" in args and "self-chat" in args
    assert "3000" in args
    assert "__HOME__/.hermes/platforms/whatsapp/session" in args


def test_signal_template_runs_http_daemon_on_8080():
    raw = _read(LAUNCHD / "com.example.hermes-messaging.signal.plist.template")
    parsed = plistlib.loads(raw.encode("utf-8"))
    args = parsed["ProgramArguments"]
    assert "daemon" in args
    assert "--http" in args
    assert "127.0.0.1:8080" in args
    assert "__SIGNAL_E164_ACCOUNT__" in args


def test_scripts_pass_bash_syntax_check():
    for script in _scripts():
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"bash -n {script.name} failed:\n{result.stderr}"


def test_scripts_use_strict_mode_and_bash_shebang():
    assert _scripts(), "no messaging/*.sh scripts found"
    for script in _scripts():
        text = _read(script)
        assert text.startswith("#!/usr/bin/env bash"), f"{script.name}: missing bash shebang"
        assert "set -euo pipefail" in text, f"{script.name}: missing 'set -euo pipefail'"


def test_readme_mentions_both_platforms():
    readme = _read(MESSAGING / "README.md").lower()
    assert "whatsapp" in readme, "README must mention WhatsApp"
    assert "signal" in readme, "README must mention Signal"
    # The honest limitations section must acknowledge the calls gap.
    assert "no real voice" in readme or "voice or video calls" in readme


# --- strict sanitization: nothing personal may ship in messaging/ -----------

# E.164-style number (`+` then 7-15 digits) or any bare run of 10+ digits.
PHONE_RE = re.compile(r"\+\d{7,15}\b|(?<!\d)\d{10,}(?!\d)")
# Absolute personal home paths.
ABS_HOME_RE = re.compile(r"/Users/[A-Za-z0-9]|/home/[A-Za-z0-9]")
# The maintainer's local username must never appear (built by concatenation so
# this test file itself carries no contiguous copy of it).
FORBIDDEN_OWNER = ("jasper" "kallflez").lower()


def test_no_real_phone_numbers_in_messaging():
    offenders = []
    for path in _text_files():
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if PHONE_RE.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "possible real phone number(s):\n" + "\n".join(offenders)


def test_no_absolute_user_paths_in_messaging():
    offenders = []
    for path in _text_files():
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if ABS_HOME_RE.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "absolute home path(s) — use $HOME / __HOME__:\n" + "\n".join(offenders)


def test_no_maintainer_username_in_messaging():
    offenders = [
        str(path.relative_to(REPO))
        for path in _text_files()
        if FORBIDDEN_OWNER in _read(path).lower()
    ]
    assert not offenders, f"maintainer username leaked in: {offenders}"
