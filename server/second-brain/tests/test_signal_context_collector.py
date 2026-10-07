from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from hermes_second_brain import personal_messaging as pm
from hermes_second_brain.context_inbox import ContextInbox


def load_collector():
    path = Path.cwd() / "scripts" / "signal_context_collector.py"
    spec = importlib.util.spec_from_file_location("signal_context_collector", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load_collector()


class SignalContextCollectorTests(unittest.TestCase):
    def test_sse_chunk_boundaries_multiline_comments_and_non_data(self) -> None:
        parser = collector.SSEParser()
        self.assertEqual(parser.feed(":\n"), [])
        self.assertEqual(parser.feed("event: ignored\nda"), [])
        self.assertEqual(parser.feed('ta: {"a":'), [])
        self.assertEqual(parser.feed('1}\ndata: {"b":2}\n\n'), ['{"a":1}\n{"b":2}'])
        self.assertEqual(parser.feed('data: {"c":3}'), [])
        self.assertEqual(parser.close(), ['{"c":3}'])

    def test_sse_oversized_event_resets_safely(self) -> None:
        parser = collector.SSEParser(max_event_bytes=8)
        with self.assertRaises(collector.SSEEventTooLarge):
            parser.feed("data: 123456789\n\n")
        self.assertEqual(parser.feed("data: ok\n\n"), ["ok"])

    def test_normalizes_inbound_dm_group_outbound_note_and_edit(self) -> None:
        account = "+15551234567"
        inbound = collector.normalize_signal_event(
            {
                "envelope": {
                    "sourceNumber": "+15557654321",
                    "sourceName": "Alex",
                    "timestamp": 1784280000000,
                    "dataMessage": {"message": "hi", "timestamp": 1784280000000},
                }
            },
            account,
        )
        group = collector.normalize_signal_event(
            {
                "sourceUuid": "u1",
                "timestamp": 1784280001000,
                "dataMessage": {"message": "team", "groupInfo": {"groupId": "abc", "groupName": "Team"}},
            },
            account,
        )
        outbound = collector.normalize_signal_event(
            {
                "envelope": {
                    "timestamp": 1784280002000,
                    "syncMessage": {"sentMessage": {"destinationNumber": account, "timestamp": 1784280002000, "message": "note"}},
                }
            },
            account,
        )
        edit = collector.normalize_signal_event(
            {
                "sourceNumber": "+15557654321",
                "timestamp": 1784280003000,
                "editMessage": {
                    "targetSentTimestamp": 1784280000000,
                    "dataMessage": {"message": "edited", "timestamp": 1784280003000},
                },
            },
            account,
        )

        assert inbound and group and outbound and edit
        self.assertEqual(inbound["direction"], "inbound")
        self.assertEqual(inbound["conversation_type"], "dm")
        self.assertNotIn(account, inbound["account"])
        self.assertEqual(inbound["action_target_id"], "+15557654321")
        self.assertEqual(inbound["action_account_id"], account)
        self.assertEqual(group["conversation_id"], "group:abc")
        self.assertEqual(group["action_target_id"], "group:abc")
        self.assertEqual(group["conversation_name"], "Team")
        self.assertEqual(outbound["direction"], "outbound")
        self.assertEqual(outbound["sender_id"], collector.account_key(account))
        self.assertIn("edit", edit["flags"])
        self.assertIn(":edit:", edit["source_message_id"])
        self.assertIn("target_source_message_id", edit)

    def test_real_collector_event_round_trips_action_peer_without_summary_leak(self) -> None:
        account = "+15551234567"
        peer = "8f14e45f-ea3e-4a7a-9f7d-08f31657c65b"
        record = collector.normalize_signal_event(
            {
                "sourceUuid": peer,
                "timestamp": 1784280000000,
                "dataMessage": {"message": "private body", "timestamp": 1784280000000},
            },
            account,
        )
        assert record is not None
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "context.sqlite3"
            event_id = ContextInbox(db).upsert_event(record)
            conn = pm.open_context_db(db)
            try:
                ordinary = pm.get_event(conn, event_id)["event"]
                action_event = pm.get_event(conn, event_id, for_action=True)["event"]
                resolved = pm.resolve_conversation(
                    conn,
                    "signal",
                    record["conversation_id"],
                    account=record["account"],
                    workspace="",
                )
            finally:
                conn.close()
            prepared = pm.build_payload(
                platform="signal", action="send", event=action_event, message="reply"
            )

        self.assertNotIn("action_target_id", ordinary)
        self.assertNotIn("action_account_id", ordinary)
        self.assertEqual(prepared.target, peer)
        self.assertEqual(prepared.payload["binding"]["execution_account"], account)
        self.assertEqual(resolved["_action_target_id"], peer)
        self.assertEqual(resolved["_action_account_id"], account)

    def test_ignored_receipt_typing_story_and_contentless_envelopes(self) -> None:
        account = "+15551234567"
        for event in (
            {"receiptMessage": {"when": 1}},
            {"typingMessage": {"action": "STARTED"}},
            {"storyMessage": {"text": "story"}},
            {"dataMessage": {"message": ""}},
            {"dataMessage": {"message": "missing peer"}},
        ):
            self.assertIsNone(collector.normalize_signal_event(event, account))

    def test_out_of_range_timestamp_is_safe_and_payload_errors_do_not_leak(self) -> None:
        self.assertEqual(collector.ms_to_iso(10**100), "")
        logs = io.StringIO()
        handler = collector.logging.StreamHandler(logs)
        collector.LOGGER.addHandler(handler)
        try:
            with mock.patch.object(collector, "normalize_signal_event", side_effect=OverflowError("private body +15551234567")):
                result = collector.handle_sse_payload('{"sourceNumber":"+15557654321","dataMessage":{"message":"secret body"}}', "+15551234567", Path("/tmp/spool.jsonl"))
        finally:
            collector.LOGGER.removeHandler(handler)

        self.assertFalse(result)
        self.assertNotIn("secret body", logs.getvalue())
        self.assertNotIn("+15551234567", logs.getvalue())

    def test_edit_links_to_original_identity_and_is_stable(self) -> None:
        account = "+15551234567"
        original = {
            "sourceNumber": "+15557654321",
            "timestamp": 1784280000000,
            "dataMessage": {"message": "before", "timestamp": 1784280000000},
        }
        edit = {
            "sourceNumber": "+15557654321",
            "timestamp": 1784280003000,
            "editMessage": {
                "targetSentTimestamp": 1784280000000,
                "dataMessage": {"message": "after", "timestamp": 1784280003000},
            },
        }

        original_record = collector.normalize_signal_event(original, account)
        first_edit = collector.normalize_signal_event(edit, account)
        duplicate_edit = collector.normalize_signal_event(json.loads(json.dumps(edit)), account)

        assert original_record and first_edit and duplicate_edit
        self.assertEqual(first_edit["source_message_id"], duplicate_edit["source_message_id"])
        self.assertNotEqual(first_edit["source_message_id"], original_record["source_message_id"])
        self.assertEqual(first_edit["target_source_message_id"], original_record["source_message_id"])
        self.assertEqual(first_edit["target_message_ts"], original_record["message_ts"])
        self.assertEqual(first_edit["target_sender_id"], original_record["sender_id"])

    def test_stable_idempotent_ids_and_attachment_reply_metadata(self) -> None:
        account = "+15551234567"
        event = {
            "sourceNumber": "+15557654321",
            "timestamp": 1784280000000,
            "dataMessage": {
                "message": "file",
                "timestamp": 1784280000000,
                "quote": {"id": 1784270000000, "text": "old", "authorNumber": "+15550000000", "authorName": "J"},
                "attachments": [
                    {
                        "id": "att1",
                        "contentType": "image/jpeg",
                        "fileName": "x.jpg",
                        "size": 123,
                        "path": "/local/x.jpg",
                    }
                ],
            },
        }
        first = collector.normalize_signal_event(event, account)
        second = collector.normalize_signal_event(json.loads(json.dumps(event)), account)
        assert first and second
        self.assertEqual(first["source_message_id"], second["source_message_id"])
        self.assertEqual(first["thread_id"], "1784270000000")
        self.assertEqual(first["reply_metadata"]["text"], "old")
        self.assertEqual(first["attachments"][0]["id"], "att1")
        self.assertEqual(first["attachments"][0]["contentType"], "image/jpeg")
        self.assertEqual(first["attachments"][0]["fileName"], "x.jpg")
        self.assertEqual(first["attachments"][0]["size"], 123)
        self.assertEqual(first["attachments"][0]["local_path"], "/local/x.jpg")

    def test_spool_permissions_atomic_file_shape_and_sanitized_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spool = Path(td) / "context-inbox-spool.jsonl"
            record = {"source_message_id": "bad/name with spaces", "body": "private body"}
            written = collector.write_spool_record(spool, record)
            queue = Path(td) / "context-inbox-spool.jsonl.d"

            self.assertEqual(stat.S_IMODE(queue.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(written.stat().st_mode), 0o600)
            self.assertEqual(written.suffix, ".jsonl")
            self.assertNotIn("/", written.name)
            self.assertFalse(list(queue.glob("*.tmp")))
            self.assertEqual(json.loads(written.read_text(encoding="utf-8")), record)

    def test_spool_record_fsyncs_queue_directory_and_propagates_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spool = Path(td) / "context-inbox-spool.jsonl"
            with mock.patch.object(collector, "fsync_dir", side_effect=OSError("dir fsync failed")) as fsync_dir:
                with self.assertRaises(OSError):
                    collector.write_spool_record(spool, {"source_message_id": "m1", "body": "body"})

            queue = Path(td) / "context-inbox-spool.jsonl.d"
            fsync_dir.assert_called_once_with(queue)
            self.assertFalse(list(queue.glob("*.tmp")))

    def test_health_check_with_local_stdlib_http_server(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/v1/check":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_args):
                return

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError as exc:
            self.skipTest(f"local HTTP server bind blocked by sandbox: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(collector.health_check(f"http://127.0.0.1:{server.server_port}"))
            self.assertFalse(collector.health_check(f"http://127.0.0.1:{server.server_port}/bad"))
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_cli_missing_account_and_no_body_or_full_account_logging(self) -> None:
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(stderr):
            collector.main(["--http-url", "http://127.0.0.1:9"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--account or SIGNAL_ACCOUNT is required", stderr.getvalue())

        with tempfile.TemporaryDirectory() as td, mock.patch.object(collector, "urlopen") as fake_urlopen:
            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _size):
                    if getattr(self, "done", False):
                        return b""
                    self.done = True
                    payload = {"sourceNumber": "+15557654321", "timestamp": 1784280000000, "dataMessage": {"message": "secret body"}}
                    return ("data: " + json.dumps(payload) + "\n\n").encode()

            fake_urlopen.return_value = Response()
            logs = io.StringIO()
            handler = collector.logging.StreamHandler(logs)
            collector.LOGGER.addHandler(handler)
            collector.LOGGER.setLevel(collector.logging.INFO)
            try:
                code = collector.main(["--account", "+15551234567", "--spool", str(Path(td) / "spool.jsonl"), "--once"])
            finally:
                collector.LOGGER.removeHandler(handler)

        self.assertEqual(code, 0)
        self.assertNotIn("+15551234567", logs.getvalue())
        self.assertNotIn("secret body", logs.getvalue())

    def test_launchd_signal_daemon_and_collector_templates_are_supervised(self) -> None:
        collector_text = (Path.cwd() / "launchd" / "com.example.hermes-second-brain.signal-context-collector.plist.template").read_text(encoding="utf-8")
        daemon_text = (Path.cwd() / "launchd" / "com.example.hermes-second-brain.signal-cli-daemon.plist.template").read_text(encoding="utf-8")
        self.assertIn("__SIGNAL_E164_ACCOUNT__", collector_text)
        self.assertIn("__PROJECT_DIR__/scripts/signal_context_collector.py", collector_text)
        self.assertIn("__SIGNAL_CLI__", daemon_text)
        self.assertIn("__SIGNAL_E164_ACCOUNT__", daemon_text)
        self.assertIn("<string>daemon</string>", daemon_text)
        self.assertIn("<string>--http</string>", daemon_text)
        self.assertIn("<string>127.0.0.1:8080</string>", daemon_text)
        for text in (collector_text, daemon_text):
            self.assertIn("<key>KeepAlive</key>", text)
            self.assertIn("<key>RunAtLoad</key>", text)
            self.assertIn("<key>Umask</key>", text)
            self.assertIn("<integer>63</integer>", text)
            self.assertIn("__HOME__/.hermes/second-brain/logs/", text)
            self.assertNotIn("+15551234567", text)


if __name__ == "__main__":
    unittest.main()
