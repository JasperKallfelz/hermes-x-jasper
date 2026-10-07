from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
import plistlib
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hermes_second_brain import signal_desktop as sd
from hermes_second_brain.context_inbox import ContextInbox

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False


SQLCIPHER = shutil.which("sqlcipher") or (
    sd.DEFAULT_SQLCIPHER if Path(sd.DEFAULT_SQLCIPHER).is_file() else None
)
TEST_KEY = "".join(format(n, "x") for n in range(16)) * 4


class FakeResult:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def encrypt_config_key(password: bytes, key_hex: str, *, encoding: str = "hex", prefix: bytes = b"v10") -> str:
    if not HAVE_CRYPTOGRAPHY:
        raise unittest.SkipTest("cryptography unavailable")
    wrapping_key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
    plaintext = key_hex.encode("ascii")
    padding = 16 - len(plaintext) % 16
    plaintext += bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(wrapping_key), modes.CBC(b" " * 16)).encryptor()
    blob = prefix + encryptor.update(plaintext) + encryptor.finalize()
    return blob.hex() if encoding == "hex" else base64.b64encode(blob).decode("ascii")


MESSAGES_DDL = """
CREATE TABLE messages(
  id TEXT PRIMARY KEY,
  json TEXT,
  conversationId TEXT,
  type TEXT,
  body TEXT,
  sent_at INTEGER,
  timestamp INTEGER,
  received_at INTEGER,
  received_at_ms INTEGER,
  source TEXT,
  sourceServiceId TEXT
)
"""
CONVERSATIONS_DDL = """
CREATE TABLE conversations(
  id TEXT PRIMARY KEY,
  json TEXT,
  type TEXT,
  name TEXT,
  profileName TEXT,
  profileFamilyName TEXT,
  e164 TEXT,
  serviceId TEXT,
  aci TEXT
)
"""
ATTACHMENTS_DDL = """
CREATE TABLE message_attachments(
  messageId TEXT NOT NULL,
  editHistoryIndex INTEGER NOT NULL,
  attachmentType TEXT NOT NULL,
  orderInMessage INTEGER NOT NULL,
  contentType TEXT,
  fileName TEXT,
  size INTEGER,
  width INTEGER,
  height INTEGER,
  flags INTEGER,
  path TEXT,
  key TEXT,
  digest TEXT,
  iv TEXT,
  transitCdnKey TEXT
)
"""


def populate_signal_fixture(connection: sqlite3.Connection, attachments_root: Path) -> None:
    (attachments_root / "ab").mkdir(parents=True, exist_ok=True)
    (attachments_root / "ab" / "att1.bin").write_bytes(b"synthetic attachment")
    (attachments_root / "ab" / "sticker.bin").write_bytes(b"synthetic sticker")
    connection.execute(MESSAGES_DDL)
    connection.execute(CONVERSATIONS_DDL)
    connection.execute(ATTACHMENTS_DDL)
    connection.executemany(
        "INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?,?)",
        [
            ("conv-dm", "{}", "private", "Alex", "Alex", "P", "+15557654321", "uuid-alex", "aci-alex"),
            ("conv-grp", "{}", "group", "Team", None, None, None, None, None),
            ("conv-bob", "{}", "private", "Bob", "Bob", None, "+15550001111", "uuid-bob", "aci-bob"),
        ],
    )

    def add_message(
        message_id: str,
        conversation_id: str,
        message_type: str,
        body: str,
        counter: int,
        *,
        source: str | None = None,
        service_id: str | None = None,
        blob: dict | None = None,
    ) -> None:
        timestamp = 1_784_280_000_000 + counter
        connection.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                json.dumps(blob or {}),
                conversation_id,
                message_type,
                body,
                timestamp,
                timestamp,
                counter,
                timestamp,
                source,
                service_id,
            ),
        )

    add_message("m1", "conv-dm", "incoming", "hey there", 100, source="+15557654321", service_id="uuid-alex")
    add_message("m2", "conv-dm", "outgoing", "reply from me", 200)
    add_message(
        "m3",
        "conv-grp",
        "incoming",
        "look at this",
        300,
        source="+15550001111",
        service_id="uuid-bob",
        blob={
            "quote": {"id": 1_784_270_000_000, "text": "original text", "authorAci": "aci-alex"},
            "attachments": [
                {
                    "contentType": "image/jpeg",
                    "fileName": "legacy.jpg",
                    "path": "ab/att1.bin",
                    "key": "LEGACY-SECRET-KEY",
                    "digest": "LEGACY-SECRET-DIGEST",
                    "iv": "LEGACY-SECRET-IV",
                }
            ],
        },
    )
    connection.execute(
        "INSERT INTO message_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "m3",
            -1,
            "attachment",
            0,
            "image/jpeg",
            "pic.jpg",
            2048,
            640,
            480,
            0,
            "ab/att1.bin",
            "TABLE-SECRET-KEY",
            "TABLE-SECRET-DIGEST",
            "TABLE-SECRET-IV",
            "TABLE-SECRET-CDN",
        ),
    )
    add_message("m4", "conv-dm", "keychange", "", 400)
    add_message(
        "m5",
        "conv-dm",
        "incoming",
        "edited msg",
        500,
        source="+15557654321",
        service_id="uuid-alex",
        blob={
            "editHistory": [
                {"body": "v1", "timestamp": 1_784_280_000_450},
                {"body": "edited msg", "timestamp": 1_784_280_000_500},
            ]
        },
    )
    add_message(
        "m6",
        "conv-grp",
        "incoming",
        "evil path",
        600,
        source="+15550001111",
        service_id="uuid-bob",
        blob={"attachments": [{"contentType": "text/plain", "path": "../../../../etc/passwd"}]},
    )
    add_message("m8", "conv-grp", "incoming", "", 800, source="+15550001111", service_id="uuid-bob")
    connection.execute(
        "INSERT INTO message_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("m8", -1, "sticker", 0, "image/webp", "sticker.webp", 32, 128, 128, 0, "ab/sticker.bin", "K", "D", "I", "C"),
    )
    connection.commit()


def build_plain_signal_fixture(db_path: Path, attachments_root: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        populate_signal_fixture(connection, attachments_root)
    finally:
        connection.close()


def build_encrypted_signal_fixture(db_path: Path, attachments_root: Path, key_hex: str = TEST_KEY) -> None:
    if SQLCIPHER is None:
        raise unittest.SkipTest("sqlcipher unavailable")
    # Build a plaintext fixture in memory, dump only synthetic SQL, and feed the
    # key and fixture to sqlcipher over stdin. No real Signal data is involved.
    source = sqlite3.connect(":memory:")
    try:
        populate_signal_fixture(source, attachments_root)
        dump = "\n".join(source.iterdump())
    finally:
        source.close()
    script = (
        f"PRAGMA key = \"x'{key_hex}'\";\n"
        "PRAGMA cipher_compatibility = 4;\n"
        + dump
        + "\n"
    )
    result = subprocess.run(
        [SQLCIPHER, "-batch", "-bail", "-noinit", "--", str(db_path)],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError("failed to create synthetic SQLCipher fixture")


@unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography unavailable")
class KeyDerivationTests(unittest.TestCase):
    def test_v10_hex_and_base64_round_trip(self) -> None:
        password = b"synthetic-safe-storage-password"
        for encoding in ("hex", "base64"):
            config = {"encryptedKey": encrypt_config_key(password, TEST_KEY, encoding=encoding)}
            self.assertEqual(sd.derive_sqlcipher_key(config, password), TEST_KEY)

    def test_legacy_plaintext_key_does_not_need_password(self) -> None:
        self.assertEqual(sd.derive_sqlcipher_key({"key": TEST_KEY.upper()}, b""), TEST_KEY)

    def test_wrong_password_and_v11_fail_closed_without_secret(self) -> None:
        config = {"encryptedKey": encrypt_config_key(b"correct", TEST_KEY)}
        with self.assertRaises(sd.DecryptionError) as raised:
            sd.derive_sqlcipher_key(config, b"wrong-secret-password")
        self.assertNotIn(TEST_KEY, str(raised.exception))
        self.assertNotIn("wrong-secret-password", str(raised.exception))
        with self.assertRaises(sd.DecryptionError):
            sd.derive_sqlcipher_key(
                {"encryptedKey": encrypt_config_key(b"correct", TEST_KEY, prefix=b"v11")},
                b"correct",
            )

    def test_missing_or_malformed_key_fails_closed(self) -> None:
        for config in ({}, {"encryptedKey": "not an encoding"}, {"encryptedKey": "76313000"}):
            with self.assertRaises(sd.DecryptionError):
                sd.derive_sqlcipher_key(config, b"password")


class ConfigAndKeychainTests(unittest.TestCase):
    def test_bounded_config_reader_rejects_symlink_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "config.json"
            valid.write_text(json.dumps({"key": TEST_KEY}), encoding="utf-8")
            self.assertEqual(sd.read_signal_config(valid)["key"], TEST_KEY)
            link = root / "link.json"
            link.symlink_to(valid)
            with self.assertRaises(sd.SignalDesktopError):
                sd.read_signal_config(link)
            huge = root / "huge.json"
            huge.write_bytes(b"x" * (sd.MAX_CONFIG_BYTES + 1))
            with self.assertRaises(sd.SignalDesktopError):
                sd.read_signal_config(huge)

    def test_keychain_secret_is_captured_only_from_stdout(self) -> None:
        seen: dict[str, object] = {}

        def runner(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return FakeResult(0, b"safe-storage-secret\r\n", b"ignored")

        password = sd.read_safe_storage_password(
            keychain="/Users/test/Library/Keychains/login.keychain-db", runner=runner
        )
        self.assertEqual(password, bytearray(b"safe-storage-secret"))
        argv = seen["argv"]
        self.assertIn("Signal Safe Storage", argv)
        self.assertIn("Signal", argv)
        self.assertEqual(argv[-1], "/Users/test/Library/Keychains/login.keychain-db")
        self.assertNotIn("-g", argv)
        self.assertNotIn("env", seen["kwargs"])
        self.assertFalse(any("safe-storage-secret" in str(part) for part in argv))

    def test_keychain_errors_are_sanitized(self) -> None:
        runner = lambda *args, **kwargs: FakeResult(44, b"", b"private target +15551234567")
        with self.assertRaises(sd.KeychainError) as raised:
            sd.read_safe_storage_password(runner=runner)
        self.assertNotIn("+15551234567", str(raised.exception))

    @unittest.skipUnless(SQLCIPHER, "sqlcipher unavailable")
    def test_legacy_config_does_not_prompt_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signal_db = root / "encrypted.sqlite"
            attachments = root / "attachments.noindex"
            config = root / "config.json"
            build_encrypted_signal_fixture(signal_db, attachments)
            config.write_text(json.dumps({"key": TEST_KEY}), encoding="utf-8")
            keychain = mock.Mock(side_effect=AssertionError("Keychain must not be queried"))
            prepared = sd.prepare_signal_reader(
                config_path=config,
                signal_db=signal_db,
                attachments_root=attachments,
                sqlcipher_path=SQLCIPHER,
                keychain_runner=keychain,
            )
            prepared.clear()
            keychain.assert_not_called()


class SqlcipherProcessReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "encrypted.sqlite"
        self.db_path.touch()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def reader(self, runner, **kwargs):
        return sd.SqlcipherProcessReader(
            self.db_path,
            "ab" * 32,
            sqlcipher_path=SQLCIPHER or "/bin/sh",
            runner=runner,
            **kwargs,
        )

    def test_key_is_stdin_only_and_database_is_os_read_only(self) -> None:
        seen: dict[str, object] = {}

        def runner(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return FakeResult(0, '{"n":3}\n', "")

        self.assertEqual(self.reader(runner).query_json("SELECT json_object('n', 3)"), [{"n": 3}])
        argv = seen["argv"]
        kwargs = seen["kwargs"]
        key = "ab" * 32
        self.assertFalse(any(key in str(part) for part in argv))
        self.assertNotIn("env", kwargs)
        self.assertIn(f"x'{key}'", kwargs["input"])
        for flag in ("-readonly", "-nofollow", "-noinit", "-bail"):
            self.assertIn(flag, argv)
        self.assertIn("PRAGMA query_only = ON", kwargs["input"])
        self.assertIn("BEGIN;", kwargs["input"])

    def test_wrong_key_and_schema_failures_are_sanitized(self) -> None:
        secret = "private message and +15551234567"
        with self.assertRaises(sd.DecryptionError) as wrong:
            self.reader(lambda *a, **k: FakeResult(1, "", f"file is not a database {secret}"))\
                .query_json("SELECT json_object('n', 1)")
        self.assertNotIn(secret, str(wrong.exception))
        with self.assertRaises(sd.SchemaError) as schema:
            self.reader(lambda *a, **k: FakeResult(1, "", f"no such table {secret}"))\
                .query_json("SELECT json_object('n', 1)")
        self.assertNotIn(secret, str(schema.exception))

    def test_output_and_row_count_are_bounded(self) -> None:
        with self.assertRaises(sd.SchemaError):
            self.reader(
                lambda *a, **k: FakeResult(0, '{"body":"secret"}\n', ""),
                maximum_output_bytes=4,
            ).query_json("SELECT 1")

    def test_timeout_also_bounds_a_child_that_never_reads_stdin(self) -> None:
        started = time.monotonic()
        with self.assertRaises(sd.SignalDesktopError) as raised:
            sd._run_bounded_process(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                " " * (512 * 1024),
                timeout=0.1,
            )
        self.assertIn("timed out", str(raised.exception))
        self.assertLess(time.monotonic() - started, 2)

    def test_symlinked_database_is_rejected(self) -> None:
        link = self.db_path.with_name("link.sqlite")
        link.symlink_to(self.db_path)
        with self.assertRaises(sd.SignalDesktopError):
            sd.SqlcipherProcessReader(link, TEST_KEY, sqlcipher_path=SQLCIPHER or "/bin/sh")


@unittest.skipUnless(SQLCIPHER, "sqlcipher unavailable")
class RealSqlcipherIntegrationTests(unittest.TestCase):
    def test_reads_synthetic_encrypted_fixture_without_plaintext_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            encrypted = root / "fixture.sqlite"
            attachments = root / "attachments.noindex"
            build_encrypted_signal_fixture(encrypted, attachments)
            before = hashlib.sha256(encrypted.read_bytes()).digest()
            header = encrypted.read_bytes()[:16]
            self.assertNotEqual(header, b"SQLite format 3\x00")
            reader = sd.SqlcipherProcessReader(encrypted, TEST_KEY, sqlcipher_path=SQLCIPHER)
            try:
                sd.validate_reader(reader)
                schema = sd.inspect_schema(reader)
                self.assertIn("message_attachments", {"message_attachments" if schema.attachment_columns else ""})
            finally:
                reader.clear_key()
            self.assertEqual(hashlib.sha256(encrypted.read_bytes()).digest(), before)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["attachments.noindex", "fixture.sqlite"])

    def test_read_only_reader_sees_a_committed_live_wal_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            encrypted = root / "fixture.sqlite"
            attachments = root / "attachments.noindex"
            ready = root / "writer-ready"
            release = root / "writer-release"
            build_encrypted_signal_fixture(encrypted, attachments)
            wait_command = (
                f": > {shlex.quote(str(ready))}; "
                f"while [ ! -e {shlex.quote(str(release))} ]; do /bin/sleep 0.02; done"
            )
            script = (
                f"PRAGMA key = \"x'{TEST_KEY}'\";\n"
                "PRAGMA cipher_compatibility = 4;\n"
                "PRAGMA journal_mode = WAL;\n"
                "PRAGMA wal_autocheckpoint = 0;\n"
                "INSERT INTO messages(id,json,conversationId,type,body,sent_at,timestamp,received_at,received_at_ms) "
                "VALUES('wal-message','{}','conv-dm','incoming','committed in wal',"
                "1784280001000,1784280001000,1000,1784280001000);\n"
                f".shell /bin/sh -c {shlex.quote(wait_command)}\n"
            )
            writer = subprocess.Popen(
                [SQLCIPHER, "-batch", "-bail", "-noinit", "--", str(encrypted)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert writer.stdin is not None
                writer.stdin.write(script)
                writer.stdin.close()
                writer.stdin = None
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if writer.poll() is not None:
                        break
                    if time.monotonic() >= deadline:
                        self.fail("synthetic SQLCipher WAL writer did not become ready")
                    time.sleep(0.02)
                self.assertIsNone(writer.poll(), "synthetic SQLCipher WAL writer exited early")
                self.assertTrue(Path(str(encrypted) + "-wal").is_file())
                reader = sd.SqlcipherProcessReader(encrypted, TEST_KEY, sqlcipher_path=SQLCIPHER)
                try:
                    rows = reader.query_json(
                        "SELECT json_object('body', body) FROM messages WHERE id = 'wal-message'"
                    )
                finally:
                    reader.clear_key()
                self.assertEqual(rows, [{"body": "committed in wal"}])
            finally:
                release.touch()
                try:
                    writer.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    writer.kill()
                    writer.wait()
                if writer.stdout is not None:
                    writer.stdout.close()
                if writer.stderr is not None:
                    writer.stderr.close()


class IngestPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.signal_db = root / "signal.sqlite"
        self.attachments = root / "attachments.noindex"
        self.context_db = root / "context.sqlite3"
        build_plain_signal_fixture(self.signal_db, self.attachments)
        self.inbox = ContextInbox(self.context_db)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @contextlib.contextmanager
    def reader(self):
        connection = sqlite3.connect(self.signal_db)
        try:
            yield sd.Sqlite3Reader(connection)
        finally:
            connection.close()

    def ingest(self, **kwargs):
        with self.reader() as reader:
            return sd.ingest_signal_desktop(
                reader, self.inbox, attachments_root=self.attachments, **kwargs
            )

    def events(self) -> dict[str, dict]:
        return {event["source_message_id"]: event for event in self.inbox.list_events()}

    def test_maps_full_history_direction_conversation_sender_and_stable_ids(self) -> None:
        result = self.ingest(full=True, batch_limit=2)
        events = self.events()
        self.assertEqual(result.imported, 6)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.scanned, 7)
        inbound = events["signal-desktop:m1"]
        self.assertEqual(inbound["direction"], "inbound")
        self.assertEqual(inbound["conversation_type"], "dm")
        self.assertEqual(inbound["conversation_name"], "Alex")
        self.assertEqual(inbound["sender_display_name"], "Alex")
        outbound = events["signal-desktop:m2"]
        self.assertEqual(outbound["direction"], "outbound")
        self.assertEqual(outbound["sender_id"], "self")
        first_ids = {key: event["event_id"] for key, event in events.items()}
        self.ingest(full=True)
        self.assertEqual(first_ids, {key: event["event_id"] for key, event in self.events().items()})

    def test_normalized_and_legacy_attachment_metadata_strip_all_crypto(self) -> None:
        self.ingest(full=True)
        events = self.events()
        normalized = json.loads(events["signal-desktop:m3"]["attachment_metadata_json"])
        self.assertEqual(normalized[0]["attachment_type"], "attachment")
        self.assertEqual(normalized[0]["file_name"], "pic.jpg")
        self.assertTrue(normalized[0]["local_path"].endswith("attachments.noindex/ab/att1.bin"))
        traversal = json.loads(events["signal-desktop:m6"]["attachment_metadata_json"])
        self.assertEqual(traversal[0]["content_type"], "text/plain")
        self.assertNotIn("local_path", traversal[0])
        sticker = json.loads(events["signal-desktop:m8"]["attachment_metadata_json"])
        self.assertEqual(sticker[0]["attachment_type"], "sticker")
        stored = "\n".join(
            event["attachment_metadata_json"] + event["raw_json"] for event in events.values()
        )
        for secret in (
            "LEGACY-SECRET-KEY",
            "LEGACY-SECRET-DIGEST",
            "LEGACY-SECRET-IV",
            "TABLE-SECRET-KEY",
            "TABLE-SECRET-DIGEST",
            "TABLE-SECRET-IV",
            "TABLE-SECRET-CDN",
        ):
            self.assertNotIn(secret, stored)

    def test_quote_and_selected_edit_history_are_preserved_without_raw_message_json(self) -> None:
        self.ingest(full=True)
        events = self.events()
        quoted = events["signal-desktop:m3"]
        self.assertEqual(quoted["thread_id"], "1784270000000")
        quote_raw = json.loads(quoted["raw_json"])
        self.assertEqual(quote_raw["quote"]["author_id"], "aci-alex")
        self.assertEqual(quote_raw["quote"]["text"], "original text")
        edited = events["signal-desktop:m5"]
        self.assertIn("edit", json.loads(edited["flags"]))
        edit_raw = json.loads(edited["raw_json"])
        self.assertEqual(edit_raw["edit_count"], 2)
        self.assertEqual([item["body"] for item in edit_raw["edit_history"]], ["v1", "edited msg"])
        self.assertNotIn("attachments", quote_raw)

    def test_events_are_search_only_and_never_action_targets(self) -> None:
        self.ingest(full=True)
        for event in self.events().values():
            self.assertEqual(event["action_target_id"], "")
            self.assertEqual(event["action_account_id"], "")

    def test_full_ingest_is_idempotent_and_uses_batch_api(self) -> None:
        with mock.patch.object(self.inbox, "upsert_events", wraps=self.inbox.upsert_events) as batched:
            first = self.ingest(full=True, batch_limit=2)
            self.assertGreaterEqual(batched.call_count, 3)
        count = len(self.inbox.list_events())
        second = self.ingest(full=True)
        self.assertEqual(first.imported, second.imported)
        self.assertEqual(len(self.inbox.list_events()), count)

    def test_incremental_cursor_imports_new_rows_and_overlap_refreshes_edits(self) -> None:
        first = self.ingest(full=True, lookback_rows=3)
        connection = sqlite3.connect(self.signal_db)
        try:
            connection.execute(
                "UPDATE messages SET body='edited again', json=? WHERE id='m5'",
                (json.dumps({"editHistory": [{"body": "v1"}, {"body": "edited msg"}, {"body": "edited again"}]}),),
            )
            timestamp = 1_784_280_000_900
            connection.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "m9", "{}", "conv-dm", "incoming", "brand new", timestamp,
                    timestamp, 900, timestamp, "+15557654321", "uuid-alex",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        result = self.ingest(full=False, cursor=first.resume_cursor, lookback_rows=3)
        self.assertIn("signal-desktop:m9", self.events())
        self.assertEqual(self.events()["signal-desktop:m5"]["body"], "edited again")
        self.assertGreaterEqual(result.scanned, 3)

    def test_null_order_cursor_does_not_skip_later_non_null_rows(self) -> None:
        variant = Path(self.temporary.name) / "null-order.sqlite"
        connection = sqlite3.connect(variant)
        connection.execute(
            "CREATE TABLE messages("
            "id TEXT PRIMARY KEY, conversationId TEXT, type TEXT, body TEXT, "
            "timestamp INTEGER, received_at INTEGER)"
        )
        rows = (
            ("null-order", "conv-null", "incoming", "null order", 1_784_280_001_000, None),
            ("order-ten", "conv-null", "incoming", "order ten", 1_784_280_002_000, 10),
            ("order-twenty", "conv-null", "incoming", "order twenty", 1_784_280_003_000, 20),
        )
        connection.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?)", rows)
        connection.commit()
        reader = sd.Sqlite3Reader(connection)
        try:
            first = sd.ingest_signal_desktop(
                reader,
                self.inbox,
                attachments_root=None,
                full=True,
                lookback_rows=0,
                max_rows=1,
            )
            self.assertIsNotNone(first.cursor)
            assert first.cursor is not None
            self.assertEqual(first.cursor["order"], -1)
            second = sd.ingest_signal_desktop(
                reader,
                self.inbox,
                attachments_root=None,
                cursor=first.cursor,
                lookback_rows=0,
                max_rows=1,
            )
            self.assertIsNotNone(second.cursor)
            assert second.cursor is not None
            self.assertEqual(second.cursor["order"], 10)
            third = sd.ingest_signal_desktop(
                reader,
                self.inbox,
                attachments_root=None,
                cursor=second.cursor,
                lookback_rows=0,
                max_rows=1,
            )
            self.assertIsNotNone(third.cursor)
            assert third.cursor is not None
            self.assertEqual(third.cursor["order"], 20)
        finally:
            connection.close()
        events = self.events()
        self.assertIn("signal-desktop:null-order", events)
        self.assertIn("signal-desktop:order-ten", events)
        self.assertIn("signal-desktop:order-twenty", events)

    def test_schema_introspection_fails_closed_but_allows_missing_conversations(self) -> None:
        empty = Path(self.temporary.name) / "empty.sqlite"
        connection = sqlite3.connect(empty)
        connection.execute("CREATE TABLE unrelated(x)")
        with self.assertRaises(sd.SchemaError):
            sd.inspect_schema(sd.Sqlite3Reader(connection))
        connection.close()

        variant = Path(self.temporary.name) / "variant.sqlite"
        connection = sqlite3.connect(variant)
        connection.execute(
            "CREATE TABLE messages(id TEXT PRIMARY KEY, conversationId TEXT, type TEXT, body TEXT, timestamp INTEGER)"
        )
        connection.execute("INSERT INTO messages VALUES('v1','c','incoming','hi',1784280000000)")
        connection.commit()
        reader = sd.Sqlite3Reader(connection)
        result = sd.ingest_signal_desktop(reader, self.inbox, attachments_root=None)
        connection.close()
        self.assertEqual(result.imported, 1)
        self.assertEqual(self.events()["signal-desktop:v1"]["conversation_name"], "")

    def test_json_id_is_stable_and_missing_message_id_fails_closed(self) -> None:
        variant = Path(self.temporary.name) / "json-only.sqlite"
        connection = sqlite3.connect(variant)
        connection.execute("CREATE TABLE messages(json TEXT)")
        connection.execute(
            "INSERT INTO messages VALUES(?)",
            (json.dumps({
                "id": "json-v1",
                "conversationId": "json-conversation",
                "type": "incoming",
                "body": "from json",
                "timestamp": 1_784_280_000_000,
            }),),
        )
        connection.commit()
        reader = sd.Sqlite3Reader(connection)
        result = sd.ingest_signal_desktop(reader, self.inbox, attachments_root=None)
        self.assertEqual(result.imported, 1)
        self.assertIn("signal-desktop:json-v1", self.events())
        connection.execute(
            "UPDATE messages SET json=?",
            (json.dumps({
                "conversationId": "json-conversation",
                "type": "incoming",
                "body": "no stable id",
                "timestamp": 1_784_280_000_000,
            }),),
        )
        connection.commit()
        with self.assertRaises(sd.SchemaError):
            sd.ingest_signal_desktop(reader, self.inbox, attachments_root=None)
        connection.close()

    def test_attachment_cardinality_is_bounded_per_message(self) -> None:
        with self.assertRaises(sd.SchemaError):
            sd._legacy_attachments([{}] * (sd.MAX_ATTACHMENTS_PER_MESSAGE + 1), None)

        reader = mock.Mock()
        reader.query_json.return_value = [
            {"messageId": "many", "orderInMessage": index}
            for index in range(sd.MAX_ATTACHMENTS_PER_MESSAGE + 1)
        ]
        with self.assertRaises(sd.SchemaError):
            sd._normalized_attachments_by_message(
                reader,
                ("messageId", "orderInMessage"),
                ({"id": "many"}, {"id": "other"}),
                None,
            )

    def test_message_query_projects_json_subfields_not_whole_raw_json(self) -> None:
        with self.reader() as reader:
            schema = sd.inspect_schema(reader)
            query = sd._message_query(schema, None, 25)
        self.assertIn("json_extract(json, '$.quote')", query)
        self.assertIn("json_extract(json, '$.id')", query)
        self.assertNotIn("'json', json", query)
        self.assertIn("LIMIT 25", query)


class StateAndSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.signal_db = root / "signal.sqlite"
        self.attachments = root / "attachments.noindex"
        self.context_db = root / "context.sqlite3"
        self.state = root / "private" / "state.json"
        build_plain_signal_fixture(self.signal_db, self.attachments)
        self.connection = sqlite3.connect(self.signal_db)
        self.reader = sd.Sqlite3Reader(self.connection)
        self.schema = sd.inspect_schema(self.reader)
        self.prepared = sd.PreparedSignalReader(self.reader, self.schema, self.attachments.resolve())

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def test_first_run_is_full_then_incremental_with_private_secret_free_state(self) -> None:
        first, first_mode = sd.sync_signal_desktop(
            self.prepared,
            context_db=self.context_db,
            state_path=self.state,
            signal_db=self.signal_db,
            lookback_rows=0,
            now=1000,
        )
        second, second_mode = sd.sync_signal_desktop(
            self.prepared,
            context_db=self.context_db,
            state_path=self.state,
            signal_db=self.signal_db,
            lookback_rows=0,
            now=1100,
        )
        self.assertEqual(first_mode, "full")
        self.assertEqual(second_mode, "incremental")
        self.assertEqual(first.scanned, 7)
        self.assertEqual(second.scanned, 0)
        self.assertEqual(stat.S_IMODE(self.state.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        state_text = self.state.read_text(encoding="utf-8")
        self.assertNotIn(TEST_KEY, state_text)
        self.assertNotIn("hey there", state_text)
        self.assertNotIn("+1555", state_text)

    def test_periodic_reconciliation_and_source_replacement_force_full(self) -> None:
        sd.sync_signal_desktop(
            self.prepared,
            context_db=self.context_db,
            state_path=self.state,
            signal_db=self.signal_db,
            lookback_rows=0,
            full_rescan_days=1,
            now=1000,
        )
        periodic, mode = sd.sync_signal_desktop(
            self.prepared,
            context_db=self.context_db,
            state_path=self.state,
            signal_db=self.signal_db,
            lookback_rows=0,
            full_rescan_days=1,
            now=1000 + 86_401,
        )
        self.assertEqual(mode, "full")
        self.assertEqual(periodic.scanned, 7)

        replacement = Path(self.temporary.name) / "replacement.sqlite"
        replacement_attachments = Path(self.temporary.name) / "replacement-attachments.noindex"
        build_plain_signal_fixture(replacement, replacement_attachments)
        replacement_connection = sqlite3.connect(replacement)
        try:
            replacement_reader = sd.Sqlite3Reader(replacement_connection)
            replacement_prepared = sd.PreparedSignalReader(
                replacement_reader, sd.inspect_schema(replacement_reader), replacement_attachments.resolve()
            )
            result, replacement_mode = sd.sync_signal_desktop(
                replacement_prepared,
                context_db=self.context_db,
                state_path=self.state,
                signal_db=replacement,
                lookback_rows=0,
                now=200_000,
            )
        finally:
            replacement_connection.close()
        self.assertEqual(replacement_mode, "full")
        self.assertEqual(result.scanned, 7)

    def test_invalid_state_requires_explicit_full_recovery(self) -> None:
        self.state.parent.mkdir()
        self.state.write_text("not-json", encoding="utf-8")
        with self.assertRaises(sd.SignalDesktopError):
            sd.sync_signal_desktop(
                self.prepared,
                context_db=self.context_db,
                state_path=self.state,
                signal_db=self.signal_db,
                now=1000,
            )
        result, mode = sd.sync_signal_desktop(
            self.prepared,
            context_db=self.context_db,
            state_path=self.state,
            signal_db=self.signal_db,
            force_full=True,
            now=1000,
        )
        self.assertEqual(mode, "full")
        self.assertEqual(result.scanned, 7)

    def test_nonblocking_lock_prevents_overlapping_collectors(self) -> None:
        self.state.parent.mkdir()
        lock_path = self.state.with_name(self.state.name + ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(sd.CollectorBusyError):
                with sd.collector_lock(self.state):
                    pass
        finally:
            os.close(fd)


@unittest.skipUnless(HAVE_CRYPTOGRAPHY and SQLCIPHER, "cryptography/sqlcipher unavailable")
class CollectorEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.signal_db = self.root / "encrypted.sqlite"
        self.attachments = self.root / "attachments.noindex"
        self.config = self.root / "config.json"
        self.context_db = self.root / "context.sqlite3"
        self.state = self.root / "state" / "collector.json"
        self.password = b"fixture-safe-storage-password"
        build_encrypted_signal_fixture(self.signal_db, self.attachments)
        self.config.write_text(
            json.dumps({"encryptedKey": encrypt_config_key(self.password, TEST_KEY)}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def keychain_runner(self, argv, **kwargs):
        return FakeResult(0, self.password + b"\n", b"")

    def args(self) -> list[str]:
        return [
            "--config",
            str(self.config),
            "--signal-db",
            str(self.signal_db),
            "--attachments",
            str(self.attachments),
            "--context-db",
            str(self.context_db),
            "--state",
            str(self.state),
            "--sqlcipher",
            str(SQLCIPHER),
        ]

    def test_health_check_validates_every_layer_without_ingesting(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sd.main(self.args() + ["--check", "--json"], keychain_runner=self.keychain_runner)
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["mode"], "check")
        self.assertFalse(self.context_db.exists())
        self.assertFalse(self.state.exists())
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(self.password.decode("ascii"), output)
        self.assertNotIn(TEST_KEY, output)

    def test_initial_and_incremental_cli_sync_store_no_secrets(self) -> None:
        summaries = []
        for timestamp in (1000, 1100):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = sd.main(
                    self.args() + ["--json", "--lookback-rows", "0"],
                    keychain_runner=self.keychain_runner,
                    now=lambda value=timestamp: value,
                )
            self.assertEqual(code, 0, stderr.getvalue())
            summaries.append(json.loads(stdout.getvalue()))
        self.assertEqual([item["mode"] for item in summaries], ["full", "incremental"])
        self.assertEqual(len(ContextInbox(self.context_db).list_events()), 6)
        stored = self.context_db.read_bytes() + self.state.read_bytes()
        for secret in (self.password, TEST_KEY.encode("ascii"), b"TABLE-SECRET-KEY"):
            self.assertNotIn(secret, stored)

    def test_sqlcipher_diagnostic_body_is_never_logged(self) -> None:
        secret = "private-body +15551234567"

        def failed_sqlcipher(argv, **kwargs):
            return FakeResult(1, "", f"file is not a database {secret}")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sd.main(
                self.args() + ["--check", "--json"],
                keychain_runner=self.keychain_runner,
                sqlcipher_runner=failed_sqlcipher,
            )
        self.assertEqual(code, 1)
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(TEST_KEY, stdout.getvalue() + stderr.getvalue())


class EntrypointAndLaunchdTests(unittest.TestCase):
    def test_script_help_bootstraps_without_accessing_signal_or_keychain(self) -> None:
        result = subprocess.run(
            [sys.executable, str(Path.cwd() / "scripts" / "signal_desktop_collector.py"), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--login-keychain", result.stdout)
        self.assertIn("--check", result.stdout)

    def test_launchd_template_is_private_periodic_batch_not_keepalive(self) -> None:
        path = Path.cwd() / "launchd" / "com.example.hermes-second-brain.signal-desktop-history.plist.template"
        value = plistlib.loads(path.read_bytes())
        self.assertEqual(value["StartInterval"], 900)
        self.assertNotIn("KeepAlive", value)
        self.assertEqual(value["Umask"], 63)
        arguments = value["ProgramArguments"]
        self.assertEqual(arguments[0], "__HERMES_PYTHON__")
        self.assertIn("__PROJECT_DIR__/scripts/signal_desktop_collector.py", arguments)
        self.assertIn("--login-keychain", arguments)
        self.assertIn("__SQLCIPHER__", arguments)


if __name__ == "__main__":
    unittest.main()
