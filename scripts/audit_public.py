#!/usr/bin/env python3
"""Fail-closed scanner for publishable content and complete Git history.

Current false positives require a narrow trailing ``# audit:allow`` (or ``//``)
comment plus an exact path/rule/line fingerprint in the reviewed exception
manifest. Historical exceptions are immutable commit/path/rule/line hashes.
Commit and annotated-tag identities/messages are scanned as well as file data.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable, NamedTuple

ALLOW_MARKER = "audit:allow"
ALLOW_FILE_MARKER = "audit:allow-file"
MARKER_RE = re.compile(r"\s+(?:#|//)\s*audit:allow\s*$")
DEFAULT_EXCEPTION_PATH = Path("security/audit-exceptions.json")

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
PLACEHOLDER_HINTS = (
    "your-", "your_", "yourname", "youruser", "placeholder", "changeme",
    "change-me", "example", "<", "xxx", "...", "dummy", "fake", "redacted",
    "n/a", "todo", "insert-", "put-your", "abc123", "0000",
)
SAFE_EMAIL_DOMAINS = (
    "example.com", "example.org", "example.net", "example.edu", "localhost",
    "invalid", "test", "domain.com", "email.com", "users.noreply.github.com",
)
SAFE_EMAIL_ADDRESSES = {"noreply@github.com"}
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
        Rule("private-key", r(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "private key block"),
        Rule("openai-key", r(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"), "OpenAI/Anthropic-style API key"),
        Rule("github-token", r(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
        Rule("slack-token", r(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
        Rule("google-key", r(r"\bAIza[A-Za-z0-9_-]{30,}"), "Google API key"),
        Rule("aws-key", r(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "AWS access key id"),
        Rule("hf-token", r(r"\bhf_[A-Za-z0-9]{20,}"), "Hugging Face token"),
        Rule("telegram-bot-token", r(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}"), "Telegram bot token"),
        Rule("discord-bot-token", r(r"\b[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}"), "Discord bot token"),
        Rule("authorization-header", r(r"(?i)authorization[\"']?\s*[:=]\s*[\"']?\s*(?:bearer|basic|token)\s+\S+"), "hardcoded Authorization header"),
        Rule("macos-home", r(r"/Users/[A-Za-z0-9](?:[A-Za-z0-9_.-]*)"), "absolute macOS home path"),
        Rule("linux-home", r(r"/home/[A-Za-z0-9](?:[A-Za-z0-9_.-]*)"), "absolute Linux home path"),
        Rule("email", r(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "email address"),
        Rule("discord-snowflake", r(r"(?<![\d.\w-])\d{17,20}(?![\d.\w-])"), "Discord snowflake ID"),
        Rule("telegram-group-id", r(r"(?<![\d\w-])-100\d{9,}(?![\d\w])"), "Telegram supergroup ID"),
    ]


RULES = _rules()


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()


def load_denylist() -> list[str]:
    raw = os.environ.get("PUBLIC_AUDIT_DENYLIST", "").strip()
    if not raw:
        return []
    candidate = Path(raw).expanduser()
    try:
        entries = candidate.read_text(encoding="utf-8").splitlines() if candidate.is_file() else raw.split(",")
    except OSError:
        entries = raw.split(",")
    return [entry.strip().lower() for entry in entries if entry.strip() and not entry.strip().startswith("#")]


def _looks_like_placeholder(line: str) -> bool:
    low = line.lower()
    return any(hint in low for hint in PLACEHOLDER_HINTS)


def _is_empty_assignment(line: str) -> bool:
    return bool(re.match(r"^\s*[#\w.\"'-]+\s*[:=]\s*(?:\"\"|''|)\s*(?:#.*)?$", line))


def _accept(rule: Rule, match: str, line: str) -> bool:
    if rule.name == "email":
        # Escaped source text for a pytest decorator can lexically resemble an
        # email (``\\n@pytest.mark.skip``). This exemption is deliberately tied
        # to that exact escaped decorator form; ordinary matching addresses
        # remain findings.
        pytest_decorator = "n" + "@pytest.mark."
        if match.startswith(pytest_decorator) and ("\\" + pytest_decorator) in line:
            return False
        if match.lower() in SAFE_EMAIL_ADDRESSES:
            return False
        domain = match.rsplit("@", 1)[-1].lower()
        return not any(domain == safe or domain.endswith("." + safe) for safe in SAFE_EMAIL_DOMAINS)
    if rule.name in ("macos-home", "linux-home"):
        # The reviewed Pi container uses a fixed non-host home. Accept only its
        # hardened Docker tmpfs/env argument forms, never an arbitrary path
        # under that username that could contain host data.
        pi_home = "/" + "home" + "/" + "pi"
        if match == pi_home and (
            ("--tmpfs" in line and pi_home + ":rw,nosuid,nodev,size=" in line)
            or ("--env" in line and "HOME=" + pi_home in line)
        ):
            return False
        user = match.rstrip("/").split("/")[2].lower() if match.count("/") >= 2 else ""
        return user not in SAFE_USERNAMES
    if rule.name in ("discord-snowflake", "telegram-group-id"):
        return len(set(match.lstrip("-"))) > 2
    if rule.name == "authorization-header":
        return not _looks_like_placeholder(line)
    return not _looks_like_placeholder(line)


def scan_text(text: str, denylist: Iterable[str] = ()) -> list[tuple[int, str, str]]:
    """Return findings without applying any inline suppression."""
    deny = [item.lower() for item in denylist]
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _is_empty_assignment(line):
            continue
        for rule in RULES:
            for match in rule.pattern.findall(line):
                found = match if isinstance(match, str) else match[0]
                if _accept(rule, found, line):
                    findings.append((lineno, rule.name, rule.message))
                    break
        low = line.lower()
        if any(needle in low for needle in deny):
            findings.append((lineno, "denylist", "denylisted string"))
    return findings


def _git(root: Path, args: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=not binary,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
    )


def _is_git_worktree(root: Path) -> bool:
    proc = _git(root, ["rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def iter_files(root: Path, tracked_only: bool = True) -> Iterable[Path]:
    if root.is_file() or root.is_symlink():
        if root.suffix.lower() not in SKIP_SUFFIXES:
            yield root
        return
    if tracked_only and _is_git_worktree(root):
        proc = _git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
        if proc.returncode == 0:
            for raw in proc.stdout.split("\0"):
                if raw and Path(raw).suffix.lower() not in SKIP_SUFFIXES:
                    yield root / raw
            return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            if name == ".git":
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() not in SKIP_SUFFIXES:
                yield path


def read_text(path: Path) -> str | None:
    try:
        if path.is_symlink():
            raw = os.fsencode(os.readlink(path))
        else:
            raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def allow_file_marker_applies(path: Path, root: Path, text: str) -> bool:
    del path, root, text
    return False


def _marker_stripped_line(line: str) -> str | None:
    match = MARKER_RE.search(line)
    return line[:match.start()].rstrip() if match else None


class Exceptions:
    def __init__(self, path: Path | None):
        self.current: set[tuple[str, str, str]] = set()
        self.history: set[tuple[str, str, str, str]] = set()
        self.metadata: set[tuple[str, str, str, str]] = set()
        self.used_current: set[tuple[str, str, str]] = set()
        self.used_history: set[tuple[str, str, str, str]] = set()
        self.used_metadata: set[tuple[str, str, str, str]] = set()
        if path is None or not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or set(data) != {
            "schema", "current_line_exceptions", "history_line_exceptions", "metadata_exceptions"
        }:
            raise ValueError("exception manifest has an invalid schema")
        rule_names = {rule.name for rule in RULES} | {"denylist"}

        def valid_path(value: object) -> bool:
            if not isinstance(value, str) or not value:
                return False
            pure = PurePosixPath(value)
            return not pure.is_absolute() and ".." not in pure.parts and "\\" not in value

        for item in data["current_line_exceptions"]:
            if (
                not isinstance(item, dict)
                or not valid_path(item.get("path"))
                or item.get("rule") not in rule_names
            ):
                raise ValueError("invalid current line exception")
            self.current.add((item["path"], item["rule"], item["line_sha256"]))
        for item in data["history_line_exceptions"]:
            if (
                not isinstance(item, dict)
                or not re.fullmatch(r"[0-9a-f]{40}", str(item.get("commit", "")))
                or not valid_path(item.get("path"))
                or item.get("rule") not in rule_names
                or not isinstance(item.get("reason"), str)
                or not item["reason"].strip()
            ):
                raise ValueError("invalid historical line exception")
            self.history.add((item["commit"], item["path"], item["rule"], item["line_sha256"]))
        for item in data["metadata_exceptions"]:
            if (
                not isinstance(item, dict)
                or not re.fullmatch(r"[0-9a-f]{40}", str(item.get("commit", "")))
                or not re.fullmatch(
                    r"(?:author|committer|tagger)\.(?:name|email)|message\.line-[1-9]\d*",
                    str(item.get("field", "")),
                )
                or item.get("rule") not in rule_names
                or not isinstance(item.get("reason"), str)
                or not item["reason"].strip()
            ):
                raise ValueError("invalid metadata exception")
            self.metadata.add((item["commit"], item["field"], item["rule"], item["value_sha256"]))
        expected = sum(len(data[key]) for key in data if key != "schema")
        if expected != len(self.current) + len(self.history) + len(self.metadata):
            raise ValueError("exception manifest contains duplicate entries")
        for value in [entry[-1] for entry in self.current | self.history | self.metadata]:
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("exception manifest contains an invalid fingerprint")

    def current_allows(self, path: str, rule: str, line: str) -> bool:
        key = (path, rule, fingerprint(line))
        if key in self.current:
            self.used_current.add(key)
            return True
        return False

    def history_allows(self, commit: str, path: str, rule: str, line: str) -> bool:
        key = (commit, path, rule, fingerprint(line))
        if key in self.history:
            self.used_history.add(key)
            return True
        return False

    def metadata_allows(self, commit: str, field: str, rule: str, value: str) -> bool:
        key = (commit, field, rule, fingerprint(value))
        if key in self.metadata:
            self.used_metadata.add(key)
            return True
        return False

    def unused(self, history: bool) -> list[str]:
        missing = [f"current:{path}:{rule}:{digest}" for path, rule, digest in self.current - self.used_current]
        if history:
            missing += [
                f"history:{commit}:{path}:{rule}:{digest}"
                for commit, path, rule, digest in self.history - self.used_history
            ]
            missing += [
                f"metadata:{commit}:{field}:{rule}:{digest}"
                for commit, field, rule, digest in self.metadata - self.used_metadata
            ]
        return sorted(missing)


def scan_current(root: Path, denylist: list[str], exceptions: Exceptions, tracked_only: bool) -> int:
    total = 0
    for path in iter_files(root, tracked_only=tracked_only):
        text = read_text(path)
        if text is None:
            continue
        relative = (
            path.name if path == root
            else path.relative_to(root).as_posix() if path.is_relative_to(root)
            else str(path)
        )
        lines = text.splitlines()
        findings = scan_text(text, denylist)
        by_line: dict[int, list[tuple[str, str]]] = {}
        for lineno, rule, message in findings:
            by_line.setdefault(lineno, []).append((rule, message))
        for lineno, line in enumerate(lines, start=1):
            stripped = _marker_stripped_line(line)
            for rule, message in by_line.get(lineno, []):
                if stripped is not None and exceptions.current_allows(relative, rule, line):
                    continue
                print(f"{relative}:{lineno}: [{rule}] {message}")
                total += 1
            if stripped is not None and not by_line.get(lineno):
                print(f"{relative}:{lineno}: [audit-allow] marker suppresses no finding")
                total += 1
    return total


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _metadata_fields(raw: bytes, object_type: str) -> dict[str, str]:
    headers, _, message = raw.partition(b"\n\n")
    fields: dict[str, str] = {"message": _decode(message)}
    wanted = (b"author ", b"committer ") if object_type == "commit" else (b"tagger ",)
    for line in headers.splitlines():
        for prefix in wanted:
            if not line.startswith(prefix):
                continue
            identity = _decode(line[len(prefix):])
            match = re.match(r"^(.*) <([^>]*)> \d+ [+-]\d{4}$", identity)
            if match:
                label = prefix.decode().strip()
                fields[f"{label}.name"] = match.group(1)
                fields[f"{label}.email"] = match.group(2)
    return fields


def _scan_metadata(
    object_id: str,
    object_type: str,
    raw: bytes,
    denylist: list[str],
    exceptions: Exceptions,
) -> int:
    total = 0
    for field, value in _metadata_fields(raw, object_type).items():
        for lineno, rule, message in scan_text(value, denylist):
            field_name = field if field != "message" else f"message.line-{lineno}"
            if exceptions.metadata_allows(object_id, field_name, rule, value):
                continue
            print(f"history-metadata:{object_id}:{field_name}: [{rule}] {message}")
            total += 1
    return total


def scan_history(root: Path, denylist: list[str], exceptions: Exceptions) -> int:
    """Scan every reachable content version and commit/annotated-tag metadata."""
    if not _is_git_worktree(root):
        print("history:0: [git] --history requires a Git working tree")
        return 1
    revs = _git(root, ["rev-list", "--reverse", "--topo-order", "--all"])
    if revs.returncode != 0 or not revs.stdout.strip():
        print("history:0: [git] could not enumerate any reachable commits")
        return 1
    commits = revs.stdout.splitlines()
    head_proc = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head_proc.returncode:
        print("history:0: [git] could not resolve HEAD for current-exception boundary")
        return 1
    head_commit = head_proc.stdout.strip()
    total = 0
    seen_versions: set[tuple[str, str]] = set()
    for commit in commits:
        raw_commit = _git(root, ["cat-file", "commit", commit], binary=True)
        if raw_commit.returncode:
            print(f"history:{commit}: [git] could not read commit metadata")
            total += 1
            continue
        total += _scan_metadata(commit, "commit", raw_commit.stdout, denylist, exceptions)
        tree = _git(root, ["ls-tree", "-r", "-z", "--full-tree", commit], binary=True)
        if tree.returncode:
            print(f"history:{commit}: [git] could not enumerate commit tree")
            total += 1
            continue
        for record in tree.stdout.split(b"\0"):
            if not record:
                continue
            metadata, separator, raw_name = record.partition(b"\t")
            parts = metadata.split()
            if not separator or len(parts) != 3 or parts[1] != b"blob":
                continue
            oid = _decode(parts[2])
            name = _decode(raw_name)
            if Path(name).suffix.lower() in SKIP_SUFFIXES or (name, oid) in seen_versions:
                continue
            seen_versions.add((name, oid))
            blob = _git(root, ["cat-file", "blob", oid], binary=True)
            if blob.returncode:
                print(f"history:{commit}:{name}: [git] could not read blob")
                total += 1
                continue
            if b"\0" in blob.stdout:
                continue
            try:
                text = _decode(blob.stdout)
            except UnicodeDecodeError:
                continue
            lines = text.splitlines()
            for lineno, rule, message in scan_text(text, denylist):
                line = lines[lineno - 1]
                if exceptions.history_allows(commit, name, rule, line):
                    continue
                # A current reviewed marker can cover the candidate tip without
                # a circular self-commit hash. The moment that tip becomes
                # historical, only a commit-keyed history entry can cover it.
                if (
                    commit == head_commit
                    and _marker_stripped_line(line) is not None
                    and exceptions.current_allows(name, rule, line)
                ):
                    continue
                print(f"history:{commit}:{name}:{lineno}: [{rule}] {message}")
                total += 1

    tags = _git(root, ["for-each-ref", "--format=%(objectname)%00%(objecttype)", "refs/tags"])
    if tags.returncode:
        print("history:0: [git] could not enumerate tags")
        total += 1
    else:
        for row in tags.stdout.splitlines():
            object_id, separator, object_type = row.partition("\0")
            if not separator or object_type != "tag":
                continue
            tag = _git(root, ["cat-file", "tag", object_id], binary=True)
            if tag.returncode:
                print(f"history-tag:{object_id}: [git] could not read tag metadata")
                total += 1
            else:
                total += _scan_metadata(object_id, "tag", tag.stdout, denylist, exceptions)
    return total


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else ["audit_public.py", *argv]
    positional = [item for item in argv[1:] if not item.startswith("-")]
    root = Path(positional[0]).expanduser().resolve() if positional else Path.cwd().resolve()
    quiet = "--quiet" in argv[1:]
    history = "--history" in argv[1:]
    tracked_only = "--all-files" not in argv[1:]
    exception_arg = next(
        (item.split("=", 1)[1] for item in argv[1:] if item.startswith("--exceptions=")), None
    )
    exception_path = Path(exception_arg).resolve() if exception_arg else root / DEFAULT_EXCEPTION_PATH
    try:
        exceptions = Exceptions(exception_path if exception_path.exists() else None)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"audit exception manifest rejected: {exc}", file=sys.stderr)
        return 1
    denylist = load_denylist()
    total = scan_current(root, denylist, exceptions, tracked_only)
    if history:
        total += scan_history(root, denylist, exceptions)
    for unused in exceptions.unused(history):
        print(f"exceptions:0: [unused] {unused}")
        total += 1
    if total:
        print(f"\n{total} potential leak or audit-integrity error(s) found — do not publish.", file=sys.stderr)
        return 1
    if not quiet:
        suffix = " + full history/metadata" if history else ""
        print(f"audit_public: clean ({root}){suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
