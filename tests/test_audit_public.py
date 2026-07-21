"""Tests for scripts/audit_public.py.

Two things matter equally: it must catch real leaks, and it must NOT cry wolf
on the placeholders a starter repo is made of. A scanner that flags .env.example
gets switched off, and then it catches nothing at all.

No real credential ever appears as a literal in this file. Every realistic
secret-shaped fixture is assembled at runtime by ``_fixture_token`` (deterministic
per tag), so an external history/secret scanner such as gitleaks finds nothing to
flag in the source, yet the values fed to ``audit_public`` at runtime are genuine
high-entropy strings — the assertions below still prove the scanner catches
real-looking secrets. The legacy ``audit:allow-file`` text is tested below;
whole-file skipping is unsupported.
"""
import random
import string
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import audit_public  # noqa: E402


_FIXTURE_ALPHABET = string.ascii_letters + string.digits


def _fixture_token(prefix: str, body_len: int, tag: str) -> str:
    """Build a realistic, high-entropy fake credential at runtime.

    The returned token never appears as a literal in this file, so a secret
    scanner has nothing static to match here; but the runtime value is a genuine
    high-entropy string, so ``audit_public``'s rules fire on it exactly as they
    would on a real leak. ``tag`` seeds a deterministic RNG for reproducibility.
    """
    rng = random.Random("audit-fixture::" + tag)
    body = "".join(rng.choice(_FIXTURE_ALPHABET) for _ in range(body_len))
    return prefix + body


def rules(text, denylist=()):
    return {rule for _line, rule, _msg in audit_public.scan_text(text, denylist)}


class TestCatchesRealLeaks(unittest.TestCase):
    def test_private_key(self):
        self.assertIn("private-key", rules("-----BEGIN OPENSSH PRIVATE KEY-----"))  # audit:allow

    def test_openai_style_key(self):
        token = _fixture_token("sk-proj-", 32, "openai")
        self.assertIn("openai-key", rules("OPENAI_API_KEY=" + token))

    def test_github_token(self):
        token = _fixture_token("ghp_", 36, "github")
        self.assertIn("github-token", rules("token: " + token))

    def test_google_key(self):
        token = _fixture_token("AIza", 33, "google")
        self.assertIn("google-key", rules("key=" + token))

    def test_telegram_bot_token(self):
        token = "7284910356:" + _fixture_token("", 34, "telegram")
        self.assertIn("telegram-bot-token", rules("TELEGRAM_BOT_TOKEN=" + token))

    def test_authorization_header(self):
        # Low-entropy JWT-prefix literal: caught by audit_public's keyword rule,
        # ignored by gitleaks (no full JWT). Kept static so an older, marker-less
        # historical copy of this exact line stays grandfathered by the audit
        # history scan's line-scoped allowance.
        self.assertIn(
            "authorization-header",
            rules('headers = {"Authorization": "Bearer eyJ0eXAiOiJKV1QiLCJhbGc"}'),  # audit:allow
        )

    def test_macos_home_path(self):
        path = "/" + "Users" + "/localaccount/hermes"
        self.assertIn("macos-home", rules(f"cd {path}"))

    def test_linux_home_path(self):
        path = "/" + "home" + "/workstation/.hermes/config.yaml"
        self.assertIn("linux-home", rules(f"log: {path}"))

    def test_real_email(self):
        self.assertIn("email", rules("contact: account@company.co"))  # audit:allow

    def test_discord_snowflake(self):
        self.assertIn("discord-snowflake", rules("guild_id = 934721085463920174"))  # audit:allow

    def test_telegram_group_id(self):
        self.assertIn("telegram-group-id", rules("chat_id: -1001938475620"))  # audit:allow

    def test_denylist_hits(self):
        found = rules("the maintainer is InternalCodeName", denylist=["internalcodename"])
        self.assertIn("denylist", found)

    def test_denylist_is_case_insensitive(self):
        self.assertIn("denylist", rules("Contact INTERNALCODENAME", denylist=["InternalCodeName"]))


class TestIgnoresPlaceholders(unittest.TestCase):
    def test_empty_env_assignment(self):
        self.assertEqual(rules("TELEGRAM_BOT_TOKEN="), set())

    def test_empty_yaml_value(self):
        self.assertEqual(rules('provider: ""'), set())

    def test_your_key_placeholder(self):
        self.assertEqual(rules("OPENAI_API_KEY=sk-your-key-here"), set())

    def test_angle_bracket_placeholder(self):
        self.assertEqual(rules("Authorization: Bearer <YOUR_TOKEN>"), set())

    def test_example_email(self):
        self.assertEqual(rules("email: user@example.com"), set())

    def test_generic_home_paths(self):
        self.assertEqual(rules("/" + "Users" + "/you/hermes-agent"), set())
        self.assertEqual(rules("/" + "home" + "/user/.hermes"), set())

    def test_repeated_digit_fixture_ids(self):
        self.assertEqual(rules("DISCORD_VOICE_AUTOJOIN_GUILD_ID=000000000000000000"), set())

    def test_allow_marker_suppresses(self):
        token = _fixture_token("sk-proj-", 32, "suppress")
        self.assertEqual(rules("key = " + token + "  # audit:allow"), set())

    def test_short_numbers_are_not_ids(self):
        self.assertEqual(rules("timeout: 300\nport: 9222\nsample_rate: 24000"), set())


class TestFileWalk(unittest.TestCase):
    def test_skips_git_dir(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("url = git@github.com:someone/x.git\n")  # audit:allow
            (root / "linked-worktree.git").write_text("gitdir: /local/metadata\n")
            (root / "ok.txt").write_text("hello\n")
            names = [p.name for p in audit_public.iter_files(root, tracked_only=False)]
            self.assertEqual(names, ["linked-worktree.git", "ok.txt"])

    def test_skips_binary_files(self):
        with TemporaryDirectory() as td:
            blob = Path(td) / "data.dat"
            # Short, low-entropy sk-proj literal: gitleaks does not flag it; kept
            # static so its marker-less historical twin stays grandfathered.
            blob.write_bytes(b"\x00\x01secret sk-proj-Ab3dEf9hIj2lMn5pQr8tUv1")  # audit:allow
            self.assertIsNone(audit_public.read_text(blob))

    def test_default_git_scan_includes_nonignored_untracked_files(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            (root / "new-release-file.txt").write_text("publish me\n")
            (root / ".gitignore").write_text("ignored.txt\n")
            (root / "ignored.txt").write_text("runtime only\n")
            names = sorted(path.name for path in audit_public.iter_files(root))
            self.assertEqual(names, [".gitignore", "new-release-file.txt"])


class TestDenylistLoading(unittest.TestCase):
    def _load(self, value):
        import os
        old = os.environ.get("PUBLIC_AUDIT_DENYLIST")
        os.environ["PUBLIC_AUDIT_DENYLIST"] = value
        try:
            return audit_public.load_denylist()
        finally:
            if old is None:
                os.environ.pop("PUBLIC_AUDIT_DENYLIST", None)
            else:
                os.environ["PUBLIC_AUDIT_DENYLIST"] = old

    def test_comma_separated(self):
        self.assertEqual(self._load("alpha, Beta"), ["alpha", "beta"])

    def test_from_file_with_comments(self):
        with TemporaryDirectory() as td:
            f = Path(td) / "deny.txt"
            f.write_text("# my strings\nalpha\n\nBeta\n")
            self.assertEqual(self._load(str(f)), ["alpha", "beta"])


class TestHistoryAllowances(unittest.TestCase):
    def test_trailing_marker_covers_only_the_same_historical_line(self):
        token = _fixture_token("sk-proj-", 32, "history")
        allowed = "value = " + token + "  # audit:allow"
        self.assertEqual(audit_public._marker_stripped_line(allowed), "value = " + token)
        self.assertIsNone(audit_public._marker_stripped_line("value = something-else"))

    def test_old_scanner_self_test_recognition_is_path_and_rule_scoped(self):
        fixture = (
            'self.assertIn("macos-home", rules("cd '
            + "/"
            + 'Users/localaccount/hermes"))'
        )
        path = Path("tests/test_audit_public.py")
        self.assertTrue(audit_public._is_historical_self_test_fixture(path, fixture, "macos-home"))
        self.assertFalse(audit_public._is_historical_self_test_fixture(path, fixture, "email"))
        self.assertFalse(
            audit_public._is_historical_self_test_fixture(Path("src/example.py"), fixture, "macos-home")
        )

    def test_history_recognizer_covers_multiline_and_e2e_fixtures(self):
        path = Path("tests/test_audit_public.py")
        # b) a bare rules(...) fixture split onto its own line (older telegram vector).
        # The recognizer keys on the line SHAPE, not the token, so build it at runtime.
        telegram = '            rules("TELEGRAM_BOT_TOKEN=7284910356:' + _fixture_token("", 30, "hist-tg") + '")'
        self.assertTrue(
            audit_public._is_historical_self_test_fixture(path, telegram, "telegram-bot-token")
        )
        # c) the end-to-end leak.py fixture the leak test writes
        e2e = '            (Path(td) / "leak.py").write_text(\'KEY = "' + _fixture_token("ghp_", 36, "hist-e2e") + '"\\n\')'
        self.assertTrue(audit_public._is_historical_self_test_fixture(path, e2e, "github-token"))
        # still narrow: same shapes in any OTHER path are NOT grandfathered
        self.assertFalse(
            audit_public._is_historical_self_test_fixture(Path("prod/config.py"), telegram, "telegram-bot-token")
        )
        self.assertFalse(
            audit_public._is_historical_self_test_fixture(Path("prod/seed.py"), e2e, "github-token")
        )
        # and an ordinary assignment in this file is NOT auto-grandfathered
        self.assertFalse(
            audit_public._is_historical_self_test_fixture(
                path, '        api_key = "' + _fixture_token("", 20, "neg-assign") + '"', "aws-key"
            )
        )


class TestEndToEnd(unittest.TestCase):
    def test_this_repo_is_clean(self):
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "audit_public.py"), str(REPO)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, f"audit failed on this repo:\n{proc.stdout}")

    def test_exits_nonzero_on_leak(self):
        with TemporaryDirectory() as td:
            token = _fixture_token("ghp_", 36, "e2e-leak")
            (Path(td) / "leak.py").write_text('KEY = "' + token + '"\n')
            proc = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "audit_public.py"), td],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("github-token", proc.stdout)
            self.assertIn("leak.py:1", proc.stdout)
            # the report must never echo the matched value
            self.assertNotIn(token, proc.stdout)


if __name__ == "__main__":
    unittest.main()
