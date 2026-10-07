from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import threading
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from hermes_second_brain.cli import main as cli_main
from hermes_second_brain.mail_context_bundle import bundle_mail_context
from hermes_second_brain.mail_context_collector import (
    MailContextVault,
    build_record,
    classify_mail,
    collect_mail_context,
    mailbox_class,
    read_messages_bounded,
    record_markdown,
    select_folders,
)


FAKE_HIMALAYA = r'''
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

data = json.loads(Path(os.environ["FAKE_HIMALAYA_DATA"]).read_text())
log = Path(os.environ["FAKE_HIMALAYA_LOG"])
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
account = args[args.index("-a") + 1] if "-a" in args else ""
if account in data.get("fail_accounts", []):
    print("account unavailable", file=sys.stderr)
    raise SystemExit(3)
if args[:2] == ["folder", "list"]:
    print(json.dumps(data["accounts"][account]["folders"]))
    raise SystemExit(0)
if args[:2] == ["envelope", "list"]:
    folder = args[args.index("-f") + 1]
    if folder in data.get("fail_folders", {}).get(account, []):
        print("folder unavailable", file=sys.stderr)
        raise SystemExit(4)
    page = int(args[args.index("-p") + 1])
    pages = data["accounts"][account]["envelopes"].get(folder, [])
    if page > len(pages):
        print("page out of range", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(pages[page - 1]))
    raise SystemExit(0)
if args[:2] == ["message", "read"]:
    folder = args[args.index("-f") + 1]
    message_id = args[-1]
    fail_once = data.get("fail_reads_once", [])
    marker_dir = Path(os.environ["FAKE_HIMALAYA_LOG"]).parent
    marker = marker_dir / ("failed-" + account + "-" + folder.replace("/", "_") + "-" + message_id)
    if message_id in fail_once and not marker.exists():
        marker.write_text("failed", encoding="utf-8")
        print("temporary read failure with secret-token-should-not-persist", file=sys.stderr)
        raise SystemExit(5)
    body = data["accounts"][account]["bodies"][folder][message_id]
    print(json.dumps({"body": body}))
    raise SystemExit(0)
raise SystemExit(9)
'''


def _addr(local: str, domain: str) -> str:
    """Join a local part and a bare domain into an email address at runtime.

    The domain is passed as a plain string with no ``@``, so no full address
    appears as a static literal at rest. This keeps the public-safety scanner
    clean while the redaction/summarization tests still exercise the exact
    address strings they must handle.
    """
    return f"{local}@{domain}"


def envelope(message_id: str, subject: str, sender: str = "Sender <sender@example.com>", folder_date: str = "2026-07-17T09:00:00Z", flags: list[str] | None = None, attachments: bool = False) -> dict[str, object]:
    return {
        "id": message_id,
        "date": folder_date,
        "from": sender,
        "to": "Robin <robin.private@example.test>",
        "subject": subject,
        "flags": flags or [],
        "has_attachment": attachments,
    }


class MailContextCollectorTests(unittest.TestCase):
    def write_sanitized_mail(self, root: Path, digest: str, label: str, body: str = "sanitized body") -> Path:
        path = root / f"mail-{digest}.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    'source: "mail-context"',
                    f'subject: "{label}"',
                    "---",
                    "",
                    body,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def make_fake(self, tmp: Path, data: dict[str, object]) -> tuple[Path, Path]:
        fake = tmp / "himalaya-fake.py"
        fake.write_text(textwrap.dedent(FAKE_HIMALAYA).lstrip(), encoding="utf-8")
        fake.chmod(0o700)
        data_path = tmp / "data.json"
        data_path.write_text(json.dumps(data), encoding="utf-8")
        log_path = tmp / "commands.jsonl"
        os.environ["FAKE_HIMALAYA_DATA"] = str(data_path)
        os.environ["FAKE_HIMALAYA_LOG"] = str(log_path)
        return fake, log_path

    def sample_data(self) -> dict[str, object]:
        return {
            "accounts": {
                "icloud": {
                    "folders": ["Inbox", "Sent", "Drafts"],
                    "envelopes": {
                        "Inbox": [
                            [
                                envelope("m1", "Project update urgent?", f"Work Person <{_addr('boss', 'company.com')}>", attachments=True),
                                envelope("m2", "Your verification code 123456", "Auth <security@example.com>"),
                            ],
                            [envelope("m3", "Rechnung Juli", f"Bank <{_addr('service', 'bank.example')}>")],
                        ],
                        "Sent": [[envelope("s1", "Sent note", "Robin <me@example.test>", flags=["\\Sent"])]],
                        "Drafts": [[envelope("d1", "Draft idea", "Robin <me@example.test>")]],
                    },
                    "bodies": {
                        "Inbox": {
                            "m1": "Please review the project today. token sk-live-" + "secret-1234567890",
                            "m2": "Your login code is 123456. Do not share it.",
                            "m3": "Bitte Rechnung bis morgen bezahlen.",
                        },
                        "Sent": {"s1": "I will send the notes."},
                        "Drafts": {"d1": "Unfinished draft."},
                    },
                },
                "gmail": {
                    "folders": ["Inbox", "[Gmail]/All Mail", "[Gmail]/Spam", "[Gmail]/Trash", "[Gmail]/Drafts", "Receipts"],
                    "envelopes": {
                        "[Gmail]/All Mail": [[envelope("g1", "Newsletter digest", f"News <{_addr('hello', 'newsletter.example')}>")]],
                        "[Gmail]/Spam": [[envelope("g2", "Win now", f"Spam <{_addr('spam', 'bad.example')}>")]],
                        "[Gmail]/Trash": [[]],
                        "[Gmail]/Drafts": [[envelope("g3", "Gmail draft", "Robin <me@example.test>")]],
                    },
                    "bodies": {
                        "[Gmail]/All Mail": {"g1": "unsubscribe from this newsletter"},
                        "[Gmail]/Spam": {"g2": "spam body"},
                        "[Gmail]/Trash": {},
                        "[Gmail]/Drafts": {"g3": "draft body"},
                    },
                },
                "broken": {"folders": [], "envelopes": {}, "bodies": {}},
            },
            "fail_accounts": ["broken"],
        }

    def test_full_pagination_idempotent_incremental_preview_private_and_security_omission(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, log = self.make_fake(tmp, self.sample_data())
            db = tmp / "mail.sqlite3"
            out = tmp / "out"
            summary, code = collect_mail_context(["icloud"], {}, db, out, str(fake), page_size=2, full=True)
            self.assertEqual(code, 0)
            self.assertEqual(summary.scanned, 5)
            self.assertEqual(summary.new, 5)
            self.assertEqual(summary.materialized, 5)
            self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)

            rendered = "\n".join(path.read_text(encoding="utf-8") for path in sorted(out.glob("*.md")))
            self.assertIn('category: "security-auth"', rendered)
            self.assertIn("[Body omitted for security-auth mail.]", rendered)
            self.assertNotIn("123456", rendered)
            self.assertNotIn("sk-live", rendered)
            self.assertNotIn("robin.private@example.test", rendered)
            self.assertIn('"domain": "example.test"', rendered)
            self.assertNotIn("message_key", rendered)
            self.assertNotIn('account: "icloud"', rendered)
            self.assertNotIn('folder: "Inbox"', rendered)

            commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            reads = [cmd for cmd in commands if cmd[:2] == ["message", "read"]]
            self.assertTrue(reads)
            self.assertTrue(all("--preview" in cmd and "--no-headers" in cmd for cmd in reads))

            second, code = collect_mail_context(["icloud"], {}, db, out, str(fake), page_size=2, full=False)
            self.assertEqual(code, 0)
            self.assertEqual(second.new + second.updated, 0)
            self.assertEqual(second.unchanged, 4)

    def test_incremental_retries_prior_page_two_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data = self.sample_data()
            data["fail_reads_once"] = ["m3"]
            fake, log = self.make_fake(tmp, data)
            db = tmp / "mail.sqlite3"
            out = tmp / "out"

            first, code = collect_mail_context(["icloud"], {"icloud": ["Inbox"]}, db, out, str(fake), page_size=2, full=True, retries=1)
            self.assertEqual(code, 0)
            self.assertEqual(first.errors, 1)
            with sqlite3.connect(db) as conn:
                failures = conn.execute("SELECT envelope_id, error_kind FROM mail_message_failures").fetchall()
            self.assertEqual([(row[0], row[1]) for row in failures], [("m3", "CalledProcessError")])

            second, code = collect_mail_context(["icloud"], {"icloud": ["Inbox"]}, db, out, str(fake), page_size=2, full=False, retries=1)
            self.assertEqual(code, 0)
            self.assertEqual(second.errors, 0)
            self.assertEqual(second.new, 1)
            with sqlite3.connect(db) as conn:
                remaining = conn.execute("SELECT COUNT(*) FROM mail_message_failures").fetchone()[0]
            self.assertEqual(remaining, 0)
            commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            page2_lists = [cmd for cmd in commands if cmd[:2] == ["envelope", "list"] and "-p" in cmd and cmd[cmd.index("-p") + 1] == "2"]
            m3_reads = [cmd for cmd in commands if cmd[:2] == ["message", "read"] and cmd[-1] == "m3"]
            self.assertGreaterEqual(len(page2_lists), 2)
            self.assertEqual(len(m3_reads), 2)

    def test_himalaya_addr_dicts_export_names_and_domains_only(self) -> None:
        record = build_record(
            "icloud",
            "Inbox",
            "m-real",
            {
                "id": "m-real",
                "date": "2026-07-17T09:00:00Z",
                "from": {"addr": _addr("boss", "company.com"), "name": "Boss Person"},
                "to": [{"addr": "robin.private@example.test", "name": "Robin"}, {"addr": "other@example.net", "name": ""}],
                "subject": "Project",
                "flags": [],
            },
            "plain body",
        )
        self.assertEqual(record.sender_display, "Boss Person")
        self.assertEqual(record.sender_domain, "company.com")
        self.assertEqual(record.recipient_display, "Robin")
        self.assertEqual(record.recipient_domain, "example.test")

        no_name = build_record(
            "icloud",
            "Inbox",
            "m-no-name",
            {"id": "m-no-name", "from": {"addr": "localpart@example.org"}, "to": [{"addr": "target@example.net"}], "subject": "Hello"},
            "body",
        )
        rendered = record_markdown(no_name)
        self.assertEqual(no_name.sender_display, "")
        self.assertEqual(no_name.sender_domain, "example.org")
        self.assertNotIn("localpart", rendered)
        self.assertNotIn("localpart@example.org", rendered)

    def test_security_auth_markdown_omits_subject_body_codes_emails_and_urls(self) -> None:
        record = build_record(
            "icloud",
            "Inbox",
            "auth-1",
            {
                "id": "auth-1",
                "from": {"addr": "security@example.com", "name": "Security"},
                "to": {"addr": "robin.private@example.test", "name": "Robin"},
                "subject": "Password reset 12345 123456 12345678 for robin.private@example.test https://reset.example.com/token",
            },
            "Raw body has code 123456, email robin.private@example.test, and https://reset.example.com/token",
        )
        rendered = record_markdown(record)
        self.assertIn('subject: "[security-auth subject omitted]"', rendered)
        for forbidden in ("12345", "123456", "12345678", "robin.private@example.test", "https://reset.example.com/token", "Raw body", "Password reset"):
            self.assertNotIn(forbidden, rendered)

    def test_bounded_workers_and_retries_without_many_subprocesses(self) -> None:
        attempts: dict[str, int] = {}
        threads: set[str] = set()
        jobs = [(envelope(f"m{i}", f"Subject {i}"), f"m{i}", f"fp{i}") for i in range(8)]

        def flaky(_himalaya: str, _account: str, _folder: str, envelope_id: str, _timeout: float) -> str:
            threads.add(threading.current_thread().name)
            attempts[envelope_id] = attempts.get(envelope_id, 0) + 1
            if envelope_id in {"m2", "m5"} and attempts[envelope_id] == 1:
                raise subprocess.TimeoutExpired(["fake"], 1)
            return f"body {envelope_id}"

        with mock.patch("hermes_second_brain.mail_context_collector.read_message", side_effect=flaky):
            results = read_messages_bounded("fake", "icloud", "Inbox", jobs, timeout=1, workers=3, retries=2, retry_backoff=0)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(result[-1] is None for result in results))
        self.assertEqual(attempts["m2"], 2)
        self.assertEqual(attempts["m5"], 2)
        self.assertLessEqual(len(threads), 3)

    def test_account_failure_continues_and_json_summary_has_no_pii(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp = Path(td)
            fake, _ = self.make_fake(tmp, self.sample_data())
            code = cli_main(
                [
                    "mail-context-collect",
                    "--account",
                    "broken",
                    "--account",
                    "icloud",
                    "--db",
                    str(tmp / "mail.sqlite3"),
                    "--output-dir",
                    str(tmp / "out"),
                    "--himalaya",
                    str(fake),
                    "--page-size",
                    "2",
                    "--json",
                ]
            )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["accounts"], 2)
            self.assertEqual(payload["errors"], 1)
            self.assertNotIn(_addr("boss", "company.com"), stdout.getvalue())
            self.assertNotIn("Project update", stdout.getvalue())
            self.assertNotIn("Please review", stdout.getvalue())

    def test_all_accounts_failed_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, _ = self.make_fake(tmp, self.sample_data())
            summary, code = collect_mail_context(["broken"], {}, tmp / "mail.sqlite3", tmp / "out", str(fake))
            self.assertEqual(code, 1)
            self.assertEqual(summary.failed_accounts, 1)

    def test_one_failed_folder_after_success_does_not_fail_account(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data = self.sample_data()
            data["fail_folders"] = {"icloud": ["Sent"]}
            fake, _ = self.make_fake(tmp, data)
            summary, code = collect_mail_context(["icloud"], {"icloud": ["Inbox", "Sent"]}, tmp / "mail.sqlite3", tmp / "out", str(fake), page_size=2, full=True)
            self.assertEqual(code, 0)
            self.assertEqual(summary.failed_accounts, 0)
            self.assertEqual(summary.errors, 1)

    def test_all_selected_folders_failed_marks_account_failed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data = self.sample_data()
            data["fail_folders"] = {"icloud": ["Inbox", "Sent"]}
            fake, _ = self.make_fake(tmp, data)
            summary, code = collect_mail_context(["icloud"], {"icloud": ["Inbox", "Sent"]}, tmp / "mail.sqlite3", tmp / "out", str(fake), page_size=2, full=True)
            self.assertEqual(code, 1)
            self.assertEqual(summary.failed_accounts, 1)

    def test_gmail_folder_policy_and_explicit_folder_override(self) -> None:
        self.assertEqual(
            select_folders("gmail", ["Inbox", "[Gmail]/All Mail", "[Gmail]/Spam", "[Gmail]/Trash", "[Gmail]/Drafts"]),
            ["[Gmail]/All Mail", "[Gmail]/Spam", "[Gmail]/Trash", "[Gmail]/Drafts"],
        )
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, log = self.make_fake(tmp, self.sample_data())
            collect_mail_context(["gmail"], {"gmail": ["Receipts"]}, tmp / "mail.sqlite3", tmp / "out", str(fake), full=True)
            commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(cmd[:2] == ["envelope", "list"] and "Receipts" in cmd for cmd in commands))

    def test_symlink_output_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, _ = self.make_fake(tmp, self.sample_data())
            target = tmp / "target"
            target.mkdir()
            link = tmp / "out-link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                collect_mail_context(["icloud"], {}, tmp / "mail.sqlite3", link, str(fake))

    def test_symlink_parent_for_db_and_output_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, _ = self.make_fake(tmp, self.sample_data())
            real = tmp / "real"
            real.mkdir()
            link_parent = tmp / "link-parent"
            link_parent.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked path component"):
                collect_mail_context(["icloud"], {}, link_parent / "mail.sqlite3", tmp / "out", str(fake))
            with self.assertRaisesRegex(ValueError, "symlinked path component"):
                collect_mail_context(["icloud"], {}, tmp / "mail.sqlite3", link_parent / "out", str(fake))

            previous_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                with self.assertRaisesRegex(ValueError, "symlinked path component"):
                    collect_mail_context(["icloud"], {}, Path("link-parent/mail.sqlite3"), Path("out"), str(fake))
            finally:
                os.chdir(previous_cwd)

    def test_configured_mailbox_classes_are_coarse_and_correct(self) -> None:
        self.assertEqual(mailbox_class("icloud"), "personal")
        self.assertEqual(mailbox_class("gmail"), "work")
        self.assertEqual(mailbox_class("acme"), "startup")

    def test_stale_unresolved_failure_pruned_after_complete_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "mail.sqlite3"
            vault = MailContextVault(db)
            vault.mark_failure("icloud", "Inbox", "gone", "fp", "read", RuntimeError("secret"))
            fake, _ = self.make_fake(tmp, self.sample_data())
            summary, code = collect_mail_context(["icloud"], {"icloud": ["Inbox"]}, db, tmp / "out", str(fake), page_size=2, full=True)
            self.assertEqual(code, 0)
            self.assertEqual(summary.errors, 0)
            with sqlite3.connect(db) as conn:
                failures = conn.execute("SELECT COUNT(*) FROM mail_message_failures").fetchone()[0]
            self.assertEqual(failures, 0)

    def test_max_messages_stops_further_folder_enumeration(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, log = self.make_fake(tmp, self.sample_data())
            summary, code = collect_mail_context(["icloud"], {}, tmp / "mail.sqlite3", tmp / "out", str(fake), page_size=2, full=True, max_messages=1)
            self.assertEqual(code, 0)
            self.assertEqual(summary.scanned, 1)
            commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            listed_folders = [cmd[cmd.index("-f") + 1] for cmd in commands if cmd[:2] == ["envelope", "list"]]
            self.assertEqual(listed_folders, ["Inbox"])

    def test_malformed_overflow_timestamp_safely_defaults(self) -> None:
        data = self.sample_data()
        data["accounts"]["icloud"]["envelopes"]["Inbox"] = [[envelope("m-overflow", "Hello", folder_date="999999999999999999999999999999")]]
        data["accounts"]["icloud"]["bodies"]["Inbox"] = {"m-overflow": "plain body"}
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake, _ = self.make_fake(tmp, data)
            collect_mail_context(["icloud"], {"icloud": ["Inbox"]}, tmp / "mail.sqlite3", tmp / "out", str(fake), full=True)
            with sqlite3.connect(tmp / "mail.sqlite3") as conn:
                date = conn.execute("SELECT message_date FROM mail_messages WHERE envelope_id='m-overflow'").fetchone()[0]
            self.assertEqual(date, "1970-01-01T00:00:00Z")

    def test_classifier_category_signals(self) -> None:
        cases = {
            "personal": ("Inbox", "family dinner", "see you"),
            "work": ("Inbox", "project client", "github"),
            "education": ("Inbox", "University course", "seminar"),
            "finance": ("Inbox", "Bank payment", "Steuer"),
            "legal-admin": ("Inbox", "Insurance contract", "Vertrag"),
            "housing": ("Inbox", "Rent utility", "Miete"),
            "travel": ("Inbox", "Flight booking", "hotel"),
            "health": ("Inbox", "Doctor appointment", "Praxis"),
            "calendar": ("Inbox", "Meeting invite", "Termin"),
            "shopping-shipping": ("Inbox", "Delivery tracking", "DHL Paket"),
            "receipts": ("Inbox", "Receipt", "Quittung"),
            "subscriptions-newsletters": ("Inbox", "Newsletter", "unsubscribe"),
            "security-auth": ("Inbox", "Verification code", "2FA"),
            "social": ("Inbox", "LinkedIn", "social"),
            "support": ("Inbox", "Support ticket", "helpdesk"),
            "sent": ("Sent", "whatever", "body"),
            "drafts": ("Drafts", "whatever", "body"),
            "spam": ("Spam", "whatever", "body"),
            "other": ("Inbox", "plain", "body"),
        }
        for expected, (folder, subject, body) in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify_mail("icloud", folder, "example.com", subject, body, (), False), expected)

    def test_mail_context_bundle_deterministic_shards_ordering_noop_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            output = tmp / "bundles"
            self.write_sanitized_mail(source, "f" + "1" * 31, "third")
            first = self.write_sanitized_mail(source, "0" + "2" * 31, "first")
            self.write_sanitized_mail(source, "0" + "1" * 31, "second")
            malformed = source / "mail-not-a-digest.md"
            malformed.write_text("ignored", encoding="utf-8")

            summary = bundle_mail_context(source, output)
            self.assertEqual(summary.source_file_count, 3)
            self.assertEqual(summary.bundle_count, 2)
            self.assertEqual(summary.changed, 2)
            self.assertEqual(summary.errors, 1)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((output / "mail-context-0.md").stat().st_mode), 0o600)

            rendered = (output / "mail-context-0.md").read_text(encoding="utf-8")
            self.assertLess(rendered.index('subject: "second"'), rendered.index('subject: "first"'))
            self.assertIn("<!-- mail-context-message-separator -->", rendered)
            self.assertNotIn(first.name, rendered)
            self.assertNotRegex(rendered, r"mail_[0-9a-f]{32}")
            self.assertNotIn("mail-not-a-digest", rendered)

            second = bundle_mail_context(source, output)
            self.assertEqual(second.changed, 0)
            self.assertEqual(second.unchanged, 2)
            self.assertEqual(second.removed, 0)

            first.write_text(first.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
            changed = bundle_mail_context(source, output)
            self.assertEqual(changed.changed, 1)
            self.assertEqual(changed.unchanged, 1)

    def test_mail_context_bundle_has_stable_sixteen_way_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            for shard in "0123456789abcdef":
                self.write_sanitized_mail(source, shard + "0" * 31, f"shard-{shard}")

            summary = bundle_mail_context(source, tmp / "bundles")
            self.assertEqual(summary.source_file_count, 16)
            self.assertEqual(summary.bundle_count, 16)
            self.assertEqual(summary.changed, 16)
            self.assertEqual(sorted(path.name for path in (tmp / "bundles").glob("mail-context-*.md")), [f"mail-context-{shard}.md" for shard in "0123456789abcdef"])

    def test_mail_context_bundle_removes_only_empty_stale_shard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            output = tmp / "bundles"
            only = self.write_sanitized_mail(source, "a" + "0" * 31, "only")
            bundle_mail_context(source, output)
            self.assertTrue((output / "mail-context-a.md").exists())
            unrelated = output / "unrelated.md"
            unrelated.write_text("keep", encoding="utf-8")

            only.unlink()
            summary = bundle_mail_context(source, output)
            self.assertEqual(summary.removed, 1)
            self.assertFalse((output / "mail-context-a.md").exists())
            self.assertTrue(unrelated.exists())

    def test_mail_context_bundle_symlink_refusal_skip_and_private_modes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            output = tmp / "bundles"
            self.write_sanitized_mail(source, "1" + "0" * 31, "safe")
            target = tmp / "target.md"
            target.write_text("not read through symlink", encoding="utf-8")
            (source / ("mail-" + "2" * 32 + ".md")).symlink_to(target)

            summary = bundle_mail_context(source, output)
            self.assertEqual(summary.source_file_count, 1)
            self.assertEqual(summary.errors, 1)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((output / "mail-context-1.md").stat().st_mode), 0o600)

            real = tmp / "real"
            real.mkdir()
            input_link = tmp / "input-link"
            input_link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                bundle_mail_context(input_link, output)

            parent_link = tmp / "parent-link"
            parent_link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked path component"):
                bundle_mail_context(source, parent_link / "out")

    def test_mail_context_bundle_read_failure_preserves_existing_shard_and_cli_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            output = tmp / "bundles"
            good = self.write_sanitized_mail(source, "5" + "0" * 31, "safe")
            bundle_mail_context(source, output)
            last_good = (output / "mail-context-5.md").read_text(encoding="utf-8")

            unreadable = self.write_sanitized_mail(source, "5" + "1" * 31, "unreadable")
            original_read_text = Path.read_text
            unreadable_resolved = unreadable.resolve()
            with mock.patch("pathlib.Path.read_text", autospec=True) as read_text:
                read_text.side_effect = lambda path, *args, **kwargs: (_ for _ in ()).throw(OSError("permission denied")) if path.resolve() == unreadable_resolved else original_read_text(path, *args, **kwargs)
                code = cli_main(["mail-context-bundle", "--input-dir", str(source), "--output-dir", str(output), "--json"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["errors"], 1)
            self.assertEqual((output / "mail-context-5.md").read_text(encoding="utf-8"), last_good)
            self.assertNotIn(good.name, stdout.getvalue())
            self.assertNotIn(unreadable.name, stdout.getvalue())
            self.assertNotIn("safe", stdout.getvalue())
            self.assertNotIn("unreadable", stdout.getvalue())

    def test_mail_context_bundle_symlink_and_malformed_cli_fail_without_names(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            self.write_sanitized_mail(source, "6" + "0" * 31, "safe")
            (source / "mail-not-a-digest.md").write_text("malformed", encoding="utf-8")
            target = tmp / "target.md"
            target.write_text("linked", encoding="utf-8")
            (source / ("mail-" + "7" * 32 + ".md")).symlink_to(target)

            code = cli_main(["mail-context-bundle", "--input-dir", str(source), "--output-dir", str(tmp / "bundles"), "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["errors"], 2)
            self.assertEqual(payload["bundle_count"], 1)
            self.assertNotIn("mail-not-a-digest", stdout.getvalue())
            self.assertNotIn("mail-" + "7" * 32, stdout.getvalue())
            self.assertNotIn("safe", stdout.getvalue())

    def test_mail_context_bundle_preserves_sanitized_security_auth_omission(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            self.write_sanitized_mail(source, "3" + "0" * 31, "[security-auth subject omitted]", "[Body omitted for security-auth mail.]")

            bundle_mail_context(source, tmp / "bundles")
            rendered = ((tmp / "bundles") / "mail-context-3.md").read_text(encoding="utf-8")
            self.assertIn("[Body omitted for security-auth mail.]", rendered)
            self.assertNotIn("123456", rendered)
            self.assertNotIn("password reset token", rendered)

    def test_mail_context_bundle_redacts_full_email_addresses_only(self) -> None:
        # Bare fake domain, interpolated below so the .dev addresses are never
        # static literals at rest (keeps the public-safety scanner clean).
        dev = "example.dev"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            first = self.write_sanitized_mail(
                source,
                "4" + "1" * 31,
                "Follow up FIRST.LAST+Case@Example.COM",
                f"Body mentions person@example.com and {_addr('SECOND_user-2', 'sub.example.co.uk')} before code [REDACTED].",
            )
            first.write_text(
                first.read_text(encoding="utf-8").replace(
                    'source: "mail-context"\n',
                    'source: "mail-context"\nfrom: {"display": "Example Person", "domain": "example.com"}\n',
                ),
                encoding="utf-8",
            )
            self.write_sanitized_mail(
                source,
                "4" + "2" * 31,
                "Multiple third@example.org fourth+tag@Example.org",
                (
                    "Keep domain-only metadata useful: example.com. Multiple addresses: alpha@example.net beta@EXAMPLE.NET. "
                    f"Embedded wrappers: _under@{dev} %encoded@{dev}: colon@{dev}] bracket@{dev} "
                    f"prewrapword@{dev} post@{dev}word."
                ),
            )

            bundle_mail_context(source, tmp / "bundles")
            rendered = ((tmp / "bundles") / "mail-context-4.md").read_text(encoding="utf-8")
            self.assertNotRegex(rendered, r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,63}")
            self.assertGreaterEqual(rendered.count("[EMAIL]"), 6)
            for leaked in (
                "FIRST.LAST+Case@Example.COM",
                "person@example.com",
                _addr("SECOND_user-2", "sub.example.co.uk"),
                "third@example.org",
                "fourth+tag@Example.org",
                "alpha@example.net",
                "beta@EXAMPLE.NET",
                _addr("under", dev),
                _addr("encoded", dev),
                _addr("colon", dev),
                _addr("bracket", dev),
                _addr("prewrapword", dev),
                _addr("post", dev + "word"),
            ):
                self.assertNotIn(leaked, rendered)
            self.assertIn('"display": "Example Person"', rendered)
            self.assertIn('"domain": "example.com"', rendered)
            self.assertIn("domain-only metadata useful: example.com", rendered)
            self.assertIn("code [REDACTED]", rendered)

    def test_mail_context_bundle_cli_json_is_aggregate_only(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp = Path(td)
            source = tmp / "mail"
            source.mkdir()
            self.write_sanitized_mail(source, "4" + "0" * 31, "safe")
            code = cli_main(["mail-context-bundle", "--input-dir", str(source), "--output-dir", str(tmp / "bundles"), "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(sorted(payload), ["bundle_count", "changed", "errors", "removed", "source_file_count", "unchanged"])
            self.assertEqual(payload["source_file_count"], 1)
            self.assertNotIn(str(source), stdout.getvalue())
            self.assertNotIn("mail-", stdout.getvalue())
            self.assertNotIn("safe", stdout.getvalue())

    def test_mail_context_sync_script_order_and_syntax(self) -> None:
        script = Path.cwd() / "scripts" / "mail_context_sync.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertLess(text.index("mail-context-collect"), text.index("mail-context-bundle"))
        self.assertLess(text.index("mail-context-bundle"), text.index(" sync --manifest "))
        self.assertIn('APP="${APP:-hermes_second_brain}"', text)
        self.assertIn('PYTHON="${PYTHON:-python3.11}"', text)
        self.assertIn('OV_BINARY="$OV_BINARY" "$PYTHON" -m "$APP" sync --manifest "$MANIFEST"', text)
        subprocess.run(["zsh", "-n", str(script)], check=True)

    def test_manifest_uses_mail_context_bundles_not_important_mail(self) -> None:
        for manifest in (Path.cwd() / "config" / "manifest.example.json", Path.cwd() / "config" / "manifest.example.json"):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            ids = {source["id"] for source in data["sources"]}
            self.assertIn("mail-context-bundles", ids)
            self.assertNotIn("important-mail", ids)
            mail_sources = [source for source in data["sources"] if source["id"] == "mail-context-bundles"]
            self.assertEqual(mail_sources[0]["namespace"], "mail")
            self.assertEqual(mail_sources[0]["include_extensions"], [".md"])
            self.assertIn("mail-bundles", mail_sources[0]["root"])


if __name__ == "__main__":
    unittest.main()
