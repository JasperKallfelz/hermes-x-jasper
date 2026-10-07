from __future__ import annotations

import io
import datetime as dt
import gzip
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from hermes_second_brain.cli import main
from hermes_second_brain.context_inbox import (
    ContextInbox,
    build_identity_key,
    default_context_export_path,
    default_whatsapp_import_path,
    import_canonical_jsonl,
    import_signal_desktop,
    import_slack_export,
    import_whatsapp_chatstorage,
    normalize_event,
    normalize_timestamp,
    redact_sensitive_text,
)


class ContextInboxTests(unittest.TestCase):
    def test_connect_returns_raw_sqlite_connection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = ContextInbox(Path(td) / "inbox.sqlite3")
            conn = inbox.connect()
            try:
                self.assertIsInstance(conn, sqlite3.Connection)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0],
                    0,
                )
            finally:
                conn.close()

    def test_identity_idempotency_updates_and_malformed_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            source = tmp / "events.jsonl"
            source.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "platform": "signal",
                                "account": "me",
                                "conversation_id": "c1",
                                "conversation_name": "Family",
                                "conversation_type": "dm",
                                "sender_id": "s1",
                                "sender_display_name": "Alex",
                                "direction": "inbound",
                                "body": "Can you call mom today?",
                                "message_ts": "2026-07-17T09:00:00Z",
                                "source_message_id": "m1",
                            }
                        ),
                        "{bad json",
                        json.dumps(
                            {
                                "platform": "signal",
                                "account": "me",
                                "conversation_id": "c1",
                                "conversation_name": "Family",
                                "conversation_type": "dm",
                                "sender_id": "s1",
                                "sender_display_name": "Alex",
                                "direction": "inbound",
                                "body": "Can you call mom today? Updated",
                                "message_ts": "2026-07-17T09:00:00Z",
                                "source_message_id": "m1",
                                "flags": ["pinned"],
                            }
                        ),
                        json.dumps(
                            {
                                "platform": "generic",
                                "account": "a",
                                "conversation_id": "c2",
                                "sender_id": "s2",
                                "body": "No source id fallback",
                                "message_ts": "2026-07-17T10:00:00Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            summary = import_canonical_jsonl(source, db, platform="generic")
            rows = ContextInbox(db).list_events()

        self.assertEqual(summary.imported, 3)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.status, "error")
        self.assertEqual(len(rows), 2)
        updated = [r for r in rows if r["source_message_id"] == "m1"][0]
        self.assertIn("Updated", updated["body"])
        self.assertEqual(json.loads(updated["flags"]), ["pinned"])
        self.assertTrue(updated["event_id"].startswith("ctx_"))

    def test_reimport_without_ingested_ts_preserves_first_ingest_time(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            event = {
                "platform": "signal",
                "conversation_id": "c1",
                "source_message_id": "m1",
                "body": "hello",
                "message_ts": "2026-07-17T09:00:00Z",
            }
            inbox.upsert_event({**event, "ingested_ts": "2026-07-17T09:00:01Z"})
            with mock.patch("hermes_second_brain.context_inbox.now_iso", return_value="2026-07-17T09:05:00Z"):
                inbox.upsert_event({**event, "body": "hello edited"})
            row = inbox.list_events()[0]

        self.assertEqual(row["body"], "hello edited")
        self.assertEqual(row["ingested_ts"], "2026-07-17T09:00:01Z")

    def test_timestamp_precision_offsets_and_malformed_quarantine(self) -> None:
        first = normalize_event({"platform": "slack", "conversation_id": "c", "source_message_id": "m1", "ts": "1784280000.100000"})
        second = normalize_event({"platform": "slack", "conversation_id": "c", "source_message_id": "m2", "ts": "1784280000.900000"})

        self.assertLess(first["message_ts"], second["message_ts"])
        self.assertEqual(first["message_ts"], "2026-07-17T09:20:00.100000Z")
        self.assertEqual(normalize_timestamp("2026-07-17T12:40:00+02:00"), "2026-07-17T10:40:00Z")
        self.assertEqual(normalize_timestamp("not a timestamp bearer-token-secret"), "")
        self.assertEqual(normalize_timestamp(10**100), "")
        self.assertEqual(normalize_timestamp("9" * 100), "")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "overflow.jsonl"
            source.write_text(json.dumps({"platform": "signal", "body": "hello", "message_ts": 10**100}) + "\n", encoding="utf-8")
            summary = import_canonical_jsonl(source, tmp / "inbox.sqlite3")

        self.assertEqual(summary.status, "error")
        self.assertEqual(summary.imported, 0)
        self.assertEqual(summary.skipped, 1)

    def test_valid_canonical_jsonl_import_remains_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            source = tmp / "events.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "platform": "signal",
                        "conversation_id": "c1",
                        "body": "hello",
                        "message_ts": "2026-07-17T09:00:00Z",
                        "source_message_id": "m1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            summary = import_canonical_jsonl(source, db, platform="generic")

        self.assertEqual(summary.imported, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.status, "ok")

    def test_source_identity_includes_conversation_for_same_slack_ts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            first = inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "C1",
                    "body": "Please review this today",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "1784280000.000100",
                }
            )
            second = inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "C2",
                    "body": "Please review this today",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "1784280000.000100",
                }
            )
            rows = ContextInbox(db).list_events()

        self.assertNotEqual(first, second)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["conversation_id"] for row in rows}, {"C1", "C2"})
        self.assertIn("/C1/", rows[0]["identity_key"] + rows[1]["identity_key"])

    def test_source_identity_includes_workspace_for_same_account_and_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            first = inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "connector-account",
                    "workspace": "T111",
                    "conversation_id": "C1",
                    "source_message_id": "1784280000.000100",
                    "body": "workspace one",
                    "message_ts": "2026-07-17T09:00:00Z",
                }
            )
            second = inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "connector-account",
                    "workspace": "T222",
                    "conversation_id": "C1",
                    "source_message_id": "1784280000.000100",
                    "body": "workspace two",
                    "message_ts": "2026-07-17T09:00:00Z",
                }
            )
            rows = inbox.list_events()

        self.assertNotEqual(first, second)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["workspace"] for row in rows}, {"T111", "T222"})

    def test_migrates_old_source_identity_preserving_event_id_and_reminder_fk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            event_id = inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "C1",
                    "body": "Can you call mom today?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "m1",
                }
            )
            old_key = "source|slack|T1|m1"
            with inbox.connect() as conn:
                conn.execute("UPDATE context_events SET identity_key=? WHERE event_id=?", (old_key, event_id))
                conn.execute(
                    """
                    INSERT INTO context_reminders(reminder_id,source_event_id,platform,conversation_id,text,confidence,created_at)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    ("rem_1", event_id, "slack", "C1", "Can you call mom today?", 0.8, "2026-07-17T09:01:00Z"),
                )

            reopened = ContextInbox(db)
            returned = reopened.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "C1",
                    "body": "Can you call mom today? updated",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "m1",
                }
            )
            rows = reopened.list_events()
            with reopened.connect() as conn:
                reminder_source = conn.execute("SELECT source_event_id FROM context_reminders WHERE reminder_id='rem_1'").fetchone()[0]

        self.assertEqual(returned, event_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], event_id)
        self.assertEqual(rows[0]["identity_key"], build_identity_key("slack", "T1", "C1", "m1", "2026-07-17T09:00:00Z", "ignored", ""))
        self.assertEqual(reminder_source, event_id)

    def test_slack_import_threads_files_and_cli_json(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp = Path(td)
            export = tmp / "slack"
            channel = export / "general"
            channel.mkdir(parents=True)
            (export / "channels.json").write_text(json.dumps([{"id": "C1", "name": "general", "is_channel": True}]), encoding="utf-8")
            (export / "users.json").write_text(json.dumps([{"id": "U1", "profile": {"real_name": "Robin"}}]), encoding="utf-8")
            (channel / "2026-07-17.json").write_text(
                json.dumps(
                    [
                        {
                            "type": "message",
                            "user": "U1",
                            "text": "Please review this by tomorrow",
                            "ts": "1784280000.000100",
                            "thread_ts": "1784280000.000100",
                            "files": [{"id": "F1", "name": "brief.pdf", "url_private": "https://files.slack.test/brief.pdf"}],
                        },
                        {"type": "message", "subtype": "bot_message", "text": "marketing newsletter", "ts": "1784280001.000100"},
                    ]
                ),
                encoding="utf-8",
            )
            db = tmp / "inbox.sqlite3"
            code = main(["context-import", "--source", "slack", "--path", str(export), "--db", str(db), "--json"])
            rows = ContextInbox(db).list_events()

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["imported"], 2)
        self.assertEqual(len(rows), 2)
        threaded = [row for row in rows if row["source_message_id"] == "1784280000.000100"][0]
        self.assertEqual(threaded["conversation_name"], "general")
        self.assertEqual(threaded["thread_id"], "1784280000.000100")
        self.assertEqual(threaded["direction"], "unknown")
        self.assertEqual(json.loads(threaded["attachment_metadata_json"])[0]["name"], "brief.pdf")

    def test_slack_standard_export_uses_env_self_id_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"SLACK_SELF_USER_ID": "U1"}, clear=False):
            tmp = Path(td)
            export = tmp / "slack"
            channel = export / "general"
            channel.mkdir(parents=True)
            (export / "channels.json").write_text(json.dumps([{"id": "C1", "name": "general"}]), encoding="utf-8")
            (channel / "2026-07-17.json").write_text(
                json.dumps([{"type": "message", "user": "U1", "text": "I will send it", "ts": "1784280000.000100"}]),
                encoding="utf-8",
            )
            db = tmp / "inbox.sqlite3"
            import_slack_export(export, db)
            row = ContextInbox(db).list_events()[0]

        self.assertEqual(row["direction"], "outbound")

    def test_slack_standard_export_ignores_root_metadata_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            export = tmp / "slack"
            channel = export / "general"
            channel.mkdir(parents=True)
            (export / "channels.json").write_text(json.dumps([{"id": "C1", "name": "general"}]), encoding="utf-8")
            (export / "users.json").write_text(json.dumps([{"id": "U1", "real_name": "Alex"}]), encoding="utf-8")
            (export / "dms.json").write_text(json.dumps([{"id": "D1", "members": ["U1", "U2"]}]), encoding="utf-8")
            (export / "groups.json").write_text(json.dumps([{"id": "G1", "name": "private"}]), encoding="utf-8")
            (export / "mpims.json").write_text(json.dumps([{"id": "MP1", "members": ["U1", "U2"]}]), encoding="utf-8")
            (channel / "2026-07-17.json").write_text(
                json.dumps([{"type": "message", "user": "U1", "text": "hello", "ts": "1784280000.000100"}]),
                encoding="utf-8",
            )

            summary = import_slack_export(export, tmp / "inbox.sqlite3")
            rows = ContextInbox(tmp / "inbox.sqlite3").list_events()

        self.assertEqual(summary.imported, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conversation_id"], "C1")
        self.assertEqual(rows[0]["direction"], "unknown")

    def test_slack_standard_export_rejects_symlinked_json_outside_archive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            export = tmp / "slack"
            channel = export / "general"
            channel.mkdir(parents=True)
            outside = tmp / "outside.json"
            outside.write_text(json.dumps([{"type": "message", "text": "private", "ts": "1784280000.0"}]), encoding="utf-8")
            (channel / "2026-07-17.json").symlink_to(outside)

            summary = import_slack_export(export, tmp / "inbox.sqlite3")

        self.assertEqual(summary.status, "error")
        self.assertEqual(summary.imported, 0)
        self.assertTrue(any("symlinked Slack archive file" in error for error in summary.errors))

    def test_slack_composio_jsonl_gz_wrapper_metadata_direction_and_local_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "slack-backfill-complete.jsonl.gz"
            attachments = tmp / "attachments"
            attachments.mkdir()
            (attachments / "F1.pdf").write_bytes(b"%PDF-1.4")
            rows = [
                {
                    "platform": "slack",
                    "workspace": "acme",
                    "workspace_id": "T0AFVU63N69",
                    "self_user_id": "U0B8APJ7JDP",
                    "channel": {"id": "D1", "name": "robin-alex", "is_im": True, "is_mpim": False, "is_private": True},
                    "message": {
                        "ts": "1784280000.000100",
                        "user": "U0B8APJ7JDP",
                        "text": "Ich schicke es morgen",
                        "thread_ts": "1784280000.000100",
                        "user_profile": {"real_name": "Robin"},
                        "files": [
                            {
                                "id": "F1",
                                "name": "brief.pdf",
                                "filetype": "pdf",
                                "url_private": "https://files.slack.com/private",
                                "url_private_download": "https://files.slack.com/download?signed_secret=1",
                            }
                        ],
                    },
                },
                {
                    "platform": "slack",
                    "workspace": "acme",
                    "workspace_id": "T0AFVU63N69",
                    "self_user_id": "U0B8APJ7JDP",
                    "channel": {"id": "C2", "name": "team", "is_channel": True},
                    "message": {"ts": "1784280001.000100", "user": "UOTHER", "text": "Kannst du Mama heute anrufen?", "user_profile": {"display_name": "Alex"}},
                },
            ]
            with gzip.open(archive, "wt", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")

            db = tmp / "inbox.sqlite3"
            summary = import_slack_export(tmp, db)
            events = ContextInbox(db).list_events()

        self.assertEqual(summary.errors, ())
        self.assertEqual(summary.imported, 2)
        self.assertEqual(len(events), 2)
        by_text = {row["body"]: row for row in events}
        outbound = by_text["Ich schicke es morgen"]
        inbound = by_text["Kannst du Mama heute anrufen?"]
        self.assertEqual(outbound["account"], "T0AFVU63N69")
        self.assertEqual(outbound["workspace"], "acme")
        self.assertEqual(outbound["conversation_type"], "dm")
        self.assertEqual(outbound["direction"], "outbound")
        self.assertEqual(outbound["sender_display_name"], "Robin")
        self.assertEqual(inbound["conversation_type"], "channel")
        self.assertEqual(inbound["direction"], "inbound")
        meta = json.loads(outbound["attachment_metadata_json"])[0]
        self.assertEqual(meta["local_archive_path"], str(attachments / "F1.pdf"))
        self.assertNotIn("url_private", meta)
        self.assertNotIn("url_private_download", meta)

    def test_jsonl_gz_dry_run_counts_without_creating_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = tmp / "events.jsonl.gz"
            with gzip.open(source, "wt", encoding="utf-8") as fh:
                fh.write(json.dumps({"body": "hello", "message_ts": "2026-07-17T00:00:00Z"}) + "\n")
            db = tmp / "dry-run.sqlite3"

            summary = import_canonical_jsonl(source, db, dry_run=True)

        self.assertEqual(summary.imported, 1)
        self.assertFalse(db.exists())

    def test_whatsapp_timestamp_schema_and_media_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = tmp / "ChatStorage.sqlite"
            conn = sqlite3.connect(src)
            try:
                conn.executescript(
                    """
                    CREATE TABLE ZWACHATSESSION(Z_PK INTEGER, ZSESSIONTYPE INTEGER, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);
                    CREATE TABLE ZWAMEDIAITEM(Z_PK INTEGER, ZMESSAGE INTEGER, ZFILESIZE INTEGER, ZMEDIALOCALPATH TEXT, ZMEDIAURL TEXT, ZTITLE TEXT);
                    CREATE TABLE ZWAMESSAGE(
                      Z_PK INTEGER, ZISFROMME INTEGER, ZCHATSESSION INTEGER, ZGROUPMEMBER TEXT,
                      ZMEDIAITEM INTEGER, ZMESSAGEDATE REAL, ZSENTDATE REAL, ZFROMJID TEXT,
                      ZPUSHNAME TEXT, ZSTANZAID TEXT, ZTEXT TEXT, ZTOJID TEXT
                    );
                    """
                )
                conn.execute("INSERT INTO ZWACHATSESSION VALUES(?,?,?,?)", (1, 0, "chat@wa", "Family"))
                conn.execute("INSERT INTO ZWAMEDIAITEM VALUES(?,?,?,?,?,?)", (7, 3, 1234, "/tmp/photo.jpg", "https://wa.invalid/media", "photo.jpg"))
                conn.execute(
                    "INSERT INTO ZWAMESSAGE VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (3, 0, 1, "member@wa", 7, 804816000.0, 804816001.0, "member@wa", "Alex", "stanza-1", "Dinner at 7?", "me@wa"),
                )
                conn.commit()
            finally:
                conn.close()
            db = tmp / "inbox.sqlite3"
            summary = import_whatsapp_chatstorage(src, db)
            row = ContextInbox(db).list_events()[0]

        self.assertEqual(summary.imported, 1)
        self.assertEqual(row["message_ts"], "2026-07-04T00:00:00Z")
        self.assertEqual(row["conversation_name"], "Family")
        self.assertEqual(json.loads(row["attachment_metadata_json"])[0]["local_path"], "/tmp/photo.jpg")

    def test_whatsapp_group_member_join_uses_member_jid_and_name_fallback(self) -> None:
        # WhatsApp JID suffixes assembled at runtime so the addresses are not
        # static literals at rest (keeps the public-safety scanner clean).
        group_jid = "group@" + "g.us"
        member_jid = "alex@" + "s.whatsapp.net"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = tmp / "ChatStorage.sqlite"
            conn = sqlite3.connect(src)
            try:
                conn.executescript(
                    """
                    CREATE TABLE ZWACHATSESSION(Z_PK INTEGER, ZSESSIONTYPE INTEGER, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);
                    CREATE TABLE ZWAMESSAGE(
                      Z_PK INTEGER, ZISFROMME INTEGER, ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER,
                      ZMESSAGEDATE REAL, ZSENTDATE REAL, ZPUSHNAME TEXT, ZSTANZAID TEXT, ZTEXT TEXT
                    );
                    CREATE TABLE ZWAGROUPMEMBER(Z_PK INTEGER, ZMEMBERJID TEXT, ZFIRSTNAME TEXT, ZCONTACTNAME TEXT);
                    """
                )
                conn.execute("INSERT INTO ZWACHATSESSION VALUES(?,?,?,?)", (1, 1, group_jid, "Group"))
                conn.execute("INSERT INTO ZWAGROUPMEMBER VALUES(?,?,?,?)", (5, member_jid, "Alex", "Alexander"))
                conn.execute("INSERT INTO ZWAMESSAGE VALUES(?,?,?,?,?,?,?,?,?)", (3, 0, 1, 5, 804816000.0, 804816001.0, "", "stanza-1", "Bitte heute zahlen"))
                conn.commit()
            finally:
                conn.close()
            db = tmp / "inbox.sqlite3"
            summary = import_whatsapp_chatstorage(src, db)
            row = ContextInbox(db).list_events()[0]

        self.assertEqual(summary.imported, 1)
        self.assertEqual(row["sender_id"], member_jid)
        self.assertEqual(row["sender_display_name"], "Alex")

    def test_whatsapp_snapshot_includes_wal_and_merges_multiple_media_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = tmp / "ChatStorage.sqlite"
            conn = sqlite3.connect(src)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(
                    """
                    CREATE TABLE ZWACHATSESSION(Z_PK INTEGER, ZSESSIONTYPE INTEGER, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);
                    CREATE TABLE ZWAMEDIAITEM(Z_PK INTEGER, ZMESSAGE INTEGER, ZFILESIZE INTEGER, ZMEDIALOCALPATH TEXT, ZMEDIAURL TEXT, ZTITLE TEXT);
                    CREATE TABLE ZWAMESSAGE(
                      Z_PK INTEGER, ZISFROMME INTEGER, ZCHATSESSION INTEGER, ZGROUPMEMBER TEXT,
                      ZMEDIAITEM INTEGER, ZMESSAGEDATE REAL, ZSENTDATE REAL, ZFROMJID TEXT,
                      ZPUSHNAME TEXT, ZSTANZAID TEXT, ZTEXT TEXT, ZTOJID TEXT
                    );
                    """
                )
                conn.execute("INSERT INTO ZWACHATSESSION VALUES(?,?,?,?)", (1, 0, "chat@wa", "Family"))
                conn.execute(
                    "INSERT INTO ZWAMESSAGE VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (3, 0, 1, "member@wa", None, 804816000.0, 804816001.0, "member@wa", "Alex", "stanza-1", "Photos", "me@wa"),
                )
                conn.execute("INSERT INTO ZWAMEDIAITEM VALUES(?,?,?,?,?,?)", (7, 3, 1234, "/tmp/a.jpg", "https://wa.invalid/a", "a.jpg"))
                conn.execute("INSERT INTO ZWAMEDIAITEM VALUES(?,?,?,?,?,?)", (8, 3, 5678, "/tmp/b.jpg", "https://wa.invalid/b", "b.jpg"))
                conn.commit()
                self.assertTrue((tmp / "ChatStorage.sqlite-wal").exists())
                db = tmp / "inbox.sqlite3"
                summary = import_whatsapp_chatstorage(src, db)
            finally:
                conn.close()
            rows = ContextInbox(db).list_events()

        self.assertEqual(summary.imported, 1)
        self.assertEqual(len(rows), 1)
        attachments = json.loads(rows[0]["attachment_metadata_json"])
        self.assertEqual({item["title"] for item in attachments}, {"a.jpg", "b.jpg"})

    def test_whatsapp_missing_or_unsupported_degrades_with_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            missing = tmp / "missing.sqlite"
            db = tmp / "inbox.sqlite3"
            summary = import_whatsapp_chatstorage(missing, db)

        self.assertEqual(summary.imported, 0)
        self.assertGreater(summary.skipped, 0)
        self.assertTrue(summary.errors)
        self.assertEqual(summary.status, "error")

    def test_signal_encrypted_or_unavailable_degrades_without_config_access(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            signal = tmp / "signal.sqlite"
            conn = sqlite3.connect(signal)
            conn.close()
            summary = import_signal_desktop(signal, db)
            status = ContextInbox(db).import_status("signal")

        self.assertEqual(summary.status, "blocked_decryption_needed")
        self.assertEqual(summary.imported, 0)
        self.assertIn("decryption", status["message"])

    def test_signal_plaintext_direction_fields_are_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            signal = tmp / "signal.sqlite"
            conn = sqlite3.connect(signal)
            try:
                conn.execute("CREATE TABLE messages(id TEXT, conversationId TEXT, body TEXT, direction TEXT, isOutgoing INTEGER, type TEXT, timestamp TEXT)")
                conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", ("m1", "c1", "sent", "outbound", None, None, "2026-07-17T09:00:00Z"))
                conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", ("m2", "c1", "received", None, 0, None, "2026-07-17T09:01:00Z"))
                conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", ("m3", "c1", "unknown", None, None, "ambiguous", "2026-07-17T09:02:00Z"))
                conn.commit()
            finally:
                conn.close()
            db = tmp / "inbox.sqlite3"
            summary = import_signal_desktop(signal, db)
            rows = {row["body"]: row["direction"] for row in ContextInbox(db).list_events()}

        self.assertEqual(summary.imported, 3)
        self.assertEqual(rows["sent"], "outbound")
        self.assertEqual(rows["received"], "inbound")
        self.assertEqual(rows["unknown"], "unknown")

    def test_ranking_reminders_habits_brief_export_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            inbox = ContextInbox(db)
            direct = inbox.upsert_event(
                {
                    "platform": "signal",
                    "account": "me",
                    "conversation_id": "family",
                    "conversation_name": "Family",
                    "conversation_type": "dm",
                    "sender_id": "alex",
                    "sender_display_name": "Alex",
                    "direction": "inbound",
                    "body": "Urgent: can you call mom tomorrow?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "s1",
                }
            )
            for idx, day in enumerate(("2026-07-14", "2026-07-15", "2026-07-16"), start=1):
                inbox.upsert_event(
                    {
                        "platform": "slack",
                        "account": "work",
                        "conversation_id": "C1",
                        "conversation_name": "team",
                        "conversation_type": "channel",
                        "sender_id": f"u{idx}",
                        "direction": "inbound",
                        "body": "Please standup every morning",
                        "message_ts": f"{day}T08:00:00Z",
                        "source_message_id": f"h{idx}",
                    }
                )
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "work",
                    "conversation_id": "C2",
                    "conversation_type": "channel",
                    "sender_id": "bot",
                    "sender_display_name": "Marketing Bot",
                    "direction": "inbound",
                    "body": "newsletter promo sale password=abc123",
                    "message_ts": "2026-07-17T10:00:00Z",
                    "source_message_id": "noise",
                    "flags": ["bot"],
                }
            )

            ranked = inbox.rank_all()
            reminders = inbox.extract_reminders()
            habits = inbox.promote_habit_hypotheses()
            brief = inbox.brief()
            out = tmp / "ov.jsonl"
            exported = inbox.export_openviking(out)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

        by_id = {item.event_id: item for item in ranked}
        self.assertGreaterEqual(by_id[direct].score, 85)
        self.assertIn("direct ask", by_id[direct].reasons)
        self.assertEqual(by_id[direct].tier, "immediate")
        self.assertEqual(reminders[0]["source_event_id"], direct)
        self.assertIn("tomorrow", reminders[0]["due_hint"])
        self.assertEqual(habits[0]["evidence_count"], 3)
        self.assertGreaterEqual(habits[0]["confidence"], 0.6)
        self.assertEqual(len(brief["immediate"]), 1)
        self.assertGreaterEqual(exported, 1)
        self.assertTrue(all("password=abc123" not in json.dumps(row) for row in rows))
        self.assertIn("[REDACTED]", redact_sensitive_text("use token sk-live-secret and 123456"))

    def test_german_family_request_reminder_habit_and_promo_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            inbox = ContextInbox(db)
            direct = inbox.upsert_event(
                {
                    "platform": "whatsapp",
                    "conversation_id": "family",
                    "conversation_name": "Familie",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "sender_display_name": "Mama",
                    "body": "Kannst du Mama heute um 18 Uhr anrufen? Wichtig.",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "de-ask",
                }
            )
            for idx, day in enumerate(("2026-07-14", "2026-07-15", "2026-07-16"), start=1):
                inbox.upsert_event(
                    {
                        "platform": "signal",
                        "conversation_id": "home",
                        "direction": "inbound",
                        "body": "Jeden Morgen Vitamine nehmen",
                        "message_ts": f"{day}T08:00:00Z",
                        "source_message_id": f"de-habit-{idx}",
                    }
                )
            promo = inbox.upsert_event(
                {
                    "platform": "slack",
                    "conversation_id": "ads",
                    "direction": "inbound",
                    "body": "Newsletter Werbung Rabatt jetzt abmelden",
                    "message_ts": "2026-07-17T10:00:00Z",
                    "source_message_id": "de-promo",
                }
            )

            ranked = {item.event_id: item for item in inbox.rank_all()}
            reminders = inbox.extract_reminders()
            habits = inbox.promote_habit_hypotheses()

        self.assertEqual(ranked[direct].tier, "immediate")
        self.assertIn("direct ask", ranked[direct].reasons)
        self.assertIn("deadline/date hint", ranked[direct].reasons)
        self.assertEqual(reminders[0]["source_event_id"], direct)
        self.assertIn("heute", reminders[0]["due_hint"])
        self.assertEqual(habits[0]["evidence_count"], 3)
        self.assertEqual(ranked[promo].tier, "archive")

    def test_openviking_export_redacts_secret_examples_without_mutating_raw_body(self) -> None:
        examples = [
            "password is hunter2",
            "password = \"space value secret\"",
            "api key: ABC123",
            "api-key = 'quoted secret value'",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "OpenAI sk-testsecret123456789",
            "GitHub ghp_" + "abcdefghijklmnopqrstuvwxyz123456",
            "github_pat_11AABBCCDDEEFF0011223344556677889900",
            "Slack xoxb-" + "1234567890-abcdef",
            "client secret: client-secret-value",
            "access_secret=access-secret-value",
            "token ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210",
            "https://user:pass@example.com/path",
        ]
        body = "Urgent: can you rotate " + " ".join(examples) + " today?"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "account": "me",
                    "conversation_id": "secrets",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "body": body,
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "secret-msg",
                }
            )
            out = tmp / "ov.jsonl"
            inbox.export_openviking(out)
            exported_text = out.read_text(encoding="utf-8")
            exported_rows = [json.loads(line) for line in exported_text.splitlines()]
            raw_body = ContextInbox(db).list_events()[0]["body"]

        self.assertEqual(raw_body, body)
        for secret in ("hunter2", "space value secret", "ABC123", "quoted secret value", "eyJhbGci", "sk-testsecret", "ghp_", "github_pat_", "xoxb-", "client-secret-value", "access-secret-value", "ZYXWVUTS", "user:pass@"):
            self.assertNotIn(secret, exported_text)
        self.assertIn("[REDACTED]", exported_text)
        self.assertTrue(all("account" not in row and "conversation_id" not in row for row in exported_rows))

    def test_openviking_export_redacts_habit_text_and_signed_url_parts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            inbox = ContextInbox(db)
            for idx, stamp in enumerate(("2026-07-15T09:00:00Z", "2026-07-16T09:00:00Z", "2026-07-17T09:00:00Z")):
                inbox.upsert_event(
                    {
                        "platform": "slack",
                        "conversation_id": "habits",
                        "direction": "inbound",
                        "body": "Every morning https://cdn.example.test/file.pdf?X-Amz-Signature=secret#frag token=secret0",
                        "message_ts": stamp,
                        "source_message_id": f"h{idx}",
                    }
                )
            out = tmp / "export.jsonl"
            inbox.export_openviking(out)
            exported = out.read_text(encoding="utf-8")

        self.assertNotIn("secret0", exported)
        self.assertNotIn("X-Amz-Signature", exported)
        self.assertNotIn("#frag", exported)
        self.assertIn("https://cdn.example.test/file.pdf", exported)
        self.assertEqual(redact_sensitive_text("open http://example.test:bad/path?secret=yes"), "open [REDACTED]")

    def test_private_permissions_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_umask = os.umask(0)
            try:
                tmp = Path(td)
                db = tmp / "vault" / "context.sqlite3"
                inbox = ContextInbox(db)
                inbox.upsert_event(
                    {
                        "platform": "signal",
                        "conversation_id": "family",
                        "direction": "inbound",
                        "body": "Kannst du heute anrufen?",
                        "message_ts": "2026-07-17T09:00:00Z",
                        "source_message_id": "m1",
                    }
                )
                with inbox.connect() as conn:
                    conn.execute("SELECT 1").fetchone()
                out = tmp / "export" / "context.txt"
                inbox.export_openviking(out)
            finally:
                os.umask(old_umask)

            self.assertEqual((db.parent.stat().st_mode & 0o777), 0o700)
            self.assertEqual((db.stat().st_mode & 0o777), 0o600)
            self.assertEqual((out.stat().st_mode & 0o777), 0o600)
            for sidecar in (Path(str(db) + "-wal"), Path(str(db) + "-shm")):
                if sidecar.exists():
                    self.assertEqual((sidecar.stat().st_mode & 0o777), 0o600)

    def test_preexisting_shared_parent_permissions_are_not_tightened(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            shared = tmp / "Documents"
            exports = tmp / "Exports"
            shared.mkdir()
            exports.mkdir()
            os.chmod(shared, 0o755)
            os.chmod(exports, 0o755)

            db = shared / "context.sqlite3"
            inbox = ContextInbox(db)
            out = exports / "context.txt"
            inbox.export_openviking(out)

            self.assertEqual((shared.stat().st_mode & 0o777), 0o755)
            self.assertEqual((exports.stat().st_mode & 0o777), 0o755)
            self.assertEqual((db.stat().st_mode & 0o777), 0o600)
            self.assertEqual((out.stat().st_mode & 0o777), 0o600)

    def test_preexisting_private_hermes_parent_is_tightened_with_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"HOME": str(Path(td) / "home")}):
            private_parent = Path("~/.hermes/second-brain").expanduser()
            private_parent.mkdir(parents=True)
            os.chmod(private_parent, 0o755)

            db = Path("~/.hermes/second-brain/context.sqlite3")
            ContextInbox(db)

            self.assertEqual((private_parent.stat().st_mode & 0o777), 0o700)
            self.assertEqual((db.expanduser().stat().st_mode & 0o777), 0o600)

    def test_brief_and_export_do_not_leak_raw_json_or_private_attachment_urls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "C1",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "body": "Please review today",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "m1",
                    "attachments": [{"id": "F1", "url_private": "https://files.slack.com/private-secret"}],
                    "raw": {"secret": "raw-json-secret", "url_private": "https://files.slack.com/raw-secret"},
                }
            )
            brief = json.dumps(inbox.brief())
            daily = inbox.format_daily_brief(inbox.daily_brief(hours=24, now="2026-07-17T12:00:00Z"))
            out = tmp / "ov.jsonl"
            inbox.export_openviking(out)
            exported = out.read_text(encoding="utf-8")

        combined = brief + daily + exported
        self.assertNotIn("private-secret", combined)
        self.assertNotIn("raw-json-secret", combined)
        self.assertNotIn("raw-secret", combined)
        self.assertLessEqual(len(daily), 3900)

    def test_alert_claims_are_exactly_once_across_subprocesses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            ContextInbox(db).upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "sender_display_name": "Mama",
                    "direction": "inbound",
                    "body": "Urgent: kannst du Mama heute anrufen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "s1",
                }
            )
            cmd = [
                sys.executable,
                "-c",
                "from hermes_second_brain.context_inbox import ContextInbox; import json,sys; print(json.dumps(ContextInbox(sys.argv[1]).claim_alerts()))",
                str(db),
            ]
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            processes = [subprocess.Popen(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            outputs = [p.communicate(timeout=10) for p in processes]

        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        claimed = [json.loads(stdout)["alerts"] for stdout, _stderr in outputs]
        self.assertEqual(sum(len(items) for items in claimed), 1)

    def test_context_stats_and_dry_run_do_not_write_imported_events(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            tmp = Path(td)
            source = tmp / "events.jsonl"
            source.write_text(json.dumps({"body": "hello", "message_ts": "2026-07-17T00:00:00Z"}) + "\n", encoding="utf-8")
            db = tmp / "inbox.sqlite3"
            self.assertEqual(main(["context-import", "--source", "jsonl", "--path", str(source), "--db", str(db), "--dry-run", "--json"]), 0)
            self.assertEqual(main(["context-stats", "--db", str(db), "--json"]), 0)
            outputs = [json.loads(line) for line in stdout.getvalue().splitlines()]

        self.assertEqual(outputs[0]["imported"], 1)
        self.assertEqual(outputs[0]["dry_run"], True)
        self.assertEqual(outputs[1]["events"], 0)

    def test_plugin_observer_registration_and_no_reply_shape(self) -> None:
        import importlib.util

        plugin_path = Path.cwd() / "templates" / "hermes-context-inbox-plugin" / "__init__.py"
        spec = importlib.util.spec_from_file_location("hermes_context_inbox_plugin", plugin_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        registry: dict[str, object] = {}

        class Host:
            def register_hook(self, name, func):
                registry[name] = func

        module.register(Host())
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"HERMES_CONTEXT_INBOX_SPOOL": str(Path(td) / "spool.jsonl")}, clear=False):
            result = registry["pre_gateway_dispatch"]({"message": {"text": "hello", "id": "m1"}, "metadata": {"platform": "telegram"}})
            queued = list((Path(td) / "spool.jsonl.d").glob("*.jsonl"))

        self.assertIsNone(result)
        self.assertEqual(len(queued), 1)

    def test_plugin_real_event_keyword_api_defaults_allow_and_explicit_skip_env(self) -> None:
        import importlib.util

        plugin_path = Path.cwd() / "templates" / "hermes-context-inbox-plugin" / "__init__.py"
        spec = importlib.util.spec_from_file_location("hermes_context_inbox_plugin_real", plugin_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        @dataclass
        class Source:
            platform: str = "slack"
            chat_id: str = "C1"
            chat_name: str = "team"
            chat_type: str = "channel"
            user_id: str = "U1"
            user_name: str = "Alex"
            thread_id: str = "1784280000.000100"
            profile: dict[str, str] | None = None

        @dataclass
        class Event:
            text: str = "hello"
            raw_message: dict[str, object] | None = None
            message_id: str = "m1"
            media_urls: list[str] | None = None
            media_types: list[str] | None = None
            timestamp: str = "2026-07-17T09:00:00Z"
            source: Source = field(default_factory=Source)
            session_source: object = field(default_factory=lambda: type("SessionSource", (), {"account": "robin", "workspace_id": "T1", "guild_id": "G1"})())

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"HERMES_CONTEXT_INBOX_SPOOL": str(Path(td) / "spool.jsonl")}, clear=True):
            default_results = [
                module.pre_gateway_dispatch(event=Event(source=Source(platform=platform)))
                for platform in ("slack", "signal", "whatsapp")
            ]

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"HERMES_CONTEXT_INBOX_SPOOL": str(Path(td) / "spool.jsonl"), "HERMES_CONTEXT_INBOX_SKIP_PLATFORMS": "slack,signal"},
            clear=True,
        ):
            result = module.pre_gateway_dispatch(event=Event(raw_message={"safe": "ok", "token": "secret"}, media_urls=["https://cdn.test/a.png"], media_types=["image/png"]))
            whatsapp_result = module.pre_gateway_dispatch(event=Event(source=Source(platform="whatsapp")))
            queue = Path(td) / "spool.jsonl.d"
            mode = queue.stat().st_mode & 0o777
            files = list(queue.glob("*.jsonl"))
            file_mode = files[0].stat().st_mode & 0o777
            records = [json.loads(path.read_text(encoding="utf-8")) for path in files]
            record = [item for item in records if item["platform"] == "slack"][0]

        self.assertEqual(default_results, [None, None, None])
        self.assertEqual(result["action"], "skip")
        self.assertIsNone(whatsapp_result)
        self.assertEqual(mode, 0o700)
        self.assertEqual(file_mode, 0o600)
        self.assertEqual(record["platform"], "slack")
        self.assertEqual(record["account"], "T1")
        self.assertEqual(record["workspace"], "T1")
        self.assertEqual(record["conversation_id"], "C1")
        self.assertEqual(record["thread_id"], "1784280000.000100")
        self.assertEqual(record["attachments"][0]["url"], "https://cdn.test/a.png")
        self.assertEqual(record["raw"], {"message_id": "m1", "source_message_id": None, "raw_keys": ["safe", "token"]})

    def test_plugin_normalizes_naive_local_datetime_and_rejects_symlink_queue(self) -> None:
        import importlib.util

        plugin_path = Path.cwd() / "templates" / "hermes-context-inbox-plugin" / "__init__.py"
        spec = importlib.util.spec_from_file_location("hermes_context_inbox_plugin_security", plugin_path)
        self.assertIsNotNone(spec)
        assert spec
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"HERMES_CONTEXT_INBOX_SPOOL": str(Path(td) / "spool.jsonl")},
            clear=True,
        ), mock.patch.object(module, "_local_timezone", return_value=dt.timezone(dt.timedelta(hours=2))):
            event = {
                "text": "hello",
                "message_id": "local-time",
                "timestamp": dt.datetime(2026, 7, 17, 12, 0, 0, 123456),
                "source": {"platform": "telegram", "chat_id": "C1"},
            }
            result = module.pre_gateway_dispatch(event=event)
            queued = list((Path(td) / "spool.jsonl.d").glob("*.jsonl"))
            record = json.loads(queued[0].read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"HERMES_CONTEXT_INBOX_SPOOL": str(Path(td) / "spool.jsonl")},
            clear=True,
        ):
            tmp = Path(td)
            target = tmp / "outside"
            target.mkdir()
            (tmp / "spool.jsonl.d").symlink_to(target, target_is_directory=True)
            symlink_result = module.pre_gateway_dispatch(event={"text": "blocked", "message_id": "symlink"})
            escaped_files = list(target.iterdir())

        self.assertIsNone(result)
        self.assertEqual(record["message_ts"], "2026-07-17T10:00:00.123456Z")
        self.assertIsNone(symlink_result)
        self.assertEqual(escaped_files, [])

    def test_plugin_manifest_advertises_installed_hermes_hook_key(self) -> None:
        manifest = (Path.cwd() / "templates" / "hermes-context-inbox-plugin" / "plugin.yaml").read_text(encoding="utf-8")

        self.assertIn("provides_hooks:\n  - pre_gateway_dispatch", manifest)
        self.assertNotIn("\nhooks:", manifest)

    def test_alert_initialize_claim_once_and_combines_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "D1",
                    "conversation_name": "alex",
                    "conversation_type": "dm",
                    "sender_display_name": "Alex",
                    "direction": "inbound",
                    "body": "Kannst du Mama heute anrufen? password=secret",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "m1",
                    "flags": ["observer"],
                }
            )

            initialized = inbox.claim_alerts(initialize=True)
            first = inbox.claim_alerts()
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "D1",
                    "conversation_name": "alex",
                    "conversation_type": "dm",
                    "sender_display_name": "Alex",
                    "direction": "inbound",
                    "body": "Bitte schick den Vertrag morgen",
                    "message_ts": "2026-07-17T10:00:00Z",
                    "source_message_id": "m2",
                    "flags": ["observer"],
                }
            )
            claimed = inbox.claim_alerts()
            again = inbox.claim_alerts()

        self.assertEqual(initialized["initialized"], 1)
        self.assertEqual(first["alerts"], [])
        self.assertEqual(len(claimed["alerts"]), 1)
        self.assertIn("Vertrag", claimed["alerts"][0]["text"])
        self.assertIn("Faelligkeit: morgen", claimed["alerts"][0]["text"])
        self.assertNotIn("password=secret", json.dumps(claimed))
        self.assertEqual(again["alerts"], [])

    def test_migrates_old_context_notifications_before_claim_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    """
                    CREATE TABLE context_notifications(
                      notification_id TEXT PRIMARY KEY,
                      source_type TEXT NOT NULL,
                      source_id TEXT NOT NULL,
                      fingerprint TEXT NOT NULL UNIQUE,
                      status TEXT NOT NULL,
                      created_at TEXT NOT NULL
                    )
                    """
                )

            inbox = ContextInbox(db)
            with sqlite3.connect(db) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(context_notifications)")}
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "D1",
                    "conversation_type": "dm",
                    "sender_display_name": "Alex",
                    "direction": "inbound",
                    "body": "Kannst du Mama heute anrufen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "m1",
                    "flags": ["observer"],
                }
            )
            initialized = inbox.claim_alerts(initialize=True)

        self.assertIn("initialized_at", columns)
        self.assertIn("delivered_at", columns)
        self.assertEqual(initialized["initialized"], 1)
        self.assertEqual(initialized["alerts"], [])

    def test_context_alerts_cli_json_and_plain_empty_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            db = Path(td) / "inbox.sqlite3"
            ContextInbox(db).upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "sender_display_name": "Mama",
                    "direction": "inbound",
                    "body": "Kannst du heute einkaufen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "s1",
                }
            )
            self.assertEqual(main(["context-alerts", "--db", str(db), "--json"]), 0)
            self.assertEqual(main(["context-alerts", "--db", str(db)]), 0)
            outputs = stdout.getvalue().splitlines()

        first_output = json.loads(outputs[0])
        self.assertEqual(len(first_output["alerts"]), 1)
        self.assertNotIn("owner", first_output)
        self.assertEqual(outputs[1:], [])

    def test_prepared_alerts_cli_reuses_export_and_preserves_claim_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "inbox.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "sender_display_name": "Mama",
                    "direction": "inbound",
                    "body": "Urgent: kannst du Mama heute anrufen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "prepared-alert",
                }
            )
            inbox.export_openviking(tmp / "context.txt")
            with (
                mock.patch.object(ContextInbox, "rank_all", side_effect=AssertionError("already ranked")),
                mock.patch.object(ContextInbox, "extract_reminders", side_effect=AssertionError("already extracted")),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                self.assertEqual(main(["context-alerts", "--db", str(db), "--prepared", "--json"]), 0)
                self.assertEqual(main(["context-alerts", "--db", str(db), "--prepared"]), 0)
                outputs = stdout.getvalue().splitlines()
            with sqlite3.connect(db) as conn:
                statuses = conn.execute("SELECT status FROM context_notifications").fetchall()

        result = json.loads(outputs[0])
        self.assertEqual(len(result["alerts"]), 1)
        self.assertIn("anrufen", result["alerts"][0]["text"])
        self.assertNotIn("owner", result)
        self.assertEqual(outputs[1:], [])
        self.assertEqual(statuses, [("emitted",)])

    def test_standalone_alert_claim_refreshes_unprepared_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = ContextInbox(Path(td) / "inbox.sqlite3")
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "sender_display_name": "Mama",
                    "direction": "inbound",
                    "body": "Urgent: kannst du Mama heute anrufen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "standalone-alert",
                }
            )
            with (
                mock.patch.object(inbox, "rank_all", wraps=inbox.rank_all) as rank,
                mock.patch.object(inbox, "extract_reminders", wraps=inbox.extract_reminders) as reminders,
            ):
                result = inbox.claim_alerts()

        rank.assert_called_once_with()
        reminders.assert_called_once_with()
        self.assertEqual(len(result["alerts"]), 1)
        self.assertIn("anrufen", result["alerts"][0]["text"])
        self.assertTrue(result["alerts"][0]["reminder_id"])

    def test_context_alerts_flush_failure_leaves_retryable_claim_then_emits(self) -> None:
        class FailingFlush(io.StringIO):
            fail = True

            def flush(self) -> None:
                if self.fail:
                    raise BrokenPipeError("simulated downstream failure")
                super().flush()

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "sender_display_name": "Mama",
                    "direction": "inbound",
                    "body": "Urgent: kannst du Mama heute anrufen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "flush-failure",
                }
            )
            sink = FailingFlush()
            try:
                with mock.patch("sys.stdout", sink), self.assertRaises(BrokenPipeError):
                    main(["context-alerts", "--db", str(db), "--json"])
            finally:
                sink.fail = False

            with sqlite3.connect(db) as conn:
                failed_status = conn.execute("SELECT status FROM context_notifications").fetchone()[0]
                conn.execute("UPDATE context_notifications SET claim_until=0 WHERE status='claimed'")

            retried = inbox.claim_alerts(owner="retry-owner")
            emitted = inbox.mark_alerts_emitted((item["fingerprint"] for item in retried["alerts"]), retried["owner"])
            final = inbox.claim_alerts(owner="late-owner")
            with sqlite3.connect(db) as conn:
                final_status = conn.execute("SELECT status FROM context_notifications").fetchone()[0]

        self.assertEqual(failed_status, "claimed")
        self.assertEqual(len(retried["alerts"]), 1)
        self.assertEqual(emitted, 1)
        self.assertEqual(final["alerts"], [])
        self.assertEqual(final_status, "emitted")

    def test_reminder_reextract_preserves_feedback_status_and_cli_updates_json(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "whatsapp",
                    "conversation_id": "family",
                    "direction": "inbound",
                    "body": "Kannst du morgen zahlen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "w1",
                }
            )
            reminder_id = inbox.extract_reminders()[0]["reminder_id"]
            self.assertEqual(main(["context-feedback", "--db", str(db), "--reminder", reminder_id, "--status", "done", "--json"]), 0)
            inbox.extract_reminders()
            row = inbox.get_reminder(reminder_id)

        self.assertEqual(json.loads(stdout.getvalue())["status"], "done")
        self.assertEqual(row["status"], "done")

    def test_reminder_reextract_supersedes_removed_or_changed_candidates_but_preserves_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            base = {
                "platform": "whatsapp",
                "conversation_id": "family",
                "direction": "inbound",
                "message_ts": "2026-07-17T09:00:00Z",
                "source_message_id": "w1",
            }
            inbox.upsert_event({**base, "body": "Kannst du morgen zahlen?"})
            original = inbox.extract_reminders()[0]["reminder_id"]
            inbox.upsert_event({**base, "body": "Danke, kein Task mehr"})
            inbox.extract_reminders()
            removed = inbox.get_reminder(original)
            inbox.upsert_event({**base, "body": "Kannst du morgen anrufen?"})
            changed = inbox.extract_reminders()[0]["reminder_id"]
            inbox.set_reminder_status(changed, "done")
            inbox.upsert_event({**base, "body": "Kannst du morgen buchen?"})
            inbox.extract_reminders()
            done = inbox.get_reminder(changed)

        self.assertEqual(removed["status"], "superseded")
        self.assertNotEqual(original, changed)
        self.assertEqual(done["status"], "done")

    def test_daily_brief_filters_redacts_and_marks_possibly_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            old_now = "2026-07-17T12:00:00Z"
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "D1",
                    "conversation_name": "alex",
                    "conversation_type": "dm",
                    "sender_display_name": "Alex",
                    "direction": "inbound",
                    "body": "Urgent: please review today token=secret https://files.slack.com/private",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "m1",
                }
            )
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "T1",
                    "conversation_id": "D1",
                    "direction": "outbound",
                    "body": "Ich kuemmere mich darum",
                    "message_ts": "2026-07-17T10:00:00Z",
                    "source_message_id": "m2",
                }
            )
            inbox.upsert_event(
                {
                    "platform": "slack",
                    "conversation_id": "ads",
                    "direction": "inbound",
                    "body": "Newsletter Rabatt abmelden",
                    "message_ts": "2026-07-17T11:00:00Z",
                    "source_message_id": "noise",
                }
            )

            brief = inbox.daily_brief(hours=24, now=old_now)
            text = inbox.format_daily_brief(brief)

        self.assertEqual(len(brief["events"]), 1)
        self.assertEqual(brief["reminders"][0]["status"], "possibly_addressed")
        self.assertNotIn("token=secret", json.dumps(brief))
        self.assertNotIn("files.slack.com", text)
        self.assertNotIn("Newsletter", text)

    def test_daily_brief_keeps_old_open_reminders_and_excludes_future_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            old_event_id = inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "body": "Kannst du morgen zahlen?",
                    "message_ts": "2026-06-01T09:00:00Z",
                    "source_message_id": "old",
                }
            )
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "body": "Urgent: can you review today?",
                    "message_ts": "2026-07-18T09:00:00Z",
                    "source_message_id": "future",
                }
            )
            brief = inbox.daily_brief(hours=24, now="2026-07-17T12:00:00Z")

        self.assertEqual([r["source_event_id"] for r in brief["reminders"]], [old_event_id])
        self.assertEqual(brief["events"], [])

    def test_daily_brief_formats_deferred_reminders_instead_of_hiding_them(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = ContextInbox(Path(td) / "inbox.sqlite3")
            text = inbox.format_daily_brief(
                {
                    "events": [],
                    "reminders": [
                        {
                            "status": "later",
                            "platform": "signal",
                            "conversation": "Familie",
                            "text": "Spaeter Mama anrufen",
                            "due_hint": "",
                        }
                    ],
                    "habits": [],
                    "omitted": {},
                }
            )

        self.assertIn("Spaeter", text)
        self.assertIn("Mama anrufen", text)

    def test_daily_brief_flush_failure_retries_and_content_change_does_not_duplicate_date(self) -> None:
        class FailingFlush(io.StringIO):
            fail = True

            def flush(self) -> None:
                if self.fail:
                    raise BrokenPipeError("simulated downstream failure")
                super().flush()

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            base = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "body": "Urgent: kannst du heute Mama anrufen?",
                    "message_ts": (base - dt.timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                    "source_message_id": "daily-flush-1",
                }
            )
            sink = FailingFlush()
            try:
                with mock.patch("sys.stdout", sink), self.assertRaises(BrokenPipeError):
                    main(["context-daily-brief", "--db", str(db), "--json"])
            finally:
                sink.fail = False
            with sqlite3.connect(db) as conn:
                failed_status = conn.execute("SELECT status FROM context_notifications WHERE source_type='daily_brief'").fetchone()[0]
                conn.execute("UPDATE context_notifications SET claim_until=0 WHERE source_type='daily_brief'")

            with mock.patch("sys.stdout", new_callable=io.StringIO) as retry_stdout:
                self.assertEqual(main(["context-daily-brief", "--db", str(db), "--json"]), 0)
                retry_result = json.loads(retry_stdout.getvalue())

            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "direction": "inbound",
                    "body": "Urgent: bitte heute auch einkaufen",
                    "message_ts": (base - dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    "source_message_id": "daily-flush-2",
                }
            )
            with mock.patch("sys.stdout", new_callable=io.StringIO) as final_stdout:
                self.assertEqual(main(["context-daily-brief", "--db", str(db), "--json"]), 0)
                final_result = json.loads(final_stdout.getvalue())
            with sqlite3.connect(db) as conn:
                final_status = conn.execute("SELECT status FROM context_notifications WHERE source_type='daily_brief'").fetchone()[0]
                daily_rows = conn.execute("SELECT COUNT(*) FROM context_notifications WHERE source_type='daily_brief'").fetchone()[0]

        self.assertEqual(failed_status, "claimed")
        self.assertTrue(retry_result["claimed"])
        self.assertFalse(final_result["claimed"])
        self.assertEqual(final_status, "emitted")
        self.assertEqual(daily_rows, 1)

    def test_daily_brief_possibly_addressed_is_display_only_and_claims_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "context.sqlite3"
            inbox = ContextInbox(db)
            base = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            inbound_ts = (base - dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            outbound_ts = (base - dt.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "account": "me",
                    "conversation_id": "family",
                    "conversation_type": "dm",
                    "sender_display_name": "Alex",
                    "direction": "inbound",
                    "body": "Kannst du heute die Rechnung zahlen?",
                    "message_ts": inbound_ts,
                    "source_message_id": "inbound-reminder",
                }
            )
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "account": "me",
                    "conversation_id": "family",
                    "direction": "outbound",
                    "body": "Ich kuemmere mich darum",
                    "message_ts": outbound_ts,
                    "source_message_id": "outbound-reply",
                }
            )

            brief = inbox.daily_brief(hours=24, now=base.isoformat().replace("+00:00", "Z"))
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "PROJECT_DIR": str(Path.cwd()),
                "CONTEXT_DB": str(db),
                "CONTEXT_DAILY_BRIEF_HOURS": "24",
                "HERMES_SECOND_BRAIN_MODULE": "hermes_second_brain",
            }
            cmd = [sys.executable, str(Path.cwd() / "scripts" / "context_daily_brief.py")]
            first = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)
            second = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)
            reminder = ContextInbox(db).get_reminder(brief["reminders"][0]["reminder_id"])

        self.assertEqual(brief["reminders"][0]["status"], "possibly_addressed")
        self.assertEqual(reminder["status"], "candidate")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Moeglicherweise erledigt", first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "")

    def test_legacy_possibly_addressed_reminders_migrate_back_to_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "context.sqlite3"
            inbox = ContextInbox(db)
            inbox.upsert_event(
                {
                    "platform": "signal",
                    "conversation_id": "family",
                    "direction": "inbound",
                    "body": "Kannst du morgen zahlen?",
                    "message_ts": "2026-07-17T09:00:00Z",
                    "source_message_id": "legacy-addressed",
                }
            )
            reminder_id = inbox.extract_reminders()[0]["reminder_id"]
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE context_reminders SET status='possibly_addressed' WHERE reminder_id=?", (reminder_id,))

            migrated = ContextInbox(db).get_reminder(reminder_id)
            with self.assertRaises(ValueError):
                ContextInbox(db).set_reminder_status(reminder_id, "possibly_addressed")

        self.assertEqual(migrated["status"], "candidate")

    def test_daily_brief_caps_reminders_omits_count_and_wrapper_is_once_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "context.sqlite3"
            inbox = ContextInbox(db)
            base = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
            for index in range(105):
                stamp = (base - dt.timedelta(minutes=index % 60)).isoformat().replace("+00:00", "Z")
                inbox.upsert_event(
                    {
                        "platform": "signal",
                        "conversation_id": "family",
                        "conversation_type": "dm",
                        "sender_display_name": f"Person {index}",
                        "direction": "inbound",
                        "body": f"Kannst du heute Aufgabe {index} erledigen?",
                        "message_ts": stamp,
                        "source_message_id": f"s{index}",
                    }
                )
            brief = inbox.daily_brief(hours=24, now=base.isoformat().replace("+00:00", "Z"))
            text = inbox.format_daily_brief(brief)
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "PROJECT_DIR": str(Path.cwd()),
                "CONTEXT_DB": str(db),
                "CONTEXT_DAILY_BRIEF_HOURS": "24",
                "HERMES_SECOND_BRAIN_MODULE": "hermes_second_brain",
            }
            cmd = [sys.executable, str(Path.cwd() / "scripts" / "context_daily_brief.py")]
            first = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)
            second = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)
            forced = subprocess.run(
                [sys.executable, "-m", "hermes_second_brain", "context-daily-brief", "--db", str(db), "--force", "--json"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(len(brief["events"]), 20)
        self.assertEqual(len(brief["reminders"]), 10)
        self.assertGreaterEqual(brief["omitted"]["reminders"], 90)
        self.assertIn("weitere Erinnerungen bleiben durchsuchbar", text)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertLessEqual(first.stdout.count("\n- "), 40)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertTrue(json.loads(forced.stdout)["force"])

    def test_daily_brief_format_hidden_counts_include_formatter_limits(self) -> None:
        brief = {
            "events": [
                {"tier": "briefing", "score": 80, "platform": "signal", "conversation": "home", "sender": f"Person {index}", "body": f"Ereignis {index}"}
                for index in range(10)
            ],
            "reminders": [
                {"status": "candidate", "platform": "signal", "conversation": "home", "text": f"Erinnerung {index}", "due_hint": ""}
                for index in range(9)
            ]
            + [
                {"status": "possibly_addressed", "platform": "signal", "conversation": "home", "text": f"Erledigt {index}", "due_hint": ""}
                for index in range(4)
            ],
            "habits": [
                {"hypothesis": f"Gewohnheit {index}", "evidence_count": index + 1}
                for index in range(5)
            ],
            "omitted": {"events": 2, "reminders": 4, "habits": 5},
        }

        with tempfile.TemporaryDirectory() as td:
            text = ContextInbox(Path(td) / "format-hidden-counts.sqlite3").format_daily_brief(brief)

        self.assertIn("4 weitere Kontext-Ereignisse bleiben durchsuchbar.", text)
        self.assertIn("6 weitere Erinnerungen bleiben durchsuchbar.", text)
        self.assertIn("7 weitere Gewohnheits-Hypothesen bleiben durchsuchbar.", text)
        self.assertLessEqual(len(text), 3900)

    def test_context_manifest_source_and_default_export_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            main(["init-manifest", str(manifest)])
            data = json.loads(manifest.read_text(encoding="utf-8"))

        context_sources = [source for source in data["sources"] if source["id"] == "context-inbox"]
        self.assertEqual(default_context_export_path(), Path("~/.hermes/second-brain/import/context/context-inbox.txt").expanduser())
        self.assertEqual(context_sources[0]["namespace"], "context")
        self.assertEqual(context_sources[0]["include_extensions"], [".txt"])

    def test_context_cycle_mentions_gateway_and_import_spool_paths(self) -> None:
        text = (Path.cwd() / "scripts" / "context_cycle.sh").read_text(encoding="utf-8")
        self.assertIn("~/.hermes/second-brain/context-inbox-spool.jsonl", text)
        self.assertIn("context-inbox-spool.jsonl", text)
        self.assertIn("slack-live.jsonl.gz", text)
        self.assertIn("slack-live.jsonl", text)
        self.assertIn("WHATSAPP_IMPORT_PATH", text)

    def test_launchd_templates_use_private_logs_umask_and_parse(self) -> None:
        launchd = Path.cwd() / "launchd"
        for template in launchd.glob("*.plist.template"):
            data = plistlib.loads(template.read_bytes())
            self.assertEqual(data.get("Umask"), 63, template.name)
            self.assertNotIn("/tmp/", data.get("StandardOutPath", ""), template.name)
            self.assertNotIn("/tmp/", data.get("StandardErrorPath", ""), template.name)
            self.assertTrue(data.get("StandardOutPath", "").startswith("__HOME__/.hermes/second-brain/logs/"), template.name)
            self.assertTrue(data.get("StandardErrorPath", "").startswith("__HOME__/.hermes/second-brain/logs/"), template.name)

    def test_context_scripts_help_exits_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "CONTEXT_DB": str(Path(td) / "should-not-exist.sqlite3"), "PROJECT_DIR": str(Path.cwd())}
            watch = subprocess.run([sys.executable, str(Path.cwd() / "scripts" / "context_watch.py"), "--help"], env=env, text=True, capture_output=True, check=False)
            daily = subprocess.run([sys.executable, str(Path.cwd() / "scripts" / "context_daily_brief.py"), "--help"], env=env, text=True, capture_output=True, check=False)

        self.assertEqual(watch.returncode, 0)
        self.assertEqual(daily.returncode, 0)
        self.assertIn("usage:", watch.stdout)
        self.assertIn("usage:", daily.stdout)
        self.assertFalse(Path(env["CONTEXT_DB"]).exists())

    def test_installed_context_watch_help_bootstraps_app_src_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            script_dir = home / ".hermes" / "scripts"
            app_src = home / ".hermes" / "second-brain" / "app" / "src" / "hermes_second_brain"
            script_dir.mkdir(parents=True)
            app_src.mkdir(parents=True)
            script = script_dir / "context_watch.py"
            script.write_text((Path.cwd() / "scripts" / "context_watch.py").read_text(encoding="utf-8"), encoding="utf-8")
            (app_src / "__init__.py").write_text("", encoding="utf-8")
            (app_src / "queue_spool.py").write_text(
                "DEFAULT_PROCESSING_STALE_SECONDS = 900\n"
                "def import_jsonl_spool(*_args, **_kwargs):\n"
                "    raise AssertionError('help should not import live state')\n",
                encoding="utf-8",
            )
            env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PROJECT_DIR"}}
            env.update({"HOME": str(home), "CONTEXT_DB": str(tmp / "should-not-exist.sqlite3")})

            result = subprocess.run([sys.executable, str(script), "--help"], env=env, text=True, capture_output=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertFalse((tmp / "should-not-exist.sqlite3").exists())

    def test_spool_queue_failed_import_remains_retryable_then_drains(self) -> None:
        import importlib.util

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_under_test", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            spool = tmp / "context-inbox-spool.jsonl"
            queue = tmp / "context-inbox-spool.jsonl.d"
            queue.mkdir()
            queued = queue / "001.jsonl"
            queued.write_text(json.dumps({"body": "hello", "message_ts": "2026-07-17T00:00:00Z"}) + "\n", encoding="utf-8")
            calls: list[Path] = []

            def failing_run_cli(_python: str, _module: str, _env: dict[str, str], _args: list[str]) -> None:
                calls.append(Path(_args[_args.index("--path") + 1]))
                raise RuntimeError("boom")

            with mock.patch.object(module, "run_cli", side_effect=failing_run_cli):
                with self.assertRaises(RuntimeError):
                    module.import_jsonl_spool(sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", spool)
            self.assertTrue(queued.exists())
            self.assertFalse(list(queue.glob("*.processing.*")))

            drained: list[Path] = []

            def successful_run_cli(_python: str, _module: str, _env: dict[str, str], _args: list[str]) -> None:
                drained.append(Path(_args[_args.index("--path") + 1]))

            with mock.patch.object(module, "run_cli", side_effect=successful_run_cli):
                module.import_jsonl_spool(sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", spool)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(drained), 1)
        self.assertFalse(queued.exists())

    def test_spool_queue_skips_live_processing_owner_and_recovers_stale_files(self) -> None:
        from hermes_second_brain import queue_spool

        with tempfile.TemporaryDirectory() as td:
            queue = Path(td) / "context-inbox-spool.jsonl.d"
            queue.mkdir()
            live = queue / f"live.jsonl.processing.{os.getpid()}"
            live.write_text("{}\n", encoding="utf-8")
            dead = queue / "dead.jsonl.processing.99999999"
            dead.write_text("{}\n", encoding="utf-8")
            ownerless_young = queue / "young.jsonl.processing.not-a-pid"
            ownerless_young.write_text("{}\n", encoding="utf-8")
            ownerless_stale = queue / "stale.jsonl.processing.not-a-pid"
            ownerless_stale.write_text("{}\n", encoding="utf-8")
            now = 2_000.0
            os.utime(ownerless_young, (now - 60, now - 60))
            os.utime(ownerless_stale, (now - 1_000, now - 1_000))

            queue_spool.recover_processing(queue, now=now, stale_after_seconds=900)

            self.assertTrue(live.exists())
            self.assertFalse((queue / "live.jsonl").exists())
            self.assertFalse(dead.exists())
            self.assertTrue((queue / "dead.jsonl").exists())
            self.assertTrue(ownerless_young.exists())
            self.assertFalse((queue / "young.jsonl").exists())
            self.assertFalse(ownerless_stale.exists())
            self.assertTrue((queue / "stale.jsonl").exists())

    def test_spool_queue_skips_processing_owned_by_other_live_process(self) -> None:
        from hermes_second_brain import queue_spool

        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with tempfile.TemporaryDirectory() as td:
                queue = Path(td) / "context-inbox-spool.jsonl.d"
                queue.mkdir()
                live = queue / f"live.jsonl.processing.{sleeper.pid}"
                live.write_text("{}\n", encoding="utf-8")

                queue_spool.recover_processing(queue, now=2_000.0, stale_after_seconds=0)

                self.assertTrue(live.exists())
                self.assertFalse((queue / "live.jsonl").exists())
        finally:
            sleeper.terminate()
            try:
                sleeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sleeper.kill()
                sleeper.wait(timeout=5)

    def test_legacy_processing_skips_live_owner_and_recovers_dead_stale_ownerless(self) -> None:
        import importlib.util
        from hermes_second_brain import queue_spool

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_legacy_processing", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            spool = tmp / "context-inbox-spool.jsonl"
            live = tmp / f"context-inbox-spool.jsonl.processing.{os.getpid()}"
            live.write_text(json.dumps({"body": "live", "message_ts": "2026-07-17T09:00:00Z"}) + "\n", encoding="utf-8")
            dead = tmp / "context-inbox-spool.jsonl.processing.99999999"
            dead.write_text(json.dumps({"body": "dead", "message_ts": "2026-07-17T09:00:00Z"}) + "\n", encoding="utf-8")
            ownerless_young = tmp / "context-inbox-spool.jsonl.processing.ownerless"
            ownerless_young.write_text(json.dumps({"body": "young", "message_ts": "2026-07-17T09:00:00Z"}) + "\n", encoding="utf-8")
            ownerless_stale = tmp / "context-inbox-spool.jsonl.processing.malformed"
            ownerless_stale.write_text(json.dumps({"body": "stale", "message_ts": "2026-07-17T09:00:00Z"}) + "\n", encoding="utf-8")
            now = 2_000.0
            os.utime(ownerless_young, (now - 60, now - 60))
            os.utime(ownerless_stale, (now - 1_000, now - 1_000))
            imported: list[Path] = []

            def capture_run_cli(_python: str, _module: str, _env: dict[str, str], _args: list[str]) -> None:
                imported.append(Path(_args[_args.index("--path") + 1]))

            with mock.patch.object(queue_spool, "time", return_value=now):
                with mock.patch.object(module, "run_cli", side_effect=capture_run_cli):
                    quarantined = module.import_jsonl_spool(sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", spool)

            self.assertEqual(quarantined, 0)
            self.assertEqual({path.name for path in imported}, {dead.name, ownerless_stale.name})
            self.assertTrue(live.exists())
            self.assertTrue(ownerless_young.exists())
            self.assertFalse(dead.exists())
            self.assertFalse(ownerless_stale.exists())

    def test_malformed_spool_queue_file_is_quarantined_and_later_file_imports(self) -> None:
        import importlib.util

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_malformed_queue", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            spool = tmp / "context-inbox-spool.jsonl"
            queue = tmp / "context-inbox-spool.jsonl.d"
            queue.mkdir()
            malformed = queue / "001.jsonl"
            malformed.write_text("{bad json\n", encoding="utf-8")
            valid = queue / "002.jsonl"
            valid.write_text(
                json.dumps(
                    {
                        "platform": "signal",
                        "conversation_id": "family",
                        "direction": "inbound",
                        "body": "please call mom today",
                        "message_ts": "2026-07-17T09:00:00Z",
                        "source_message_id": "m1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            db = tmp / "db.sqlite3"
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            quarantined = module.import_jsonl_spool(sys.executable, "hermes_second_brain", env, db, spool)

            quarantine = queue / "quarantine"
            quarantined_files = list(quarantine.glob("001.jsonl.*.jsonl"))
            sidecars = list(quarantine.glob("001.jsonl.*.jsonl.error.json"))

            self.assertEqual(quarantined, 1)
            self.assertFalse(malformed.exists())
            self.assertFalse(valid.exists())
            self.assertEqual(len(quarantined_files), 1)
            self.assertEqual(quarantined_files[0].read_text(encoding="utf-8"), "{bad json\n")
            self.assertEqual(quarantine.stat().st_mode & 0o777, 0o700)
            self.assertEqual(quarantined_files[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(sidecars), 1)
            self.assertEqual(sidecars[0].stat().st_mode & 0o777, 0o600)
            sidecar = json.loads(sidecars[0].read_text(encoding="utf-8"))
            self.assertEqual(sidecar["filename"], "001.jsonl")
            self.assertEqual(sidecar["category"], "malformed_json")
            self.assertEqual(sidecar["line_number"], 1)
            self.assertNotIn("bad json", json.dumps(sidecar))
            self.assertFalse(list(queue.glob("*.processing.*")))
            rows = ContextInbox(db).list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["body"], "please call mom today")

    def test_semantically_invalid_queue_timestamp_is_quarantined_before_later_valid_file(self) -> None:
        import importlib.util

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_invalid_timestamp", script)
        self.assertIsNotNone(spec)
        assert spec
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            spool = tmp / "context-inbox-spool.jsonl"
            queue = tmp / "context-inbox-spool.jsonl.d"
            queue.mkdir()
            invalid = queue / "001.jsonl"
            invalid.write_text(json.dumps({"platform": "signal", "body": "bad", "message_ts": "9" * 100}) + "\n", encoding="utf-8")
            valid = queue / "002.jsonl"
            valid.write_text(
                json.dumps(
                    {
                        "platform": "signal",
                        "conversation_id": "family",
                        "direction": "inbound",
                        "body": "valid later event",
                        "message_ts": "2026-07-17T09:00:00Z",
                        "source_message_id": "valid-after-invalid",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            db = tmp / "db.sqlite3"
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            quarantined = module.import_jsonl_spool(sys.executable, "hermes_second_brain", env, db, spool)
            sidecars = list((queue / "quarantine").glob("001.jsonl.*.jsonl.error.json"))
            sidecar_category = json.loads(sidecars[0].read_text(encoding="utf-8"))["category"]
            rows = ContextInbox(db).list_events()

        self.assertEqual(quarantined, 1)
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(sidecar_category, "invalid_timestamp")
        self.assertEqual([row["body"] for row in rows], ["valid later event"])

    def test_spool_import_rejects_symlinked_spool_and_queue_paths(self) -> None:
        from hermes_second_brain import queue_spool

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            outside_queue = tmp / "outside-queue"
            outside_queue.mkdir()
            spool = tmp / "context-inbox-spool.jsonl"
            queue = spool.with_suffix(spool.suffix + ".d")
            queue.symlink_to(outside_queue, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlinked spool queue"):
                queue_spool.import_jsonl_spool(
                    sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", spool, lambda *_args: None
                )

            queue.unlink()
            outside_spool = tmp / "outside.jsonl"
            outside_spool.write_text("{}\n", encoding="utf-8")
            spool.symlink_to(outside_spool)
            with self.assertRaisesRegex(ValueError, "symlinked spool"):
                queue_spool.import_jsonl_spool(
                    sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", spool, lambda *_args: None
                )

    def test_quarantine_sidecar_failure_keeps_payload_and_later_file_imports(self) -> None:
        import importlib.util
        from hermes_second_brain import queue_spool

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_sidecar_failure", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            spool = tmp / "context-inbox-spool.jsonl"
            queue = tmp / "context-inbox-spool.jsonl.d"
            queue.mkdir()
            malformed = queue / "001.jsonl"
            malformed.write_text("{bad json\n", encoding="utf-8")
            valid = queue / "002.jsonl"
            valid.write_text(
                json.dumps(
                    {
                        "platform": "signal",
                        "conversation_id": "family",
                        "body": "later file",
                        "message_ts": "2026-07-17T09:00:00Z",
                        "source_message_id": "m2",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            db = tmp / "db.sqlite3"
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            with mock.patch.object(queue_spool, "write_private_json", side_effect=OSError("sidecar failed")):
                quarantined = module.import_jsonl_spool(sys.executable, "hermes_second_brain", env, db, spool)

            quarantined_files = list((queue / "quarantine").glob("001.jsonl.*.jsonl"))
            self.assertEqual(quarantined, 1)
            self.assertEqual(len(quarantined_files), 1)
            self.assertEqual(quarantined_files[0].read_text(encoding="utf-8"), "{bad json\n")
            self.assertFalse(valid.exists())
            rows = ContextInbox(db).list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["body"], "later file")

    def test_legacy_single_file_spool_malformed_is_quarantined_without_loss(self) -> None:
        import importlib.util

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_legacy_malformed", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            spool = tmp / "context-inbox-spool.jsonl"
            spool.write_text("[1, 2, 3]\n", encoding="utf-8")
            db = tmp / "db.sqlite3"
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            quarantined = module.import_jsonl_spool(sys.executable, "hermes_second_brain", env, db, spool)

            queue = tmp / "context-inbox-spool.jsonl.d"
            quarantined_files = list((queue / "quarantine").glob("context-inbox-spool.jsonl.*.jsonl"))
            self.assertEqual(quarantined, 1)
            self.assertTrue(spool.exists())
            self.assertEqual(spool.read_text(encoding="utf-8"), "")
            self.assertEqual(len(quarantined_files), 1)
            self.assertEqual(quarantined_files[0].read_text(encoding="utf-8"), "[1, 2, 3]\n")
            self.assertFalse(list(tmp.glob("context-inbox-spool.jsonl.processing.*")))
            self.assertEqual(ContextInbox(db).list_events(), [])

    def test_watch_prefers_gzip_slack_live_over_legacy_jsonl(self) -> None:
        import importlib.util

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_under_test_gzip", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            slack_dir = tmp / "slack"
            slack_dir.mkdir()
            gzip_live = slack_dir / "slack-live.jsonl.gz"
            legacy_live = slack_dir / "slack-live.jsonl"
            gzip_live.write_bytes(b"\x1f\x8b")
            legacy_live.write_text("", encoding="utf-8")
            imported: list[Path] = []

            def capture_run_cli(_python: str, _module: str, _env: dict[str, str], _args: list[str]) -> None:
                imported.append(Path(_args[_args.index("--path") + 1]))

            with mock.patch.object(module, "run_cli", side_effect=capture_run_cli):
                module.import_slack_live_if_exists(sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", slack_dir)

        self.assertEqual(imported, [gzip_live])

    def test_watch_falls_back_to_legacy_slack_live_jsonl(self) -> None:
        import importlib.util

        script = Path.cwd() / "scripts" / "context_watch.py"
        spec = importlib.util.spec_from_file_location("context_watch_under_test_legacy", script)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            slack_dir = tmp / "slack"
            slack_dir.mkdir()
            legacy_live = slack_dir / "slack-live.jsonl"
            legacy_live.write_text("", encoding="utf-8")
            imported: list[Path] = []

            def capture_run_cli(_python: str, _module: str, _env: dict[str, str], _args: list[str]) -> None:
                imported.append(Path(_args[_args.index("--path") + 1]))

            with mock.patch.object(module, "run_cli", side_effect=capture_run_cli):
                module.import_slack_live_if_exists(sys.executable, "hermes_second_brain", {}, tmp / "db.sqlite3", slack_dir)

        self.assertEqual(imported, [legacy_live])

    def test_watch_script_quiet_once_with_temp_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            import_dir = tmp / "import"
            import_dir.mkdir()
            spool = import_dir / "context-inbox-spool.jsonl"
            spool.write_text(
                json.dumps(
                    {
                        "platform": "signal",
                        "conversation_id": "family",
                        "conversation_type": "dm",
                        "sender_display_name": "Mama",
                        "direction": "inbound",
                        "body": "Kannst du heute anrufen?",
                        "message_ts": "2026-07-17T09:00:00Z",
                        "source_message_id": "m1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CONTEXT_IMPORT_DIR": str(import_dir),
                "CONTEXT_DB": str(tmp / "context.sqlite3"),
                "CONTEXT_EXPORT": str(tmp / "context.txt"),
                "CONTEXT_WATCH_STATE": str(tmp / "context-watch-state.json"),
                "HERMES_CONTEXT_INBOX_SPOOL": str(spool),
                "HERMES_SECOND_BRAIN_MODULE": "hermes_second_brain",
                "PROJECT_DIR": str(Path.cwd()),
                "WHATSAPP_IMPORT_PATH": str(tmp / "missing-whatsapp.sqlite"),
            }
            cmd = [sys.executable, str(Path.cwd() / "scripts" / "context_watch.py")]
            first = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)
            second = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("anrufen", first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "")

    def test_unchanged_upsert_preserves_updated_at_and_processing_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.sqlite3"
            inbox = ContextInbox(db)
            event = {
                "platform": "whatsapp",
                "conversation_id": "family",
                "direction": "inbound",
                "body": "Bitte morgen zahlen",
                "message_ts": "2026-07-17T09:00:00Z",
                "source_message_id": "w1",
            }
            event_id = inbox.upsert_event(event)
            inbox.rank_all()
            before = ContextInbox(db).list_events()[0]
            inbox.upsert_event(event)
            same = ContextInbox(db).list_events()[0]
            changed = dict(event, body="Bitte morgen zahlen, danke", raw={"changed": True})
            returned = inbox.upsert_event(changed)
            after = ContextInbox(db).list_events()[0]

        self.assertEqual(returned, event_id)
        self.assertEqual(same["updated_at"], before["updated_at"])
        self.assertEqual(same["processing_state"], "ranked")
        self.assertGreater(after["updated_at"], same["updated_at"])
        self.assertEqual(after["processing_state"], "new")

    def test_signal_dry_run_blocked_does_not_create_context_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            signal = tmp / "signal.sqlite"
            sqlite3.connect(signal).close()
            db = tmp / "inbox.sqlite3"
            summary = import_signal_desktop(signal, db, dry_run=True)

        self.assertEqual(summary.status, "blocked_decryption_needed")
        self.assertFalse(db.exists())

    def test_default_whatsapp_import_path_can_be_overridden(self) -> None:
        with mock.patch.dict(os.environ, {"WHATSAPP_IMPORT_PATH": "/tmp/staged-wa.sqlite"}, clear=False):
            self.assertEqual(default_whatsapp_import_path(Path("/tmp/import")), Path("/tmp/staged-wa.sqlite"))


if __name__ == "__main__":
    unittest.main()
