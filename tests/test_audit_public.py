"""Tests for scripts/audit_public.py.

Two things matter equally: it must catch real leaks, and it must NOT cry wolf
on the placeholders a starter repo is made of. A scanner that flags .env.example
gets switched off, and then it catches nothing at all.

The credentials below are invented fixtures, not real keys. The legacy
`audit:allow-file` text is tested below; whole-file skipping is unsupported.
Every value here is randomly typed and belongs to no account.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import audit_public  # noqa: E402


def rules(text, denylist=()):
    return {rule for _line, rule, _msg in audit_public.scan_text(text, denylist)}


class TestCatchesRealLeaks(unittest.TestCase):
    def test_private_key(self):
        self.assertIn("private-key", rules("-----BEGIN OPENSSH PRIVATE KEY-----"))  # audit:allow

    def test_openai_style_key(self):
        self.assertIn("openai-key", rules("OPENAI_API_KEY=sk-proj-Ab3dEf9hIj2lMn5pQr8tUv1xYz4B7c0D"))  # audit:allow

    def test_github_token(self):
        self.assertIn("github-token", rules("token: ghp_9sKq2Wm4Rt7Yv1Bn6Xz3Cd8Ef5Gh0Jk2Lp4"))  # audit:allow

    def test_google_key(self):
        self.assertIn("google-key", rules("key=AIzaSyD3Kf9Lm2Pq7Rt4Wv8Xz1Cb6Nh5Jg0Ye3"))  # audit:allow

    def test_telegram_bot_token(self):
        self.assertIn(
            "telegram-bot-token",
            rules("TELEGRAM_BOT_TOKEN=7284910356:AAF9kQ2mVx7Rp3Tz8Wn1Yb5Cd6Eg0Hj4Lk2"),  # audit:allow
        )

    def test_authorization_header(self):
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
        self.assertEqual(rules("key = sk-proj-Ab3dEf9hIj2lMn5pQr8tUv1xYz4B7c0D  # audit:allow"), set())

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
            blob.write_bytes(b"\x00\x01secret sk-proj-Ab3dEf9hIj2lMn5pQr8tUv1")  # audit:allow
            self.assertIsNone(audit_public.read_text(blob))


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


class TestEndToEnd(unittest.TestCase):
    def test_this_repo_is_clean(self):
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "audit_public.py"), str(REPO)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, f"audit failed on this repo:\n{proc.stdout}")

    def test_exits_nonzero_on_leak(self):
        with TemporaryDirectory() as td:
            (Path(td) / "leak.py").write_text('KEY = "ghp_9sKq2Wm4Rt7Yv1Bn6Xz3Cd8Ef5Gh0Jk2Lp4"\n')  # audit:allow
            proc = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "audit_public.py"), td],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("github-token", proc.stdout)
            self.assertIn("leak.py:1", proc.stdout)
            self.assertNotIn("ghp_9sKq2", proc.stdout)


if __name__ == "__main__":
    unittest.main()
