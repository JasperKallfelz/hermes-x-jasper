#!/usr/bin/env python3
"""Scan tracked repository content for secrets, PII and local paths.

By default, a git checkout scans tracked files only. Outside git, it falls back
to walking the tree (skipping .git and other noise). It matches every text line
against a set of leak patterns, and prints ``path:line: [rule] message`` for
each hit. Messages intentionally do not echo the matched value.
Exits non-zero when anything is found, so it can gate CI and `make check`.

Placeholders are expected in a starter repo: a line is only reported when it
looks like a *real* value. Lines carrying the ``audit:allow`` marker are skipped
(same idea as ``# noqa``) — that is how this file declares its own patterns
without matching itself.

Extra project-specific strings can be supplied via PUBLIC_AUDIT_DENYLIST, which
is either a path to a newline-separated file or a comma-separated list:

    PUBLIC_AUDIT_DENYLIST="internalcodename,my-vpn.example" ./scripts/audit_public.py
    PUBLIC_AUDIT_DENYLIST=~/.hermes-denylist.txt ./scripts/audit_public.py

Usage: audit_public.py [ROOT] [--quiet] [--history] [--all-files]
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

ALLOW_MARKER = "audit:allow"
# A file containing this marker is skipped entirely — for test fixtures, which
# must contain realistic-looking secrets in order to prove the scanner works.
ALLOW_FILE_MARKER = "audit:allow-file"

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".idea",
    ".tox", ".eggs", "site-packages",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".whl", ".so", ".dylib", ".dll", ".mp3", ".mp4", ".ogg", ".wav",
    ".woff", ".woff2", ".ttf", ".pyc", ".onnx", ".bin", ".pt", ".safetensors",
}

# Tokens that mark a line as an intentional placeholder rather than a real leak.
PLACEHOLDER_HINTS = (
    "your-", "your_", "yourname", "youruser", "placeholder", "changeme",
    "change-me", "example", "<", "xxx", "...", "dummy", "fake", "redacted",
    "n/a", "todo", "insert-", "put-your", "abc123", "0000",
)

# Domains that are reserved for documentation and safe to publish (RFC 2606).
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.edu",
                      "localhost", "invalid", "test", "domain.com", "email.com")

# Generic usernames that are obviously not a real person's account name.
SAFE_USERNAMES = {
    "you", "user", "username", "youruser", "yourname", "me", "name", "someone",
    "runner", "root", "ubuntu", "admin", "test", "example", "foo", "bar",
    "<user>", "<name>", "your-user", "your_user", "hermes",
}


class Rule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    message: str


def _rules() -> list[Rule]:
    r = re.compile
    return [
        Rule("private-key",
             r(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
             "private key block"),
        Rule("openai-key", r(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),  # audit:allow
             "OpenAI/Anthropic-style API key"),
        Rule("github-token", r(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # audit:allow
             "GitHub token"),
        Rule("slack-token", r(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # audit:allow
             "Slack token"),
        Rule("google-key", r(r"\bAIza[A-Za-z0-9_-]{30,}"),  # audit:allow
             "Google API key"),
        Rule("aws-key", r(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),  # audit:allow
             "AWS access key id"),
        Rule("hf-token", r(r"\bhf_[A-Za-z0-9]{20,}"),  # audit:allow
             "Hugging Face token"),
        Rule("telegram-bot-token", r(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}"),  # audit:allow
             "Telegram bot token"),
        Rule("discord-bot-token",
             r(r"\b[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}"),  # audit:allow
             "Discord bot token"),
        Rule("authorization-header",
             r(r"(?i)authorization[\"']?\s*[:=]\s*[\"']?\s*(?:bearer|basic|token)\s+\S+"),  # audit:allow
             "hardcoded Authorization header"),
        Rule("macos-home", r(r"/Users/[A-Za-z0-9](?:[A-Za-z0-9_.-]*)"),  # audit:allow
             "absolute macOS home path"),
        Rule("linux-home", r(r"/home/[A-Za-z0-9](?:[A-Za-z0-9_.-]*)"),  # audit:allow
             "absolute Linux home path"),
        Rule("email", r(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # audit:allow
             "email address"),
        Rule("discord-snowflake", r(r"(?<![\d.\w-])\d{17,20}(?![\d.\w-])"),  # audit:allow
             "Discord snowflake ID"),
        Rule("telegram-group-id", r(r"(?<![\d\w-])-100\d{9,}(?![\d\w])"),  # audit:allow
             "Telegram supergroup ID"),
    ]


RULES = _rules()


def load_denylist() -> list[str]:
    """Read PUBLIC_AUDIT_DENYLIST — either a file path or a comma-separated list."""
    raw = os.environ.get("PUBLIC_AUDIT_DENYLIST", "").strip()
    if not raw:
        return []
    candidate = Path(raw).expanduser()
    try:
        if candidate.is_file():
            entries = candidate.read_text(encoding="utf-8").splitlines()
        else:
            entries = raw.split(",")
    except OSError:
        entries = raw.split(",")
    return [e.strip().lower() for e in entries if e.strip() and not e.strip().startswith("#")]


def _looks_like_placeholder(line: str) -> bool:
    low = line.lower()
    return any(hint in low for hint in PLACEHOLDER_HINTS)


def _is_empty_assignment(line: str) -> bool:
    """True for `KEY=` / `key: ""` style lines — declared but unset."""
    return bool(re.match(r"^\s*[#\w.\"'-]+\s*[:=]\s*(?:\"\"|''|)\s*(?:#.*)?$", line))


def _accept(rule: Rule, match: str, line: str, denylist: list[str]) -> bool:
    """Decide whether a raw regex hit is a genuine finding."""
    if rule.name == "email":
        domain = match.rsplit("@", 1)[-1].lower()
        if any(domain == d or domain.endswith("." + d) for d in SAFE_EMAIL_DOMAINS):
            return False
        return True
    if rule.name in ("macos-home", "linux-home"):
        user = match.rstrip("/").split("/")[2].lower() if match.count("/") >= 2 else ""
        return user not in SAFE_USERNAMES
    if rule.name in ("discord-snowflake", "telegram-group-id"):
        # Repeated digits (000000000000000000, 1111...) are obvious fixtures.
        digits = match.lstrip("-")
        return len(set(digits)) > 2
    if rule.name == "authorization-header":
        return not _looks_like_placeholder(line)
    # Key-shaped rules: a placeholder-looking line is fine.
    return not _looks_like_placeholder(line)


def scan_text(text: str, denylist: Iterable[str] = ()) -> list[tuple[int, str, str]]:
    """Return (line_number, rule_name, message) for every finding in *text*."""
    deny = [d.lower() for d in denylist]
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        if _is_empty_assignment(line):
            continue
        for rule in RULES:
            for match in rule.pattern.findall(line):
                found = match if isinstance(match, str) else match[0]
                if _accept(rule, found, line, deny):
                    findings.append((lineno, rule.name, rule.message))
                    break
        low = line.lower()
        for needle in deny:
            if needle in low:
                findings.append((lineno, "denylist", "denylisted string"))
                break
    return findings


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _is_git_worktree(root: Path) -> bool:
    proc = _git(root, ["rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def iter_files(root: Path, tracked_only: bool = True) -> Iterable[Path]:
    if tracked_only and _is_git_worktree(root):
        proc = _git(root, ["ls-files", "-z"])
        if proc.returncode == 0:
            for raw in proc.stdout.split("\0"):
                if raw:
                    path = root / raw
                    if path.suffix.lower() not in SKIP_SUFFIXES:
                        yield path
            return

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            # A linked worktree stores its Git metadata in a `.git` file whose
            # absolute path is local machine state, never publishable content.
            if name == ".git":
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            yield path


def read_text(path: Path) -> str | None:
    """Return the file's text, or None when it is binary/unreadable."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_history(root: Path, denylist: Iterable[str]) -> int:
    """Scan reachable git blobs without printing matched values."""
    if not _is_git_worktree(root):
        return 0
    revs = _git(root, ["rev-list", "--all"])
    if revs.returncode != 0 or not revs.stdout.strip():
        return 0

    total = 0
    listed = _git(root, ["rev-list", "--objects", "--all"])
    if listed.returncode != 0:
        print("history:0: [git] could not enumerate history")
        return 1

    seen: set[str] = set()
    for line in listed.stdout.splitlines():
        if not line:
            continue
        blob, _, name = line.partition(" ")
        if blob in seen or not name or Path(name).suffix.lower() in SKIP_SUFFIXES:
            continue
        seen.add(blob)
        cat = _git(root, ["cat-file", "-p", blob])
        if cat.returncode != 0 or "\x00" in cat.stdout:
            continue
        if ALLOW_FILE_MARKER in cat.stdout:
            continue
        for lineno, rule, message in scan_text(cat.stdout, denylist):
            print(f"history:{name}:{lineno}: [{rule}] {message}")
            total += 1
    return total


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    quiet = "--quiet" in argv[1:]
    history = "--history" in argv[1:]
    tracked_only = "--all-files" not in argv[1:]
    root = Path(args[0]).expanduser().resolve() if args else Path.cwd()
    denylist = load_denylist()

    total = 0
    for path in iter_files(root, tracked_only=tracked_only):
        text = read_text(path)
        if text is None or ALLOW_FILE_MARKER in text:
            continue
        for lineno, rule, message in scan_text(text, denylist):
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"{rel}:{lineno}: [{rule}] {message}")
            total += 1
    if history:
        total += scan_history(root, denylist)

    if total:
        print(f"\n{total} potential leak(s) found — do not publish until resolved.",
              file=sys.stderr)
        print("False positive? Add an 'audit:allow' marker to the line.", file=sys.stderr)
        return 1
    if not quiet:
        print(f"audit_public: clean ({root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
