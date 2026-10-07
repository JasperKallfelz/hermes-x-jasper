from __future__ import annotations

import json
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_second_brain.context_inbox import ContextInbox
from hermes_second_brain.graph import build_memory_graph, graph_arg_parser, make_handler, options_from_args, validate_bind
from hermes_second_brain.manifest import load_manifest
from hermes_second_brain.state import State


class MemoryGraphTests(unittest.TestCase):
    def test_hierarchy_includes_empty_configured_sources_and_folders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = tmp / "corpus"
            empty = tmp / "empty"
            corpus.mkdir()
            empty.mkdir()
            manifest_path = _write_manifest(tmp, [("docs", corpus, "brain"), ("empty", empty, "empty")])
            manifest = load_manifest(manifest_path)
            State(manifest.state_db)
            _insert_resource(manifest.state_db, "docs", "brain", "projects/hermes/overview.md", status="synced")

            graph = build_memory_graph(manifest)

        labels = {node["label"] for node in graph["nodes"]}
        self.assertIn("Hermes Second Brain", labels)
        self.assertIn("brain", labels)
        self.assertIn("empty", labels)
        self.assertIn("projects", labels)
        self.assertIn("hermes", labels)
        self.assertIn("overview.md", labels)
        self.assertTrue(any(link["source"] == "root" and link["target"] == "source:empty" for link in graph["links"]))

    def test_deleted_tombstones_are_excluded_and_resource_rows_are_capped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            root.mkdir()
            manifest = load_manifest(_write_manifest(tmp, [("docs", root, "brain")]))
            State(manifest.state_db)
            _insert_resource(manifest.state_db, "docs", "brain", "active.md", status="synced")
            _insert_resource(manifest.state_db, "docs", "brain", "gone.md", status="deleted")
            _insert_resource(manifest.state_db, "docs", "brain", "other.md", status="pending")

            graph = build_memory_graph(manifest, resource_limit=1)

        labels = {node["label"] for node in graph["nodes"]}
        self.assertIn("active.md", labels)
        self.assertNotIn("gone.md", labels)
        self.assertNotIn("other.md", labels)
        self.assertEqual(graph["stats"]["resources"], 1)
        self.assertTrue(graph["stats"]["truncated"])

    def test_rows_from_removed_manifest_sources_are_not_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            root.mkdir()
            manifest = load_manifest(_write_manifest(tmp, [("docs", root, "brain")]))
            State(manifest.state_db)
            _insert_resource(manifest.state_db, "docs", "brain", "current.md", status="synced")
            _insert_resource(manifest.state_db, "retired-mail", "mail", "old-private-export.md", status="synced")

            graph = build_memory_graph(manifest)

        serialized = json.dumps(graph, sort_keys=True)
        self.assertIn("current.md", serialized)
        self.assertNotIn("old-private-export.md", serialized)
        self.assertNotIn("retired-mail", serialized)

    def test_context_inbox_serializes_only_private_aggregate_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            root.mkdir()
            manifest = load_manifest(_write_manifest(tmp, [("docs", root, "brain")]))
            State(manifest.state_db)
            inbox = ContextInbox(manifest.context_inbox_db)
            event_id = inbox.upsert_event(
                {
                    "platform": "slack",
                    "account": "secret-account",
                    "conversation_id": "C-private",
                    "conversation_name": "Board Room",
                    "sender_display_name": "Private Sender",
                    "body": "raw body with token sk-secret",
                    "raw_json": {"token": "super-secret"},
                    "source_path": "/Users/" + "robin/private/messages.jsonl",
                    "source_message_id": "m1",
                    "message_ts": "2026-07-17T09:00:00Z",
                }
            )
            with inbox.connect() as conn:
                conn.execute("UPDATE context_events SET relevance_tier='immediate' WHERE event_id=?", (event_id,))
                conn.execute(
                    """
                    INSERT INTO context_reminders(reminder_id,source_event_id,platform,conversation_id,text,confidence,status,created_at)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    ("rem_1", event_id, "slack", "C-private", "Call private person", 0.9, "candidate", "2026-07-17T09:00:00Z"),
                )
                conn.execute(
                    """
                    INSERT INTO context_habit_hypotheses(habit_id,hypothesis,evidence_count,day_count,confidence,status,evidence_event_ids,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    ("hab_1", "Private habit text", 3, 2, 0.8, "candidate", json.dumps([event_id]), "now", "now"),
                )

            graph = build_memory_graph(manifest)

        encoded = json.dumps(graph, sort_keys=True)
        self.assertIn("slack (1)", encoded)
        self.assertIn("immediate (1)", encoded)
        self.assertIn("candidate (1)", encoded)
        for sensitive in ("raw body", "sk-secret", "super-secret", "Board Room", "Private Sender", "Call private", "Private habit", "/Users/" + "robin", "secret-account", "C-private"):
            self.assertNotIn(sensitive, encoded)

    def test_bind_safety_and_cli_parsing(self) -> None:
        validate_bind("127.0.0.1")
        validate_bind("0.0.0.0", allow_remote=True)
        with self.assertRaisesRegex(ValueError, "refusing non-loopback"):
            validate_bind("0.0.0.0")

        parser = graph_arg_parser()
        args = parser.parse_args(["--manifest", "config/manifest.json", "--host", "127.0.0.1", "--port", "8765", "--no-open"])
        options = options_from_args(args)
        self.assertEqual(options.manifest, Path("config/manifest.json"))
        self.assertEqual(options.host, "127.0.0.1")
        self.assertEqual(options.port, 8765)
        self.assertFalse(options.open_browser)
        self.assertFalse(options.allow_remote)

    def test_routes_headers_and_asset_traversal_protection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            root.mkdir()
            manifest = load_manifest(_write_manifest(tmp, [("docs", root, "brain")]))
            State(manifest.state_db)

            index = _handle(manifest, "/")
            self.assertEqual(index.status, 200)
            self.assertIn("no-store", index.headers["Cache-Control"])
            self.assertIn("default-src 'self'", index.headers["Content-Security-Policy"])
            self.assertIn("text/html", index.headers["Content-Type"])

            graph = _handle(manifest, "/api/graph")
            self.assertEqual(graph.status, 200)
            self.assertIn("application/json", graph.headers["Content-Type"])

            app = _handle(manifest, "/assets/app.js")
            self.assertEqual(app.status, 200)
            self.assertIn("application/javascript", app.headers["Content-Type"])

            missing = _handle(manifest, "/assets/../graph.py")
            self.assertEqual(missing.status, 404)

            bad_route = _handle(manifest, "/api/private")
            self.assertEqual(bad_route.status, 404)


def _write_manifest(tmp: Path, sources: list[tuple[str, Path, str]]) -> Path:
    data = {
        "state_db": str(tmp / "state.sqlite3"),
        "context_inbox_db": str(tmp / "context.sqlite3"),
        "sources": [{"id": source_id, "root": str(root), "namespace": namespace} for source_id, root, namespace in sources],
    }
    path = tmp / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _insert_resource(db: Path, source_root_id: str, namespace: str, relative_path: str, *, status: str) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO resources(source_id,source_root_id,namespace,path,relative_path,sha256,size_bytes,mtime_ns,status,attempts,first_seen,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{namespace}:{relative_path}",
                source_root_id,
                namespace,
                f"/private/local/{relative_path}",
                relative_path,
                "a" * 64,
                123,
                1,
                status,
                0,
                1.0,
                1.0,
            ),
        )


class _Response:
    def __init__(self, status: int, headers, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body


def _handle(manifest, path: str) -> _Response:
    base = make_handler(manifest)

    class CaptureHandler(base):  # type: ignore[misc, valid-type]
        def send_response(self, code, message=None):  # type: ignore[no-untyped-def]
            self._status = code

        def send_header(self, keyword, value):  # type: ignore[no-untyped-def]
            self._headers[keyword] = value

        def end_headers(self):  # type: ignore[no-untyped-def]
            return None

    handler = CaptureHandler.__new__(CaptureHandler)
    handler.path = path
    handler.wfile = io.BytesIO()
    handler._headers = {}
    handler._status = None
    handler.do_GET()
    return _Response(handler._status, handler._headers, handler.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
