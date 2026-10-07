from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from hermes_second_brain import personal_messaging as pm

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

SIGNAL_CLI_RESULTS = json.loads(
    (Path(__file__).parent / "fixtures" / "signal_cli_results.json").read_text(encoding="utf-8")
)

CONTEXT_SCHEMA = """
CREATE TABLE context_events(
  event_id TEXT PRIMARY KEY,
  identity_key TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL,
  account TEXT NOT NULL DEFAULT '',
  workspace TEXT NOT NULL DEFAULT '',
  action_target_id TEXT NOT NULL DEFAULT '',
  action_account_id TEXT NOT NULL DEFAULT '',
  conversation_id TEXT NOT NULL DEFAULT '',
  conversation_name TEXT NOT NULL DEFAULT '',
  conversation_type TEXT NOT NULL DEFAULT '',
  sender_id TEXT NOT NULL DEFAULT '',
  sender_display_name TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL DEFAULT 'unknown',
  body TEXT NOT NULL DEFAULT '',
  message_ts TEXT NOT NULL DEFAULT '',
  received_ts TEXT NOT NULL DEFAULT '',
  ingested_ts TEXT NOT NULL DEFAULT '',
  source_message_id TEXT NOT NULL DEFAULT '',
  thread_id TEXT NOT NULL DEFAULT '',
  permalink TEXT NOT NULL DEFAULT '',
  attachment_metadata_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT NOT NULL DEFAULT '',
  flags TEXT NOT NULL DEFAULT '[]',
  relevance_score INTEGER NOT NULL DEFAULT 0,
  relevance_tier TEXT NOT NULL DEFAULT 'archive',
  relevance_reasons TEXT NOT NULL DEFAULT '[]',
  processing_state TEXT NOT NULL DEFAULT 'new',
  updated_at REAL NOT NULL
);
"""

EVENTS = [
    {
        "event_id": "ev-wa-1",
        "platform": "whatsapp",
        "account": "wa-primary",
        "conversation_id": ("4915112345678" + "@s.whatsapp.net"),
        "conversation_name": "Mara",
        "conversation_type": "dm",
        "sender_id": ("4915112345678" + "@s.whatsapp.net"),
        "sender_display_name": "Mara",
        "direction": "inbound",
        "body": "Can you bring the 50% off voucher tonight?",
        "message_ts": "2026-07-20T18:00:00Z",
        "source_message_id": "3EB0A1B2C3D4",
        "thread_id": "",
    },
    {
        "event_id": "ev-wa-2",
        "platform": "whatsapp",
        "account": "wa-primary",
        "conversation_id": ("111111111111111111" + "@g.us"),
        "conversation_name": "Team Standup",
        "conversation_type": "group",
        "sender_id": "4915199999999@lid",
        "sender_display_name": "Jonas",
        "direction": "inbound",
        "body": "standup moved to 10:00",
        "message_ts": "2026-07-20T09:00:00Z",
        "source_message_id": "3EB0FFFFFFFF",
        "thread_id": "",
    },
    {
        "event_id": "ev-sig-1",
        "platform": "signal",
        "account": "signal-account:test-primary",
        "action_target_id": "+4915100000000",
        "action_account_id": "+4915000000000",
        "conversation_id": "+4915100000000",
        "conversation_name": "Ines",
        "conversation_type": "dm",
        "sender_id": "+4915100000000",
        "sender_display_name": "Ines",
        "direction": "inbound",
        "body": "dinner at eight?",
        "message_ts": "2026-07-20T17:30:00Z",
        "source_message_id": "signal-1753031400000",
        "thread_id": "",
    },
    {
        "event_id": "ev-sig-2",
        "platform": "signal",
        "account": "signal-account:test-primary",
        "action_target_id": "group:aGVsbG8td29ybGQ=",
        "action_account_id": "+4915000000000",
        "conversation_id": "group:aGVsbG8td29ybGQ=",
        "conversation_name": "Climbing",
        "conversation_type": "group",
        "sender_id": "+4915100000001",
        "sender_display_name": "Ana",
        "direction": "inbound",
        "body": "session tomorrow",
        "message_ts": "2026-07-20T12:00:00Z",
        "source_message_id": "signal-1753012800000",
        "thread_id": "",
    },
    {
        "event_id": "ev-sl-1",
        "platform": "slack",
        "account": "T0123ACME",
        "workspace": "acme",
        "conversation_id": "C0123ABCD",
        "conversation_name": "eng-general",
        "conversation_type": "channel",
        "sender_id": "U9999AAAA",
        "sender_display_name": "Priya",
        "direction": "inbound",
        "body": "deploy is green",
        "message_ts": "2026-07-20T15:00:00Z",
        "source_message_id": "1753023600.001900",
        "thread_id": "1753023600.001900",
    },
    # Two conversations sharing a name: resolving "Family" must be refused.
    {
        "event_id": "ev-wa-3",
        "platform": "whatsapp",
        "account": "wa-primary",
        "conversation_id": ("222222222222222222" + "@g.us"),
        "conversation_name": "Family",
        "conversation_type": "group",
        "sender_id": ("4915100000002" + "@s.whatsapp.net"),
        "sender_display_name": "Dad",
        "direction": "inbound",
        "body": "call me",
        "message_ts": "2026-07-19T10:00:00Z",
        "source_message_id": "3EB0AAAA1111",
        "thread_id": "",
    },
    {
        "event_id": "ev-wa-4",
        "platform": "whatsapp",
        "account": "wa-primary",
        "conversation_id": ("333333333333333333" + "@g.us"),
        "conversation_name": "Family Office",
        "conversation_type": "group",
        "sender_id": ("4915100000003" + "@s.whatsapp.net"),
        "sender_display_name": "Lea",
        "direction": "outbound",
        "body": "sent the docs",
        "message_ts": "2026-07-19T11:00:00Z",
        "source_message_id": "3EB0BBBB2222",
        "thread_id": "",
    },
    # A body containing SQL metacharacters and LIKE wildcards.
    {
        "event_id": "ev-wa-5",
        "platform": "whatsapp",
        "account": "wa-primary",
        "conversation_id": ("4915188888888" + "@s.whatsapp.net"),
        "conversation_name": "Kim",
        "conversation_type": "dm",
        "sender_id": ("4915188888888" + "@s.whatsapp.net"),
        "sender_display_name": "Kim",
        "direction": "inbound",
        "body": "quote: '); DROP TABLE context_events;--",
        "message_ts": "2026-07-18T08:00:00Z",
        "source_message_id": "3EB0CCCC3333",
        "thread_id": "",
    },
]


def make_context_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(CONTEXT_SCHEMA)
    for index, event in enumerate(EVENTS):
        row = {
            "identity_key": f"ik-{index}",
            "account": "",
            "workspace": "",
            "action_target_id": "",
            "action_account_id": "",
            "received_ts": event["message_ts"],
            "ingested_ts": event["message_ts"],
            "permalink": "",
            "attachment_metadata_json": "[]",
            "raw_json": json.dumps({"secret_token": "should-not-leak", "id": event["event_id"]}),
            "source_path": "",
            "flags": "[]",
            "updated_at": 0.0,
            **event,
        }
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        conn.execute(f"INSERT INTO context_events({columns}) VALUES({placeholders})", row)
    conn.commit()
    conn.close()
    return path


class FakeClient:
    """Stands in for LoopbackHttpClient; records calls, never touches a socket."""

    def __init__(self, response=None, error: Exception | None = None):
        self.response = response if response is not None else {"success": True, "id": "REMOTE-1"}
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, path, payload):
        self.calls.append((path, payload))
        if self.error is not None:
            raise self.error
        if callable(self.response):
            return self.response(path, payload)
        return self.response


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += dt.timedelta(**kwargs)


class TempCaseMixin:
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.context_db = make_context_db(self.tmp / "context-inbox.sqlite3")
        self.actions_db = self.tmp / "private" / "actions.sqlite3"

    def store(self, clock=None) -> pm.ActionStore:
        return pm.ActionStore(self.actions_db, clock=clock or pm._utc_now)

    def conn(self):
        conn = pm.open_context_db(self.context_db)
        self.addCleanup(conn.close)
        return conn

    def config(self, client) -> pm.ExecutionConfig:
        return pm.ExecutionConfig(
            signal_account="+4915000000000",
            client_factory=lambda url, timeout, allow: client,
        )

    def prepare(self, store: pm.ActionStore, **kwargs) -> dict:
        prepared = pm.build_payload(**kwargs)
        return store.create_intent(prepared, ttl_seconds=kwargs.pop("ttl_seconds", 300))


# --------------------------------------------------------------------------
# read-only surface
# --------------------------------------------------------------------------


class SearchTests(TempCaseMixin, unittest.TestCase):
    def test_search_matches_body_and_bounds_snippet(self):
        result = pm.search_events(self.conn(), query="voucher")
        self.assertEqual(result["count"], 1)
        hit = result["results"][0]
        self.assertEqual(hit["event_id"], "ev-wa-1")
        self.assertIn("voucher", hit["snippet"])
        self.assertNotIn("body", hit)

    def test_search_escapes_like_wildcards(self):
        """A literal % must not behave as "match anything"."""
        wildcard_only = pm.search_events(self.conn(), query="%")
        self.assertEqual(wildcard_only["count"], 1)
        self.assertEqual(wildcard_only["results"][0]["event_id"], "ev-wa-1")

        # A literal "_" matches only the one body containing an underscore
        # ("context_events"), not every non-empty row -- proof it is escaped.
        underscore = pm.search_events(self.conn(), query="_")
        self.assertEqual(underscore["count"], 1)
        self.assertEqual(underscore["results"][0]["event_id"], "ev-wa-5")

    def test_search_is_injection_resistant(self):
        injection = "'); DROP TABLE context_events;--"
        result = pm.search_events(self.conn(), query=injection)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["event_id"], "ev-wa-5")
        # The table survived, so the payload was data and never SQL.
        still_there = pm.search_events(self.conn(), query="")
        self.assertEqual(still_there["count"], len(EVENTS))

    def test_search_filters_combine(self):
        result = pm.search_events(
            self.conn(), platform="whatsapp", direction="outgoing", since="2026-07-19T00:00:00Z"
        )
        self.assertEqual([hit["event_id"] for hit in result["results"]], ["ev-wa-4"])

    def test_search_limit_is_bounded(self):
        result = pm.search_events(self.conn(), limit=10_000)
        self.assertEqual(result["limit"], pm.SEARCH_LIMIT_MAX)

    def test_search_rejects_bad_direction_and_since(self):
        with self.assertRaises(pm.ValidationError):
            pm.search_events(self.conn(), direction="sideways")
        with self.assertRaises(pm.ValidationError):
            pm.search_events(self.conn(), since="yesterday")

    def test_search_truncated_flag(self):
        result = pm.search_events(self.conn(), limit=2)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["count"], 2)


class ConversationTests(TempCaseMixin, unittest.TestCase):
    def test_conversations_report_counts_and_latest(self):
        result = pm.list_conversations(self.conn(), platform="whatsapp")
        by_id = {item["conversation_id"]: item for item in result["conversations"]}
        mara = by_id[("4915112345678" + "@s.whatsapp.net")]
        self.assertEqual(mara["message_count"], 1)
        self.assertEqual(mara["latest"]["event_id"], "ev-wa-1")
        self.assertEqual(mara["conversation_type"], "dm")

    def test_conversations_query_escapes_wildcards(self):
        result = pm.list_conversations(self.conn(), query="_")
        self.assertEqual(result["count"], 0)

    def test_resolve_conversation_exact_id(self):
        resolved = pm.resolve_conversation(self.conn(), "whatsapp", ("111111111111111111" + "@g.us"))
        self.assertEqual(resolved["conversation_name"], "Team Standup")

    def test_resolve_conversation_ambiguous_name_refuses(self):
        with self.assertRaises(pm.AmbiguousTargetError) as ctx:
            pm.resolve_conversation(self.conn(), "whatsapp", "Family")
        names = {c["conversation_name"] for c in ctx.exception.details["candidates"]}
        self.assertEqual(names, {"Family", "Family Office"})

    def test_resolve_conversation_unique_name(self):
        resolved = pm.resolve_conversation(self.conn(), "whatsapp", "Team Stand")
        self.assertEqual(resolved["conversation_id"], ("111111111111111111" + "@g.us"))

    def test_resolve_conversation_missing(self):
        with self.assertRaises(pm.MessagingError) as ctx:
            pm.resolve_conversation(self.conn(), "whatsapp", "nobody-here")
        self.assertEqual(ctx.exception.code, "conversation_not_found")

    def test_same_conversation_id_in_two_accounts_is_never_merged(self):
        conn = sqlite3.connect(self.context_db)
        other = {
            **EVENTS[4],
            "event_id": "ev-sl-other",
            "account": "T9999OTHER",
            "workspace": "other-workspace",
            "body": "other tenant",
        }
        row = {
            "identity_key": "ik-sl-other",
            "received_ts": other["message_ts"],
            "ingested_ts": other["message_ts"],
            "permalink": "",
            "attachment_metadata_json": "[]",
            "raw_json": "{}",
            "source_path": "",
            "flags": "[]",
            "action_target_id": "",
            "action_account_id": "",
            "updated_at": 0.0,
            **other,
        }
        conn.execute(
            f"INSERT INTO context_events({', '.join(row)}) VALUES({', '.join(':' + key for key in row)})",
            row,
        )
        conn.commit()
        conn.close()

        read = self.conn()
        conversations = pm.list_conversations(read, platform="slack")["conversations"]
        matches = [item for item in conversations if item["conversation_id"] == "C0123ABCD"]
        self.assertEqual(len(matches), 2)
        self.assertEqual({item["account"] for item in matches}, {"T0123ACME", "T9999OTHER"})
        with self.assertRaises(pm.AmbiguousTargetError):
            pm.resolve_conversation(read, "slack", "C0123ABCD")
        selected = pm.resolve_conversation(
            read, "slack", "C0123ABCD", account="T9999OTHER", workspace="other-workspace"
        )
        self.assertEqual(selected["account"], "T9999OTHER")


class ShowTests(TempCaseMixin, unittest.TestCase):
    def test_show_includes_body_but_not_raw_json(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        self.assertEqual(event["body"], EVENTS[0]["body"])
        self.assertNotIn("raw_json", event)

    def test_show_raw_json_is_opt_in(self):
        event = pm.get_event(self.conn(), "ev-wa-1", include_raw=True)["event"]
        self.assertIn("secret_token", event["raw_json"])

    def test_show_missing_event(self):
        with self.assertRaises(pm.MessagingError) as ctx:
            pm.get_event(self.conn(), "nope")
        self.assertEqual(ctx.exception.code, "event_not_found")

    def test_context_db_opens_read_only(self):
        conn = self.conn()
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM context_events")


# --------------------------------------------------------------------------
# target validation
# --------------------------------------------------------------------------


class TargetValidationTests(unittest.TestCase):
    def test_whatsapp_targets(self):
        self.assertEqual(
            pm.normalize_target("whatsapp", ("111111111111111111" + "@g.us")), ("111111111111111111" + "@g.us")
        )
        self.assertEqual(pm.normalize_target("whatsapp", "49151123456@lid"), "49151123456@lid")
        self.assertEqual(
            pm.normalize_target("whatsapp", ("49151123456:12" + "@c.us")), ("49151123456:12" + "@c.us")
        )
        self.assertEqual(
            pm.normalize_target("whatsapp", "111111111111111111@newsletter"),
            "111111111111111111@newsletter",
        )
        self.assertEqual(
            pm.normalize_target("whatsapp", "+4915112345678"), ("4915112345678" + "@s.whatsapp.net")
        )
        for bad in (
            "",
            "not a jid",
            "someone@example.com",
            ("letters" + "@s.whatsapp.net"),
            ("4915112345678" + "@evil.net"),
        ):
            with self.assertRaises(pm.ValidationError, msg=bad):
                pm.normalize_target("whatsapp", bad)

    def test_signal_targets(self):
        self.assertEqual(pm.normalize_target("signal", "+4915100000000"), "+4915100000000")
        self.assertEqual(pm.normalize_target("signal", "group:aGVsbG8="), "group:aGVsbG8=")
        for bad in ("015112345678", "+0123", "group:", "hello"):
            with self.assertRaises(pm.ValidationError, msg=bad):
                pm.normalize_target("signal", bad)

    def test_control_characters_rejected(self):
        with self.assertRaises(pm.ValidationError):
            pm.normalize_target("whatsapp", ("4915112345678" + "@s.whatsapp.net\n/send"))

    def test_signal_group_params(self):
        self.assertEqual(pm.signal_target_params("group:abc"), {"groupId": "abc"})
        self.assertEqual(pm.signal_target_params("+491511"), {"recipient": ["+491511"]})


class DirectionCanonicalizationTests(unittest.TestCase):
    def test_canonical_directions_are_inbound_outbound(self):
        self.assertEqual(pm.DIRECTIONS, ("inbound", "outbound", "unknown"))

    def test_cli_aliases_map_to_canonical(self):
        self.assertEqual(pm.canonical_direction("incoming"), "inbound")
        self.assertEqual(pm.canonical_direction("outgoing"), "outbound")
        self.assertEqual(pm.canonical_direction("inbound"), "inbound")
        self.assertEqual(pm.canonical_direction("outbound"), "outbound")
        self.assertEqual(pm.canonical_direction(""), "unknown")
        self.assertEqual(pm.canonical_direction("sideways"), "unknown")

    def test_target_from_me_follows_direction(self):
        self.assertFalse(pm.direction_is_from_me("inbound"))
        self.assertFalse(pm.direction_is_from_me("incoming"))
        self.assertTrue(pm.direction_is_from_me("outbound"))
        self.assertTrue(pm.direction_is_from_me("outgoing"))
        # An unknown direction must not be silently treated as our own message.
        with self.assertRaises(pm.ValidationError):
            pm.direction_is_from_me("unknown")


class LoopbackTests(unittest.TestCase):
    def test_loopback_hosts_allowed(self):
        for url in ("http://127.0.0.1:3000", "http://localhost:8080", "http://[::1]:9000"):
            self.assertTrue(pm.assert_loopback(url))

    def test_non_loopback_refused(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.assert_loopback("http://10.0.0.5:3000", allow_override=False)
        self.assertEqual(ctx.exception.code, "non_loopback_refused")

    def test_non_loopback_allowed_with_explicit_optin(self):
        self.assertEqual(
            pm.assert_loopback("http://10.0.0.5:3000/", allow_override=True), "http://10.0.0.5:3000"
        )

    def test_non_loopback_env_optin(self):
        os.environ[pm.ALLOW_NON_LOOPBACK_ENV] = "1"
        self.addCleanup(os.environ.pop, pm.ALLOW_NON_LOOPBACK_ENV, None)
        self.assertTrue(pm.assert_loopback("http://10.0.0.5:3000"))

    def test_non_http_scheme_refused(self):
        for url in ("file:///etc/passwd", "https://127.0.0.1:3000"):
            with self.subTest(url=url), self.assertRaises(pm.ValidationError):
                pm.assert_loopback(url)

    def test_deceptive_or_ambiguous_loopback_urls_are_refused(self):
        for url in (
            "http://127.attacker.example:3000",
            "http://127.0.0.1.attacker.example:3000",
            "http://user:pass@127.0.0.1:3000",
            "http://127.0.0.1:3000/?next=http://evil.test",
            "http://127.0.0.1:3000/#fragment",
            "http://127.1:3000",
        ):
            with self.subTest(url=url), self.assertRaises(pm.ValidationError):
                pm.assert_loopback(url, allow_override=False)

    def test_localhost_is_resolved_once_and_pinned_to_loopback_literal(self):
        answers = [(pm.socket.AF_INET, pm.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 3000))]
        with mock.patch.object(pm.socket, "getaddrinfo", return_value=answers):
            self.assertEqual(pm.assert_loopback("http://localhost:3000"), "http://127.0.0.1:3000")

        non_loopback = [(pm.socket.AF_INET, pm.socket.SOCK_STREAM, 6, "", ("192.0.2.2", 3000))]
        with mock.patch.object(pm.socket, "getaddrinfo", return_value=non_loopback):
            with self.assertRaises(pm.ValidationError):
                pm.assert_loopback("http://localhost:3000")


# --------------------------------------------------------------------------
# payload construction
# --------------------------------------------------------------------------


class PayloadTests(TempCaseMixin, unittest.TestCase):
    def test_reply_from_event_resolves_quote_context(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        prepared = pm.build_payload(
            platform="whatsapp", action="reply", event=event, message="on my way"
        )
        self.assertEqual(prepared.target, ("4915112345678" + "@s.whatsapp.net"))
        self.assertEqual(prepared.payload["target_message_id"], "3EB0A1B2C3D4")
        self.assertEqual(prepared.payload["target_author"], ("4915112345678" + "@s.whatsapp.net"))
        self.assertIn("voucher", prepared.payload["quote_text"])
        self.assertEqual(prepared.source_event_id, "ev-wa-1")

    def test_whatsapp_reply_preserves_exact_event_quote_text(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        event["body"] = "line one\n  line two"
        prepared = pm.build_payload(
            platform="whatsapp", action="reply", event=event, message="reply"
        )
        self.assertEqual(prepared.payload["quote_text"], "line one\n  line two")

    def test_whatsapp_reply_respects_bridge_message_limit(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="whatsapp", action="reply", event=event, message="x" * 10001
            )
        self.assertEqual(ctx.exception.code, "message_too_large")

    def test_signal_reply_derives_timestamp_millis(self):
        event = pm.get_event(self.conn(), "ev-sig-1")["event"]
        prepared = pm.build_payload(platform="signal", action="reply", event=event, message="yes")
        expected = int(
            dt.datetime(2026, 7, 20, 17, 30, tzinfo=dt.timezone.utc).timestamp() * 1000
        )
        self.assertEqual(prepared.payload["target_timestamp_millis"], expected)
        self.assertEqual(prepared.payload["target_author"], "+4915100000000")

    def test_react_payload_marks_removal(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        react = pm.build_payload(platform="whatsapp", action="react", event=event, emoji="👍")
        unreact = pm.build_payload(platform="whatsapp", action="unreact", event=event, emoji="👍")
        self.assertFalse(react.payload["remove"])
        self.assertTrue(unreact.payload["remove"])

    def test_whatsapp_react_requires_one_non_whitespace_grapheme(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        for emoji in ("👍", "👍🏽", "👩🏽\u200d💻", "❤️"):
            with self.subTest(valid=emoji):
                prepared = pm.build_payload(
                    platform="whatsapp", action="react", event=event, emoji=emoji
                )
                self.assertEqual(prepared.payload["emoji"], emoji)

        for emoji in (" ", "\t", " 👍", "👍 ", "👍 👍", "👍👍"):
            with self.subTest(invalid=repr(emoji)), self.assertRaises(pm.ValidationError) as ctx:
                pm.build_payload(platform="whatsapp", action="react", event=event, emoji=emoji)
            self.assertEqual(ctx.exception.code, "invalid_reaction")

    def test_whatsapp_unreact_accepts_bounded_non_grapheme_identifier(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        for identifier in ("👍👍", "legacy-reaction-id"):
            with self.subTest(identifier=identifier):
                prepared = pm.build_payload(
                    platform="whatsapp", action="unreact", event=event, emoji=identifier
                )
                self.assertEqual(prepared.payload["emoji"], identifier)
                self.assertTrue(prepared.payload["remove"])

        for identifier in (" ", "bad reaction", "👍\n"):
            with self.subTest(identifier=repr(identifier)), self.assertRaises(pm.ValidationError):
                pm.build_payload(
                    platform="whatsapp", action="unreact", event=event, emoji=identifier
                )

    def test_reaction_validation_remains_platform_specific(self):
        signal_event = pm.get_event(self.conn(), "ev-sig-1")["event"]
        signal = pm.build_payload(
            platform="signal", action="react", event=signal_event, emoji="👍👍"
        )
        self.assertEqual(signal.payload["emoji"], "👍👍")

        slack_event = pm.get_event(self.conn(), "ev-sl-1")["event"]
        slack = pm.build_payload(
            platform="slack", action="react", event=slack_event, emoji=":party_parrot:"
        )
        self.assertEqual(slack.payload["emoji"], "party_parrot")

        long_canonical_name = "a" * 40
        slack_long = pm.build_payload(
            platform="slack",
            action="react",
            event=slack_event,
            emoji=f":{long_canonical_name}:",
        )
        self.assertEqual(slack_long.payload["emoji"], long_canonical_name)

    def test_platform_mismatch_between_event_and_flag(self):
        event = pm.get_event(self.conn(), "ev-sl-1")["event"]
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(platform="whatsapp", action="reply", event=event, message="hi")
        self.assertEqual(ctx.exception.code, "platform_mismatch")

    def test_missing_required_fields(self):
        with self.assertRaises(pm.ValidationError):
            pm.build_payload(platform="whatsapp", action="send", target="+4915112345678")
        with self.assertRaises(pm.ValidationError):
            pm.build_payload(
                platform="whatsapp", action="react", target="+4915112345678", emoji="👍"
            )
        with self.assertRaises(pm.ValidationError):
            pm.build_payload(
                platform="whatsapp",
                action="send",
                target="+4915112345678",
                message="hi",
                emoji="👍",
            )

    def test_signal_react_requires_author(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="signal",
                action="react",
                target="+4915100000000",
                emoji="👍",
                target_timestamp="2026-07-20T17:30:00Z",
            )
        self.assertEqual(ctx.exception.code, "missing_target_author")

    def test_manual_whatsapp_message_metadata_requires_validated_direction(self):
        common = {
            "platform": "whatsapp",
            "action": "react",
            "target": "+4915112345678",
            "target_message_id": "3EB0A1B2C3D4",
            "emoji": "👍",
        }
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(**common)
        self.assertEqual(ctx.exception.code, "missing_target_direction")
        prepared = pm.build_payload(**common, target_direction="incoming")
        self.assertFalse(prepared.payload["target_from_me"])
        with self.assertRaises(pm.ValidationError):
            pm.build_payload(**common, target_direction="unknown")

    def test_whatsapp_mark_read_allows_only_ordinary_user_and_group_jids(self):
        common = {
            "platform": "whatsapp",
            "action": "mark-read",
            "target_message_id": "3EB0A1B2C3D4",
            "target_direction": "inbound",
        }
        allowed = (
            ("4915112345678" + "@s.whatsapp.net"),
            ("4915112345678:12" + "@c.us"),
            "4915112345678@lid",
            ("111111111111111111" + "@g.us"),
        )
        for target in allowed:
            with self.subTest(allowed=target):
                prepared = pm.build_payload(target=target, **common)
                self.assertEqual(prepared.target, target)

        rejected = (
            "status@broadcast",
            "4915112345678@broadcast",
            "111111111111111111@newsletter",
            "someone@broadcast",
            "4915112345678@evil.example.com",
        )
        for target in rejected:
            with self.subTest(rejected=target), self.assertRaises(pm.ValidationError) as ctx:
                pm.build_payload(target=target, **common)
            if target in rejected[:3]:
                self.assertEqual(ctx.exception.code, "invalid_whatsapp_mark_read_target")

    def test_whatsapp_reply_requires_event_quote_and_edit_delete_require_outbound(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="whatsapp",
                action="reply",
                target="+4915112345678",
                target_message_id="3EB0A1B2C3D4",
                target_direction="inbound",
                message="reply",
            )
        self.assertEqual(ctx.exception.code, "whatsapp_reply_requires_event")

        inbound = pm.get_event(self.conn(), "ev-wa-2")["event"]
        for action, kwargs in (("edit", {"message": "fixed"}), ("delete", {})):
            with self.subTest(action=action), self.assertRaises(pm.ValidationError) as ctx:
                pm.build_payload(platform="whatsapp", action=action, event=inbound, **kwargs)
            self.assertEqual(ctx.exception.code, "target_not_outbound")

    def test_signal_sender_reference_must_be_e164_or_uuid(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="signal",
                action="react",
                target="+4915100000000",
                target_timestamp="1753031400000",
                target_author="not-a-signal-sender",
                emoji="👍",
            )
        self.assertEqual(ctx.exception.code, "invalid_signal_sender")

    def test_attachment_validation(self):
        good = self.tmp / "photo.jpg"
        good.write_bytes(b"jpeg")
        prepared = pm.build_payload(
            platform="whatsapp", action="send-file", target="+4915112345678", files=[str(good)]
        )
        self.assertEqual(prepared.payload["attachments"], [str(good.absolute())])

        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="whatsapp",
                action="send-file",
                target="+4915112345678",
                files=[str(self.tmp / "missing.jpg")],
            )
        self.assertEqual(ctx.exception.code, "attachment_missing")

        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="whatsapp", action="send-file", target="+4915112345678", files=[str(self.tmp)]
            )
        self.assertEqual(ctx.exception.code, "attachment_invalid")

    def test_message_length_bound(self):
        with self.assertRaises(pm.ValidationError):
            pm.build_payload(
                platform="whatsapp",
                action="send",
                target="+4915112345678",
                message="x" * (pm.MAX_MESSAGE_CHARS + 1),
            )

    def test_event_identity_and_connector_binding_are_hashed_into_payload(self):
        event = pm.get_event(self.conn(), "ev-sl-1", for_action=True)["event"]
        prepared = pm.build_payload(platform="slack", action="send", event=event, message="hi")
        self.assertEqual(
            prepared.payload["binding"]["target_identity"],
            {
                "platform": "slack",
                "account": "T0123ACME",
                "workspace": "acme",
                "conversation_id": "C0123ABCD",
            },
        )
        self.assertEqual(prepared.payload["binding"]["connector_id"], "hermes_slack_personal")

    def test_event_metadata_cannot_be_conflicted_or_mixed_with_manual_metadata(self):
        event = pm.get_event(self.conn(), "ev-wa-1", for_action=True)["event"]
        for kwargs in (
            {"target": "+4915199999999"},
            {"account": "wa-other"},
            {"workspace": "other"},
            {"target_message_id": "3EB0OTHER"},
            {"target_author": ("4915199999999" + "@s.whatsapp.net")},
            {"target_timestamp": "2026-07-20T18:00:01Z"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(pm.ValidationError):
                pm.build_payload(
                    platform="whatsapp", action="reply", event=event, message="hi", **kwargs
                )


class AttachmentIntegrityTests(TempCaseMixin, unittest.TestCase):
    def prepare_attachment(self, store, content=b"original"):
        source = self.tmp / "source.png"
        source.write_bytes(content)
        prepared = pm.build_payload(
            platform="whatsapp",
            action="send-file",
            target="+4915112345678",
            files=[str(source)],
            message="caption",
        )
        intent = store.create_intent(prepared)
        payload = store.verify_executable(store.load_intent(intent["intent_id"]))
        return source, intent, payload

    def test_prepare_rejects_symlinks(self):
        target = self.tmp / "target.png"
        link = self.tmp / "link.png"
        target.write_bytes(b"private")
        link.symlink_to(target)
        with self.assertRaises(pm.ValidationError) as ctx:
            prepared = pm.build_payload(
                platform="whatsapp",
                action="send-file",
                target="+4915112345678",
                files=[str(link)],
            )
            self.store().create_intent(prepared)
        self.assertEqual(ctx.exception.code, "attachment_symlink")

    def test_prepare_stages_private_hashed_copy_and_forgets_original_path(self):
        store = self.store()
        source, intent, payload = self.prepare_attachment(store, b"immutable bytes")
        attachment = payload["attachments"][0]
        staged = Path(attachment["staged_path"])

        self.assertNotEqual(staged, source)
        self.assertTrue(staged.is_relative_to(store.staging_root))
        self.assertEqual(staged.read_bytes(), b"immutable bytes")
        self.assertEqual(attachment["size"], len(b"immutable bytes"))
        self.assertEqual(attachment["sha256"], hashlib.sha256(b"immutable bytes").hexdigest())
        self.assertEqual(store.staging_root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(staged.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(str(source), store.load_intent(intent["intent_id"])["payload_json"])
        self.assertNotIn(str(staged), json.dumps(pm.summarize_intent(store.load_intent(intent["intent_id"]))))

    def test_original_mutation_does_not_change_dispatched_staged_copy(self):
        store = self.store()
        source, intent, payload = self.prepare_attachment(store, b"first")
        source.write_bytes(b"changed after prepare")
        staged = Path(payload["attachments"][0]["staged_path"])
        client = FakeClient({"success": True, "messageId": "MEDIA-1"})
        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(client),
        )

        self.assertEqual(result["status"], pm.STATUS_SUCCEEDED)
        self.assertEqual(client.calls[0][1]["filePath"], str(staged))
        self.assertNotEqual(client.calls[0][1]["filePath"], str(source))
        self.assertFalse(staged.exists())

    def test_tampered_staged_copy_fails_before_dispatch_and_is_purged(self):
        store = self.store()
        _source, intent, payload = self.prepare_attachment(store)
        staged = Path(payload["attachments"][0]["staged_path"])
        staged.write_bytes(b"tampered")
        client = FakeClient()

        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(client),
        )

        self.assertEqual(result["status"], pm.STATUS_FAILED)
        self.assertEqual(result["code"], "attachment_integrity_failed")
        self.assertEqual(client.calls, [])
        self.assertFalse(staged.exists())

    def test_expiry_and_uncertain_result_purge_staged_copy(self):
        clock = MutableClock(dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc))
        store = self.store(clock=clock)
        source = self.tmp / "expire.png"
        source.write_bytes(b"expire")
        prepared = pm.build_payload(
            platform="whatsapp", action="send-file", target="+4915112345678", files=[str(source)]
        )
        expired = store.create_intent(prepared, ttl_seconds=1)
        expired_payload = json.loads(expired["payload_json"])
        expired_path = Path(expired_payload["attachments"][0]["staged_path"])
        clock.advance(seconds=2)
        with self.assertRaises(pm.IntentStateError):
            store.verify_executable(store.load_intent(expired["intent_id"]))
        self.assertFalse(expired_path.exists())

        _source, uncertain, payload = self.prepare_attachment(store, b"uncertain")
        uncertain_path = Path(payload["attachments"][0]["staged_path"])
        result = pm.execute_intent(
            store,
            uncertain["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient(error=pm.TransportAmbiguousError("reset"))),
        )
        self.assertEqual(result["status"], pm.STATUS_UNCERTAIN)
        self.assertFalse(uncertain_path.exists())


# --------------------------------------------------------------------------
# intent store
# --------------------------------------------------------------------------


class IntentStoreTests(TempCaseMixin, unittest.TestCase):
    def test_private_permissions(self):
        store = self.store()
        self.assertEqual(store.db_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(store.db_path.stat().st_mode & 0o777, 0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(store.db_path) + suffix)
            if sidecar.exists():
                self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)

    def test_prepare_does_not_touch_the_network(self):
        store = self.store()

        def explode(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("prepare-action opened a socket")

        original = pm.socket.create_connection
        pm.socket.create_connection = explode
        self.addCleanup(setattr, pm.socket, "create_connection", original)
        intent = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        self.assertEqual(intent["status"], pm.STATUS_PENDING)

    def test_create_rejects_nested_dispatch_metadata_mutation(self):
        store = self.store()
        cases = (
            ("platform", "signal"),
            ("action", "typing-start"),
            ("target", ("4915199999999" + "@s.whatsapp.net")),
        )
        for field, replacement in cases:
            with self.subTest(field=field):
                prepared = pm.build_payload(
                    platform="whatsapp", action="send", target=("4915112345678" + "@s.whatsapp.net"), message="hi"
                )
                prepared.payload[field] = replacement
                with self.assertRaises(pm.ValidationError) as ctx:
                    store.create_intent(prepared)
                self.assertEqual(ctx.exception.code, "prepared_payload_mismatch")
        self.assertEqual(store.list_intents(), [])

    def test_post_creation_prepared_payload_mutation_cannot_change_stored_dispatch(self):
        store = self.store()
        prepared = pm.build_payload(
            platform="whatsapp", action="send", target=("4915112345678" + "@s.whatsapp.net"), message="hi"
        )
        intent = store.create_intent(prepared)
        prepared.payload["platform"] = "signal"
        prepared.payload["action"] = "typing-start"
        prepared.payload["target"] = ("4915199999999" + "@s.whatsapp.net")

        verified = store.verify_executable(store.load_intent(intent["intent_id"]))
        self.assertEqual(
            (verified["platform"], verified["action"], verified["target"]),
            ("whatsapp", "send", ("4915112345678" + "@s.whatsapp.net")),
        )

    def test_verify_rejects_rehashed_nested_dispatch_metadata_tampering_before_adapter_call(self):
        store = self.store()
        cases = (
            ("platform", "signal"),
            ("action", "typing-start"),
            ("target", ("4915199999999" + "@s.whatsapp.net")),
        )
        intents = []
        for field, replacement in cases:
            prepared = pm.build_payload(
                platform="whatsapp", action="send", target=("4915112345678" + "@s.whatsapp.net"), message="hi"
            )
            intents.append((field, replacement, store.create_intent(prepared)))

        conn = store.connect()
        self.addCleanup(conn.close)
        conn.execute("DROP TRIGGER action_intents_immutable")
        client = FakeClient()
        for field, replacement, created in intents:
            with self.subTest(field=field):
                payload = json.loads(created["payload_json"])
                payload[field] = replacement
                conn.execute(
                    "UPDATE action_intents SET payload_json=?, payload_hash=? WHERE intent_id=?",
                    (
                        pm._canonical_json(payload),
                        pm._payload_hash(payload),
                        created["intent_id"],
                    ),
                )
                with self.assertRaises(pm.IntentStateError) as ctx:
                    pm.execute_intent(
                        store,
                        created["intent_id"],
                        acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                        config=self.config(client),
                    )
                self.assertEqual(ctx.exception.code, "intent_tampered")
                self.assertEqual(
                    store.load_intent(created["intent_id"])["status"], pm.STATUS_PENDING
                )
        self.assertEqual(client.calls, [])

    def test_signal_intent_requires_execution_account_at_prepare_time(self):
        prepared = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        )
        with self.assertRaises(pm.ValidationError) as ctx:
            self.store().create_intent(prepared)
        self.assertEqual(ctx.exception.code, "signal_account_missing")

    def test_intent_id_is_random_and_hash_is_exact(self):
        store = self.store()
        first = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        second = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        self.assertNotEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(first["payload_hash"], second["payload_hash"])
        different = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi!"
        )
        self.assertNotEqual(first["payload_hash"], different["payload_hash"])

    def test_ttl_is_bounded(self):
        store = self.store()
        prepared = pm.build_payload(
            platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        intent = store.create_intent(prepared, ttl_seconds=100_000)
        created = pm._parse_iso(intent["created_ts"])
        expires = pm._parse_iso(intent["expires_ts"])
        self.assertEqual((expires - created).total_seconds(), pm.MAX_TTL_SECONDS)

    def test_payload_is_immutable_in_sql(self):
        store = self.store()
        intent = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        conn = store.connect()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE action_intents SET payload_json = ? WHERE intent_id = ?",
                ('{"action":"send"}', intent["intent_id"]),
            )

    def test_audit_is_append_only(self):
        store = self.store()
        intent = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        conn = store.connect()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM action_audit WHERE intent_id = ?", (intent["intent_id"],))

    def test_audit_never_stores_message_bodies_or_targets(self):
        store = self.store()
        secret = "meet me at the safehouse"
        intent = self.prepare(
            store,
            platform="whatsapp",
            action="send",
            target="+4915112345678",
            message=secret,
        )
        conn = store.connect()
        self.addCleanup(conn.close)
        dump = json.dumps([dict(row) for row in conn.execute("SELECT * FROM action_audit")])
        self.assertNotIn(secret, dump)
        self.assertNotIn("4915112345678", dump)
        self.assertIn(intent["payload_hash"], dump)

    def test_audit_detail_rejects_arbitrary_remote_text(self):
        store = self.store()
        secret = "remote said private body token=abc123"
        created = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        intent = store.load_intent(created["intent_id"])
        store.audit_only(intent, "diagnostic", pm.STATUS_PENDING, secret)
        trail = store.audit_trail(intent["intent_id"])

        self.assertNotIn(secret, json.dumps(trail))
        self.assertEqual(trail[-1]["detail"], "detail_redacted")

    def test_intent_summaries_use_private_keyed_target_handles(self):
        store = self.store()
        first = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="one"
        )
        second = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="two"
        )
        summary_one = pm.summarize_intent(store.load_intent(first["intent_id"]))
        summary_two = pm.summarize_intent(store.load_intent(second["intent_id"]))
        target = ("4915112345678" + "@s.whatsapp.net")

        self.assertNotIn("target", summary_one)
        self.assertEqual(summary_one["target_handle"], summary_two["target_handle"])
        self.assertNotEqual(
            summary_one["target_handle"], hashlib.sha256(target.encode()).hexdigest()[:32]
        )
        self.assertEqual(store.handle_key_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(store.handle_key_path.read_bytes()), 32)

    def test_expired_intent_is_refused_and_marked(self):
        moment = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc)
        clock = lambda: moment  # noqa: E731
        store = self.store(clock=clock)
        prepared = pm.build_payload(
            platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        intent = store.create_intent(prepared, ttl_seconds=60)

        later = moment + dt.timedelta(seconds=61)
        store.clock = lambda: later
        with self.assertRaises(pm.IntentStateError) as ctx:
            store.verify_executable(store.load_intent(intent["intent_id"]))
        self.assertEqual(ctx.exception.code, "intent_expired")
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_EXPIRED)

    def test_tampered_hash_is_refused(self):
        store = self.store()
        intent = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        conn = store.connect()
        self.addCleanup(conn.close)
        # Simulate an attacker who dropped the immutability trigger first.
        conn.execute("DROP TRIGGER action_intents_immutable")
        conn.execute(
            "UPDATE action_intents SET payload_json = ? WHERE intent_id = ?",
            (
                json.dumps(
                    {
                        "platform": "whatsapp",
                        "action": "send",
                        "target": ("4915199999999" + "@s.whatsapp.net"),
                        "message": "send money",
                    }
                ),
                intent["intent_id"],
            ),
        )
        with self.assertRaises(pm.IntentStateError) as ctx:
            store.verify_executable(store.load_intent(intent["intent_id"]))
        self.assertEqual(ctx.exception.code, "intent_tampered")

    def test_source_event_provenance_is_inside_hashed_binding(self):
        store = self.store()
        event = pm.get_event(self.conn(), "ev-wa-1", for_action=True)["event"]
        created = self.prepare(store, platform="whatsapp", action="reply", event=event, message="hi")
        payload = json.loads(created["payload_json"])
        self.assertEqual(payload["binding"]["source_event_id"], "ev-wa-1")

        conn = store.connect()
        self.addCleanup(conn.close)
        conn.execute("DROP TRIGGER action_intents_immutable")
        conn.execute(
            "UPDATE action_intents SET source_event_id=? WHERE intent_id=?",
            ("ev-wa-elsewhere", created["intent_id"]),
        )
        with self.assertRaises(pm.IntentStateError) as ctx:
            store.verify_executable(store.load_intent(created["intent_id"]))
        self.assertEqual(ctx.exception.code, "intent_tampered")

    def test_claim_is_local_at_most_once(self):
        store = self.store()
        intent_row = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        intent = store.load_intent(intent_row["intent_id"])
        store.claim(intent)
        with self.assertRaises(pm.IntentStateError) as ctx:
            store.claim(intent)
        self.assertEqual(ctx.exception.code, "intent_already_claimed")

    def test_claim_atomically_checks_pending_and_unexpired(self):
        clock = MutableClock(dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc))
        store = self.store(clock=clock)
        prepared = pm.build_payload(
            platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        intent = store.create_intent(prepared, ttl_seconds=1)
        loaded = store.load_intent(intent["intent_id"])
        # Simulates expiry after a caller's earlier validation but before claim.
        store.verify_executable(loaded)
        clock.advance(seconds=2)
        with self.assertRaises(pm.IntentStateError) as ctx:
            store.claim(loaded)
        self.assertEqual(ctx.exception.code, "intent_expired")
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_EXPIRED)
        events = [row["event"] for row in store.audit_trail(intent["intent_id"])]
        self.assertEqual(events, ["prepared", "expired"])

    def test_finish_checks_transition_and_never_writes_contradictory_audit(self):
        store = self.store()
        created = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        intent = store.load_intent(created["intent_id"])
        store.claim(intent)
        store.finish(intent, status=pm.STATUS_SUCCEEDED, result_code="acknowledged")
        with self.assertRaises(pm.IntentStateError):
            store.finish(intent, status=pm.STATUS_UNCERTAIN, result_code="late_result")
        result_rows = [
            row for row in store.audit_trail(intent["intent_id"]) if row["event"] == "result"
        ]
        self.assertEqual(len(result_rows), 1)
        self.assertEqual(result_rows[0]["status"], pm.STATUS_SUCCEEDED)

    def test_reconcile_moves_only_stale_dispatches_to_uncertain_without_replay(self):
        clock = MutableClock(dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc))
        store = self.store(clock=clock)
        stale = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="old"
        )
        store.claim(store.load_intent(stale["intent_id"]))
        clock.advance(minutes=10)
        fresh = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="fresh"
        )
        store.claim(store.load_intent(fresh["intent_id"]))

        result = store.reconcile_stale(age_seconds=300)

        self.assertEqual(result["reconciled"], 1)
        self.assertEqual(store.load_intent(stale["intent_id"])["status"], pm.STATUS_UNCERTAIN)
        self.assertEqual(store.load_intent(fresh["intent_id"])["status"], pm.STATUS_IN_FLIGHT)

    def test_short_retention_purges_old_terminal_payload_but_keeps_audit(self):
        clock = MutableClock(dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc))
        store = self.store(clock=clock)
        created = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="secret"
        )
        intent = store.load_intent(created["intent_id"])
        store.claim(intent)
        store.finish(intent, status=pm.STATUS_SUCCEEDED, result_code="acknowledged")
        clock.advance(hours=25)

        result = store.purge_terminal(retention_seconds=24 * 60 * 60)

        self.assertEqual(result["purged_intents"], 1)
        with self.assertRaises(pm.MessagingError):
            store.load_intent(created["intent_id"])
        self.assertTrue(store.audit_trail(created["intent_id"]))

    def test_purge_expires_pending_intent_and_removes_old_orphan_staging(self):
        clock = MutableClock(dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc))
        store = self.store(clock=clock)
        source = self.tmp / "pending.png"
        source.write_bytes(b"pending")
        intent = store.create_intent(
            pm.build_payload(
                platform="whatsapp",
                action="send-file",
                target="+4915112345678",
                files=[str(source)],
            ),
            ttl_seconds=1,
        )
        payload = json.loads(intent["payload_json"])
        staged = Path(payload["attachments"][0]["staged_path"])
        orphan = store.staging_root / ("pmi_" + "f" * 32)
        orphan.mkdir(mode=0o700)
        (orphan / "attachment-0").write_bytes(b"orphan")
        old_epoch = clock.value.timestamp() - (25 * 60 * 60)
        os.utime(orphan, (old_epoch, old_epoch))
        clock.advance(hours=25)

        result = store.purge_terminal(retention_seconds=24 * 60 * 60)

        self.assertEqual(result["expired_intents"], 1)
        self.assertEqual(result["purged_staged_artifacts"], 2)
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_EXPIRED)
        self.assertFalse(staged.exists())
        self.assertFalse(orphan.exists())


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


class ExecutionTests(TempCaseMixin, unittest.TestCase):
    def _whatsapp_intent(self, store, action="send", **kwargs):
        defaults = dict(platform="whatsapp", action=action, target="+4915112345678")
        defaults.update(kwargs)
        return self.prepare(store, **defaults)

    def test_acknowledgement_is_required_verbatim(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient()
        for bad in ("", "yes", "user-approved", "explicit_user_request", "EXPLICIT-USER-REQUEST"):
            with self.assertRaises(pm.AcknowledgementError, msg=bad):
                pm.execute_intent(
                    store, intent["intent_id"], acknowledgement=bad, config=self.config(client)
                )
        self.assertEqual(client.calls, [])
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_PENDING)

    def test_successful_whatsapp_send(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="on my way")
        client = FakeClient({"success": True, "id": "3EB0NEW"})
        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(client),
        )
        self.assertEqual(result["status"], pm.STATUS_SUCCEEDED)
        self.assertEqual(result["remote_ref"], "3EB0NEW")
        path, body = client.calls[0]
        self.assertEqual(path, "/send")
        self.assertEqual(body, {"chatId": ("4915112345678" + "@s.whatsapp.net"), "message": "on my way"})
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_SUCCEEDED)

    def test_duplicate_execution_is_refused(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient()
        pm.execute_intent(
            store, intent["intent_id"], acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN, config=self.config(client)
        )
        with self.assertRaises(pm.IntentStateError) as ctx:
            pm.execute_intent(
                store,
                intent["intent_id"],
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "intent_not_pending")
        self.assertEqual(len(client.calls), 1)

    def test_timeout_marks_uncertain_and_never_replays(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient(error=pm.TransportAmbiguousError("bridge request timed out"))
        result = pm.execute_intent(
            store, intent["intent_id"], acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN, config=self.config(client)
        )
        self.assertEqual(result["status"], pm.STATUS_UNCERTAIN)
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_UNCERTAIN)
        with self.assertRaises(pm.IntentStateError):
            pm.execute_intent(
                store,
                intent["intent_id"],
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=self.config(client),
            )

    def test_preflight_failure_marks_failed(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient(error=pm.TransportPreflightError("cannot reach bridge: ConnectionRefusedError"))
        result = pm.execute_intent(
            store, intent["intent_id"], acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN, config=self.config(client)
        )
        self.assertEqual(result["status"], pm.STATUS_FAILED)
        # Failed is still terminal: a retry needs a fresh, freshly-authorized intent.
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_FAILED)

    def test_unexpected_exception_is_treated_as_uncertain(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient(error=RuntimeError("boom"))
        result = pm.execute_intent(
            store, intent["intent_id"], acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN, config=self.config(client)
        )
        self.assertEqual(result["status"], pm.STATUS_UNCERTAIN)

    def test_dry_run_neither_claims_nor_calls(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient()
        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(client),
            dry_run=True,
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["would_call"]["path"], "/send")
        self.assertEqual(client.calls, [])
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_PENDING)

    def test_dry_run_output_does_not_echo_the_message(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="confidential text")
        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
            dry_run=True,
        )
        self.assertNotIn("confidential", json.dumps(result))

    def test_negative_ack_after_dispatch_marks_uncertain(self):
        store = self.store()
        intent = self._whatsapp_intent(store, message="hi")
        client = FakeClient({"success": False, "error": "no session"})
        result = pm.execute_intent(
            store, intent["intent_id"], acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN, config=self.config(client)
        )
        self.assertEqual(result["status"], pm.STATUS_UNCERTAIN)
        self.assertEqual(result["code"], "remote_rejected")

    def test_endpoint_and_signal_account_cannot_be_substituted_at_execution(self):
        store = self.store()
        wa = self.prepare(
            store,
            platform="whatsapp",
            action="send",
            target="+4915112345678",
            account="wa-primary",
            message="hi",
        )
        client = FakeClient()
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.execute_intent(
                store,
                wa["intent_id"],
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=pm.ExecutionConfig(
                    whatsapp_url="http://127.0.0.1:3999",
                    client_factory=lambda *_args: client,
                ),
            )
        self.assertEqual(ctx.exception.code, "execution_binding_mismatch")
        self.assertEqual(client.calls, [])

        event = pm.get_event(self.conn(), "ev-sig-1", for_action=True)["event"]
        signal_intent = self.prepare(store, platform="signal", action="send", event=event, message="hi")
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.execute_intent(
                store,
                signal_intent["intent_id"],
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=pm.ExecutionConfig(
                    signal_account="+4915999999999",
                    client_factory=lambda *_args: client,
                ),
            )
        self.assertEqual(ctx.exception.code, "execution_binding_mismatch")
        self.assertEqual(client.calls, [])


class WhatsAppAdapterContractTests(TempCaseMixin, unittest.TestCase):
    """Every action body must match scripts/whatsapp-bridge/bridge.js exactly.

    The bridge rejects a request whose body contains any unsupported field, so a
    body that is a superset of the contract fails at the bridge. These tests
    assert full dict equality, not just presence of individual keys.
    """

    def adapter(self):
        return pm.WhatsAppAdapter(FakeClient())

    def group_payload(self, action, **kwargs):
        # ev-wa-2 is an inbound group message from 4915199999999@lid.
        event = pm.get_event(self.conn(), "ev-wa-2")["event"]
        return pm.build_payload(platform="whatsapp", action=action, event=event, **kwargs).payload

    def outbound_group_payload(self, action, **kwargs):
        event = pm.get_event(self.conn(), "ev-wa-4")["event"]
        return pm.build_payload(platform="whatsapp", action=action, event=event, **kwargs).payload

    def test_route_map_covers_every_action(self):
        self.assertEqual(set(pm.WhatsAppAdapter.ROUTES), set(pm.ACTIONS))

    def test_send_body_is_chatid_and_message(self):
        payload = pm.build_payload(
            platform="whatsapp", action="send", target="+4915112345678", message="on my way"
        ).payload
        self.assertEqual(
            self.adapter().build_request(payload),
            ("/send", {"chatId": ("4915112345678" + "@s.whatsapp.net"), "message": "on my way"}),
        )

    def test_send_media_single_attachment_body(self):
        photo = self.tmp / "a.png"
        photo.write_bytes(b"png")
        prepared = pm.build_payload(
            platform="whatsapp",
            action="send-file",
            target="+4915112345678",
            files=[str(photo)],
            message="look",
        )
        store = self.store()
        intent = store.create_intent(prepared)
        payload = store.verify_executable(store.load_intent(intent["intent_id"]))
        path, body = self.adapter().build_request(payload)
        self.assertEqual(path, "/send-media")
        self.assertEqual(set(body), {"chatId", "filePath", "caption"})
        self.assertEqual(body["chatId"], ("4915112345678" + "@s.whatsapp.net"))
        self.assertEqual(body["caption"], "look")
        # Exactly the staged/validated single path, never a list.
        self.assertIsInstance(body["filePath"], str)

    def test_send_media_rejects_multiple_attachments(self):
        a = self.tmp / "a.png"
        b = self.tmp / "b.png"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(
                platform="whatsapp",
                action="send-file",
                target="+4915112345678",
                files=[str(a), str(b)],
            )
        self.assertEqual(ctx.exception.code, "too_many_attachments")

    def test_reply_body_matches_contract(self):
        path, body = self.adapter().build_request(self.group_payload("reply", message="ok"))
        self.assertEqual(path, "/reply")
        self.assertEqual(
            body,
            {
                "chatId": ("111111111111111111" + "@g.us"),
                "messageId": "3EB0FFFFFFFF",
                "message": "ok",
                "quotedText": "standup moved to 10:00",
                "participant": "4915199999999@lid",
                "fromMe": False,
            },
        )

    def test_reply_dm_omits_participant(self):
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        payload = pm.build_payload(
            platform="whatsapp", action="reply", event=event, message="omw"
        ).payload
        _, body = self.adapter().build_request(payload)
        self.assertNotIn("participant", body)
        self.assertEqual(body["quotedText"], EVENTS[0]["body"])
        self.assertFalse(body["fromMe"])

    def test_reply_fromme_follows_outbound_direction(self):
        event = pm.get_event(self.conn(), "ev-wa-4")["event"]  # outbound
        payload = pm.build_payload(
            platform="whatsapp", action="reply", event=event, message="follow up"
        ).payload
        _, body = self.adapter().build_request(payload)
        self.assertTrue(body["fromMe"])

    def test_reaction_body_matches_contract(self):
        _, react = self.adapter().build_request(self.group_payload("react", emoji="🔥"))
        self.assertEqual(
            react,
            {
                "chatId": ("111111111111111111" + "@g.us"),
                "messageId": "3EB0FFFFFFFF",
                "emoji": "🔥",
                "participant": "4915199999999@lid",
                "fromMe": False,
                "remove": False,
            },
        )
        _, unreact = self.adapter().build_request(self.group_payload("unreact", emoji="🔥"))
        self.assertTrue(unreact["remove"])
        self.assertEqual(unreact["emoji"], "🔥")

    def test_edit_body_matches_contract(self):
        path, body = self.adapter().build_request(
            self.outbound_group_payload("edit", message="fixed")
        )
        self.assertEqual(path, "/edit")
        self.assertEqual(
            body,
            {
                "chatId": ("333333333333333333" + "@g.us"),
                "messageId": "3EB0BBBB2222",
                "message": "fixed",
            },
        )

    def test_delete_body_matches_contract(self):
        path, body = self.adapter().build_request(self.outbound_group_payload("delete"))
        self.assertEqual(path, "/delete")
        self.assertEqual(
            body,
            {
                "chatId": ("333333333333333333" + "@g.us"),
                "messageId": "3EB0BBBB2222",
                "participant": ("4915100000003" + "@s.whatsapp.net"),
            },
        )

    def test_mark_read_body_matches_contract(self):
        path, body = self.adapter().build_request(self.group_payload("mark-read"))
        self.assertEqual(path, "/read")
        self.assertEqual(
            body,
            {
                "chatId": ("111111111111111111" + "@g.us"),
                "messageId": "3EB0FFFFFFFF",
                "participant": "4915199999999@lid",
                "fromMe": False,
            },
        )

    def test_typing_body_matches_contract(self):
        start = pm.build_payload(
            platform="whatsapp", action="typing-start", target="+4915112345678"
        ).payload
        stop = pm.build_payload(
            platform="whatsapp", action="typing-stop", target="+4915112345678"
        ).payload
        self.assertEqual(
            self.adapter().build_request(start),
            ("/typing", {"chatId": ("4915112345678" + "@s.whatsapp.net")}),
        )
        self.assertEqual(
            self.adapter().build_request(stop),
            ("/typing", {"chatId": ("4915112345678" + "@s.whatsapp.net"), "stop": True}),
        )

    def test_positive_ack_is_required_and_send_reply_require_message_id(self):
        send = pm.build_payload(
            platform="whatsapp", action="send", target="+4915112345678", message="hi"
        ).payload
        event = pm.get_event(self.conn(), "ev-wa-1")["event"]
        reply = pm.build_payload(
            platform="whatsapp", action="reply", event=event, message="hi"
        ).payload
        reaction = pm.build_payload(
            platform="whatsapp", action="react", event=event, emoji="👍"
        ).payload
        for response in ({}, {"ok": True}, {"success": True}):
            with self.subTest(response=response), self.assertRaises(pm.TransportAmbiguousError):
                pm.WhatsAppAdapter(FakeClient(response)).execute(send)
        with self.assertRaises(pm.TransportAmbiguousError):
            pm.WhatsAppAdapter(FakeClient({"success": True})).execute(reply)
        self.assertEqual(
            pm.WhatsAppAdapter(FakeClient({"success": True})).execute(reaction),
            {"remote_ref": ""},
        )
        for response in (
            {"success": True, "messageId": "bad id with spaces"},
            {"success": True, "messageId": "GOOD-ID", "error": "contradictory"},
        ):
            with self.subTest(response=response), self.assertRaises(pm.TransportAmbiguousError):
                pm.WhatsAppAdapter(FakeClient(response)).execute(send)


class SignalAdapterTests(TempCaseMixin, unittest.TestCase):
    ACCOUNT = "+4915000000000"

    def adapter(self):
        return pm.SignalAdapter(FakeClient(), self.ACCOUNT)

    def payload(self, event_id, action, **kwargs):
        event = pm.get_event(self.conn(), event_id)["event"]
        return pm.build_payload(platform="signal", action=action, event=event, **kwargs).payload

    def adapter_with_result(self, result):
        def response(_path, request):
            return {"jsonrpc": "2.0", "id": request["id"], "result": result}

        return pm.SignalAdapter(FakeClient(response), self.ACCOUNT)

    def test_account_must_be_configured_and_valid(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.SignalAdapter(FakeClient(), "")
        self.assertEqual(ctx.exception.code, "signal_account_missing")
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.SignalAdapter(FakeClient(), "0151000")
        self.assertEqual(ctx.exception.code, "signal_account_invalid")

    def test_send_to_dm_uses_recipient(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hello"
        ).payload
        request = self.adapter().build_request(payload)
        self.assertEqual(request["method"], "send")
        self.assertEqual(request["params"]["recipient"], ["+4915100000000"])
        self.assertEqual(request["params"]["account"], self.ACCOUNT)
        self.assertNotIn("groupId", request["params"])

    def test_send_to_group_uses_group_id(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="group:aGVsbG8=", message="hello"
        ).payload
        request = self.adapter().build_request(payload)
        self.assertEqual(request["params"]["groupId"], "aGVsbG8=")
        self.assertNotIn("recipient", request["params"])

    def test_reply_sets_quote_fields(self):
        request = self.adapter().build_request(self.payload("ev-sig-1", "reply", message="sure"))
        params = request["params"]
        self.assertEqual(request["method"], "send")
        self.assertEqual(params["quoteAuthor"], "+4915100000000")
        self.assertEqual(params["quoteTimestamp"], pm._epoch_millis("2026-07-20T17:30:00Z"))
        self.assertEqual(params["quoteMessage"], "dinner at eight?")

    def test_reaction_uses_send_reaction(self):
        request = self.adapter().build_request(self.payload("ev-sig-1", "react", emoji="❤️"))
        self.assertEqual(request["method"], "sendReaction")
        self.assertEqual(request["params"]["targetAuthor"], "+4915100000000")
        self.assertNotIn("remove", request["params"])

        removal = self.adapter().build_request(self.payload("ev-sig-1", "unreact", emoji="❤️"))
        self.assertTrue(removal["params"]["remove"])

    def test_edit_and_delete(self):
        event = pm.get_event(self.conn(), "ev-sig-1", for_action=True)["event"]
        event["direction"] = "outbound"
        event["sender_id"] = self.ACCOUNT
        edit_payload = pm.build_payload(
            platform="signal", action="edit", event=event, message="typo fixed"
        ).payload
        delete_payload = pm.build_payload(platform="signal", action="delete", event=event).payload
        edit = self.adapter().build_request(edit_payload)
        self.assertEqual(edit["method"], "send")
        self.assertEqual(edit["params"]["editTimestamp"], pm._epoch_millis("2026-07-20T17:30:00Z"))

        delete = self.adapter().build_request(delete_payload)
        self.assertEqual(delete["method"], "remoteDelete")
        self.assertEqual(delete["params"]["targetTimestamp"], pm._epoch_millis("2026-07-20T17:30:00Z"))

        inbound = pm.get_event(self.conn(), "ev-sig-1", for_action=True)["event"]
        for action, kwargs in (("edit", {"message": "no"}), ("delete", {})):
            with self.subTest(action=action):
                with self.assertRaises(pm.ValidationError) as raised:
                    pm.build_payload(platform="signal", action=action, event=inbound, **kwargs)
                self.assertEqual(raised.exception.code, "target_not_outbound")

    def test_mark_read_dm_uses_send_receipt(self):
        request = self.adapter().build_request(self.payload("ev-sig-1", "mark-read"))
        self.assertEqual(request["method"], "sendReceipt")
        self.assertEqual(request["params"]["type"], "read")

    def test_mark_read_group_is_unsupported_not_guessed(self):
        with self.assertRaises(pm.UnsupportedActionError) as ctx:
            self.adapter().build_request(self.payload("ev-sig-2", "mark-read"))
        self.assertEqual(ctx.exception.code, "unsupported_on_platform")

    def test_typing_stop_sets_stop_flag(self):
        start = pm.build_payload(
            platform="signal", action="typing-start", target="+4915100000000"
        ).payload
        stop = pm.build_payload(
            platform="signal", action="typing-stop", target="+4915100000000"
        ).payload
        self.assertNotIn("stop", self.adapter().build_request(start)["params"])
        self.assertTrue(self.adapter().build_request(stop)["params"]["stop"])

    def test_jsonrpc_error_is_a_deterministic_rejection(self):
        def response(_path, request):
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32602, "message": "unknown recipient"},
            }

        adapter = pm.SignalAdapter(
            FakeClient(response),
            self.ACCOUNT,
        )
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        with self.assertRaises(pm.RemoteRejectedError):
            adapter.execute(payload)

    def test_success_returns_timestamp(self):
        def response(_path, request):
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"timestamp": 1753031400000},
            }

        adapter = pm.SignalAdapter(FakeClient(response), self.ACCOUNT)
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        self.assertEqual(adapter.execute(payload)["remote_ref"], "1753031400000")

    def test_signal_cli_all_success_fixture_returns_timestamp(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="group:aGVsbG8=", message="hi"
        ).payload
        result = SIGNAL_CLI_RESULTS["all_success"]
        self.assertEqual(
            self.adapter_with_result(result).execute(payload)["remote_ref"],
            str(result["timestamp"]),
        )

    def test_signal_cli_single_recipient_total_failure_is_rejected_without_details(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        result = SIGNAL_CLI_RESULTS["single_total_failure"]
        with self.assertRaises(pm.RemoteRejectedError) as ctx:
            self.adapter_with_result(result).execute(payload)

        rendered = f"{ctx.exception} {ctx.exception.details!r}"
        for private_value in (
            "fixture-private-recipient-error",
            "+15550000001",
            "bbbbbbbb-0000-4000-8000-000000000001",
        ):
            self.assertNotIn(private_value, rendered)

    def test_each_explicit_recipient_failure_shape_prevents_success(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        failure_entries = (
            {"type": "NETWORK_FAILURE"},
            {"success": False},
            {"error": "fixture-private-error-only"},
        )
        for entry in failure_entries:
            result = {"timestamp": 1712345678004, "results": [entry]}
            with self.subTest(entry=entry), self.assertRaises(pm.RemoteRejectedError) as ctx:
                self.adapter_with_result(result).execute(payload)
            self.assertNotIn("fixture-private-error-only", str(ctx.exception))

    def test_signal_cli_mixed_partial_failure_is_ambiguous_without_details(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="group:aGVsbG8=", message="hi"
        ).payload
        result = SIGNAL_CLI_RESULTS["mixed_partial_failure"]
        with self.assertRaises(pm.TransportAmbiguousError) as ctx:
            self.adapter_with_result(result).execute(payload)

        rendered = f"{ctx.exception} {ctx.exception.details!r}"
        for private_value in (
            "fixture-private-partial-failure",
            "+15550000002",
            "+15550000003",
            "cccccccc-0000-4000-8000-000000000002",
        ):
            self.assertNotIn(private_value, rendered)

    def test_signal_cli_partial_failure_marks_intent_uncertain_and_not_replayable(self):
        store = self.store()
        event = pm.get_event(self.conn(), "ev-sig-2", for_action=True)["event"]
        intent = self.prepare(store, platform="signal", action="send", event=event, message="hi")
        result = SIGNAL_CLI_RESULTS["mixed_partial_failure"]
        client = self.adapter_with_result(result).client

        outcome = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(client),
        )

        self.assertEqual(outcome["status"], pm.STATUS_UNCERTAIN)
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_UNCERTAIN)
        with self.assertRaises(pm.IntentStateError) as ctx:
            pm.execute_intent(
                store,
                intent["intent_id"],
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "intent_not_pending")
        self.assertEqual(len(client.calls), 1)

    def test_signal_cli_group_failure_is_never_a_deterministic_single_recipient_rejection(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="group:aGVsbG8=", message="hi"
        ).payload
        result = SIGNAL_CLI_RESULTS["single_total_failure"]
        with self.assertRaises(pm.TransportAmbiguousError):
            self.adapter_with_result(result).execute(payload)

    def test_signal_cli_malformed_results_are_ambiguous(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        malformed_results = (
            SIGNAL_CLI_RESULTS["malformed_results"],
            {"timestamp": 1712345678005, "results": []},
            {"timestamp": 1712345678006, "results": {"type": "SUCCESS"}},
            {"timestamp": 1712345678007, "results": ["SUCCESS"]},
        )
        for result in malformed_results:
            with self.subTest(result=result), self.assertRaises(pm.TransportAmbiguousError):
                self.adapter_with_result(result).execute(payload)

    def test_fire_and_forget_accepts_any_present_non_null_result(self):
        payloads = (
            pm.build_payload(
                platform="signal", action="typing-start", target="+4915100000000"
            ).payload,
            self.payload("ev-sig-1", "mark-read"),
        )
        for payload in payloads:
            for result in ({}, [], False, 0, "", True, {"unexpected": "but-present"}):
                with self.subTest(action=payload["action"], result=result):
                    self.assertEqual(
                        self.adapter_with_result(result).execute(payload), {"remote_ref": ""}
                    )

    def test_fire_and_forget_rejects_missing_null_or_error_result(self):
        payload = pm.build_payload(
            platform="signal", action="typing-start", target="+4915100000000"
        ).payload

        def missing(_path, request):
            return {"jsonrpc": "2.0", "id": request["id"]}

        with self.assertRaises(pm.TransportAmbiguousError):
            pm.SignalAdapter(FakeClient(missing), self.ACCOUNT).execute(payload)
        with self.assertRaises(pm.TransportAmbiguousError):
            self.adapter_with_result(None).execute(payload)

        def error(_path, request):
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -1, "message": "fixture-private-error"},
                "result": {},
            }

        with self.assertRaises(pm.RemoteRejectedError) as ctx:
            pm.SignalAdapter(FakeClient(error), self.ACCOUNT).execute(payload)
        self.assertNotIn("fixture-private-error", str(ctx.exception))

    def test_signal_ack_requires_jsonrpc_matching_id_and_expected_result(self):
        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        responses = (
            {},
            {"jsonrpc": "2.0", "id": "wrong", "result": {"timestamp": 1}},
            {"jsonrpc": "1.0", "id": "dynamic", "result": {"timestamp": 1}},
            {"jsonrpc": "2.0", "id": "dynamic"},
            {"jsonrpc": "2.0", "id": "dynamic", "result": {}},
            {"jsonrpc": "2.0", "id": "dynamic", "result": {"timestamp": "not-a-time"}},
        )
        for template in responses:
            def response(_path, request, template=template):
                value = dict(template)
                if value.get("id") == "dynamic":
                    value["id"] = request["id"]
                return value

            with self.subTest(response=template), self.assertRaises(pm.TransportAmbiguousError):
                pm.SignalAdapter(FakeClient(response), self.ACCOUNT).execute(payload)

    def test_signal_jsonrpc_error_does_not_echo_remote_text(self):
        secret = "remote-secret-message-body"

        def response(_path, request):
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -1, "message": secret},
            }

        payload = pm.build_payload(
            platform="signal", action="send", target="+4915100000000", message="hi"
        ).payload
        with self.assertRaises(pm.RemoteRejectedError) as ctx:
            pm.SignalAdapter(FakeClient(response), self.ACCOUNT).execute(payload)
        self.assertNotIn(secret, str(ctx.exception))

    def test_unsupported_action_marks_failed_without_claiming_delivery(self):
        store = self.store()
        event = pm.get_event(self.conn(), "ev-sig-2", for_action=True)["event"]
        intent = self.prepare(store, platform="signal", action="mark-read", event=event)
        client = FakeClient()
        with self.assertRaises(pm.UnsupportedActionError):
            pm.execute_intent(
                store,
                intent["intent_id"],
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=self.config(client),
            )
        # Unsupported is detected before the claim, so the intent stays usable
        # for nothing at all -- but crucially no request was made.
        self.assertEqual(client.calls, [])
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_PENDING)


# --------------------------------------------------------------------------
# Slack external routing
# --------------------------------------------------------------------------


class SlackRoutingTests(TempCaseMixin, unittest.TestCase):
    def slack_intent(self, store, action, **kwargs):
        event = pm.get_event(self.conn(), "ev-sl-1")["event"]
        return self.prepare(store, platform="slack", action=action, event=event, **kwargs)

    def render(self, store, intent):
        stored = store.load_intent(intent["intent_id"])
        payload = store.verify_executable(stored)
        return pm.render_slack_external_action(stored, payload)

    def test_send_routes_to_composio_without_executing(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="shipping now")
        result = self.render(store, intent)
        self.assertEqual(result["status"], "unexecuted_plan")
        self.assertFalse(result["executed"])
        self.assertEqual(result["route"]["tool_slug"], "SLACK_SEND_MESSAGE")
        self.assertEqual(
            result["route"]["arguments"],
            {"channel": "C0123ABCD", "markdown_text": "shipping now"},
        )
        self.assertRegex(result["plan_hash"], r"^[0-9a-f]{64}$")

    def test_plan_hash_detects_any_route_or_argument_substitution(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="approved text")
        plan = self.render(store, intent)
        changed = json.loads(json.dumps(plan["route"]))
        changed["arguments"]["markdown_text"] = "substituted text"

        self.assertEqual(
            plan["plan_hash"],
            pm.slack_plan_hash(intent["payload_hash"], intent["intent_id"], plan["route"]),
        )
        self.assertNotEqual(
            plan["plan_hash"],
            pm.slack_plan_hash(intent["payload_hash"], intent["intent_id"], changed),
        )

    def test_reply_carries_thread_ts(self):
        store = self.store()
        intent = self.slack_intent(store, "reply", message="ack")
        result = self.render(store, intent)
        self.assertEqual(
            result["route"],
            {
                "provider": "composio",
                "connection": "hermes_slack_personal",
                "tool_slug": "SLACK_SEND_MESSAGE",
                "arguments": {
                    "channel": "C0123ABCD",
                    "markdown_text": "ack",
                    "thread_ts": "1753023600.001900",
                },
            },
        )

    def test_react_unreact_edit_delete_and_mark_read_exact_schemas(self):
        """Schemas independently verified live against Composio on 2026-07-21."""
        store = self.store()
        cases = (
            (
                "react",
                {"emoji": ":tada:"},
                "SLACK_ADD_REACTION_TO_AN_ITEM",
                {"channel": "C0123ABCD", "name": "tada", "timestamp": "1753023600.001900"},
            ),
            (
                "unreact",
                {"emoji": ":tada:"},
                "SLACK_REMOVE_REACTION_FROM_ITEM",
                {"channel": "C0123ABCD", "name": "tada", "timestamp": "1753023600.001900"},
            ),
            (
                "edit",
                {"message": "corrected"},
                "SLACK_UPDATES_A_SLACK_MESSAGE",
                {"channel": "C0123ABCD", "ts": "1753023600.001900", "markdown_text": "corrected"},
            ),
            (
                "delete",
                {},
                "SLACK_DELETES_A_MESSAGE_FROM_A_CHAT",
                {"channel": "C0123ABCD", "ts": "1753023600.001900"},
            ),
            (
                "mark-read",
                {},
                "SLACK_SET_READ_CURSOR_IN_A_CONVERSATION",
                {"channel": "C0123ABCD", "ts": "1753023600.001900"},
            ),
        )
        for action, kwargs, slug, arguments in cases:
            with self.subTest(action=action):
                plan = self.render(store, self.slack_intent(store, action, **kwargs))
                self.assertEqual(plan["route"]["tool_slug"], slug)
                self.assertEqual(plan["route"]["arguments"], arguments)

    def test_unicode_slack_reaction_is_validated_and_preserved(self):
        store = self.store()
        plan = self.render(store, self.slack_intent(store, "react", emoji=":🔥:"))
        self.assertEqual(plan["route"]["arguments"]["name"], "🔥")

    def test_slack_send_file_and_typing_are_declared_unsupported(self):
        store = self.store()
        upload = self.tmp / "private.txt"
        upload.write_text("private", encoding="utf-8")
        for action, kwargs in (
            ("send-file", {"files": [str(upload)]}),
            ("typing-start", {}),
            ("typing-stop", {}),
        ):
            with self.assertRaises(pm.UnsupportedActionError, msg=action) as ctx:
                prepared = pm.build_payload(
                    platform="slack", action=action, target="C0123ABCD", **kwargs
                )
                intent = store.create_intent(prepared)
                self.render(store, intent)
            self.assertEqual(ctx.exception.code, "unsupported_on_platform")

    def test_slack_validation_rejects_bad_channel_timestamp_and_reaction(self):
        event = pm.get_event(self.conn(), "ev-sl-1")["event"]
        for bad_target in ("general", "#general", "C12", "https://slack.test/C123", "C 123"):
            with self.subTest(target=bad_target), self.assertRaises(pm.ValidationError):
                pm.build_payload(platform="slack", action="send", target=bad_target, message="hi")
        for bad_ts in ("2026-07-20T15:00:00Z", "1753023600", "1753023600.1", "x.000001"):
            with self.subTest(ts=bad_ts), self.assertRaises(pm.ValidationError):
                pm.build_payload(
                    platform="slack",
                    action="delete",
                    target="C0123ABCD",
                    target_message_id=bad_ts,
                )
        for bad_emoji in (":", "::", " : ", "bad reaction", "\n"):
            with self.subTest(emoji=bad_emoji), self.assertRaises(pm.ValidationError):
                pm.build_payload(platform="slack", action="react", event=event, emoji=bad_emoji)

    def test_slack_dry_run_does_not_claim(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_PENDING)

    def test_external_plan_output_is_redacted_unless_local_reveal_is_deliberate(self):
        store = self.store()
        secret = "sensitive shipping note"
        intent = self.slack_intent(store, "send", message=secret)
        result = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        rendered = json.dumps(result)
        self.assertEqual(result["status"], "unexecuted_plan")
        self.assertFalse(result["executed"])
        self.assertNotIn(secret, rendered)
        self.assertNotIn("C0123ABCD", rendered)
        self.assertNotIn("arguments", result["route"])

        other = self.slack_intent(store, "send", message=secret)
        revealed = pm.execute_intent(
            store,
            other["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
            reveal_local_plan=True,
        )
        self.assertEqual(revealed["route"]["arguments"]["markdown_text"], secret)

    def test_record_external_result_closes_the_trail(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        plan = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        recorded = pm.record_external_result(
            store,
            intent["intent_id"],
            result="success",
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            plan_hash=plan["plan_hash"],
            remote_ref="1753023601.000100",
        )
        self.assertEqual(recorded["intent"]["status"], pm.STATUS_SUCCEEDED)
        self.assertEqual(store.load_intent(intent["intent_id"])["status"], pm.STATUS_SUCCEEDED)

    def test_record_external_result_requires_acknowledgement(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        with self.assertRaises(pm.AcknowledgementError):
            pm.record_external_result(
                store, intent["intent_id"], result="success", acknowledgement="sure"
            )

    def test_record_external_result_is_not_replayable(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        plan = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        pm.record_external_result(
            store,
            intent["intent_id"],
            result="uncertain",
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            plan_hash=plan["plan_hash"],
        )
        with self.assertRaises(pm.IntentStateError) as ctx:
            pm.record_external_result(
                store,
                intent["intent_id"],
                result="success",
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                plan_hash=plan["plan_hash"],
            )
        self.assertEqual(ctx.exception.code, "intent_not_external_pending")

    def test_record_external_result_cannot_forge_a_payload(self):
        """A result can reference the bound plan hash but cannot replace its payload."""
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        before = store.load_intent(intent["intent_id"])["payload_json"]
        plan = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        pm.record_external_result(
            store,
            intent["intent_id"],
            result="success",
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            plan_hash=plan["plan_hash"],
        )
        after = store.load_intent(intent["intent_id"])
        self.assertEqual(after["payload_json"], before)
        self.assertEqual(after["payload_hash"], intent["payload_hash"])

    def test_record_external_result_rejects_non_slack_intents(self):
        store = self.store()
        intent = self.prepare(
            store, platform="whatsapp", action="send", target="+4915112345678", message="hi"
        )
        with self.assertRaises(pm.IntentStateError) as ctx:
            pm.record_external_result(
                store, intent["intent_id"], result="success", acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN
            )
        self.assertEqual(ctx.exception.code, "intent_not_external")

    def test_record_external_result_validates_result_value(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        with self.assertRaises(pm.ValidationError):
            pm.record_external_result(
                store, intent["intent_id"], result="maybe", acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN
            )

    def test_record_external_result_rejects_wrong_plan_hash_without_transition(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        plan = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        with self.assertRaises(pm.IntentStateError) as ctx:
            pm.record_external_result(
                store,
                intent["intent_id"],
                result="success",
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                plan_hash="0" * 64,
            )
        self.assertEqual(ctx.exception.code, "external_plan_mismatch")
        self.assertEqual(
            store.load_intent(intent["intent_id"])["status"], pm.STATUS_EXTERNAL_PENDING
        )
        self.assertRegex(plan["plan_hash"], r"^[0-9a-f]{64}$")

    def test_concurrent_external_results_create_one_terminal_result(self):
        store = self.store()
        intent = self.slack_intent(store, "send", message="hi")
        plan = pm.execute_intent(
            store,
            intent["intent_id"],
            acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
            config=self.config(FakeClient()),
        )
        barrier = threading.Barrier(2)
        outcomes = []

        def finish(result):
            barrier.wait()
            try:
                outcomes.append(
                    pm.record_external_result(
                        store,
                        intent["intent_id"],
                        result=result,
                        acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                        plan_hash=plan["plan_hash"],
                    )["intent"]["status"]
                )
            except pm.IntentStateError:
                outcomes.append("rejected")

        threads = [
            threading.Thread(target=finish, args=("success",)),
            threading.Thread(target=finish, args=("uncertain",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(outcomes.count("rejected"), 1)
        result_rows = [row for row in store.audit_trail(intent["intent_id"]) if row["event"] == "result"]
        self.assertEqual(len(result_rows), 1)


# --------------------------------------------------------------------------
# HTTP transport with deterministic mocked responses (no socket permissions)
# --------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, body=b'{"success":true,"messageId":"OK-1"}', error=None):
        self.body = body
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        if self.error:
            raise self.error
        return self.body


class HttpClientTests(unittest.TestCase):
    BASE_URL = "http://127.0.0.1:34567"

    def client(self, response=None, error=None, **kwargs):
        def opener(_request, _timeout):
            if error:
                raise error
            return response or _FakeHttpResponse()

        return pm.LoopbackHttpClient(self.BASE_URL, opener=opener, **kwargs)

    def test_post_json_round_trip(self):
        self.assertEqual(
            self.client().post_json("/send", {"chatId": "x"}),
            {"success": True, "messageId": "OK-1"},
        )

    def test_client_rejects_non_loopback_base_url(self):
        with self.assertRaises(pm.ValidationError):
            pm.LoopbackHttpClient("http://192.168.1.10:3000", allow_non_loopback=False)

    def test_any_http_error_after_post_is_ambiguous(self):
        for code in (302, 400, 503):
            error = urllib.error.HTTPError(self.BASE_URL, code, "remote text", {}, None)
            with self.subTest(code=code), self.assertRaises(pm.TransportAmbiguousError):
                self.client(error=error).post_json("/send", {})

    def test_timeout_is_ambiguous(self):
        with self.assertRaises(pm.TransportAmbiguousError):
            self.client(error=TimeoutError("late")).post_json("/send", {})

    def test_connection_refused_is_preflight_failure(self):
        with self.assertRaises(pm.TransportPreflightError):
            self.client(error=urllib.error.URLError(ConnectionRefusedError())).post_json("/send", {})

    def test_generic_urlerror_and_oserror_are_ambiguous(self):
        for error in (urllib.error.URLError("generic"), BrokenPipeError(), ConnectionResetError()):
            with self.subTest(error=type(error).__name__), self.assertRaises(pm.TransportAmbiguousError):
                self.client(error=error).post_json("/send", {})

    def test_oversized_response_is_ambiguous(self):
        client = self.client(response=_FakeHttpResponse(b"12345"), max_response_bytes=4)
        with self.assertRaises(pm.TransportAmbiguousError):
            client.post_json("/send", {})

    def test_empty_malformed_or_non_object_response_is_ambiguous(self):
        for body in (b"", b"not json", b'"not-an-object"', b"[]"):
            with self.subTest(body=body), self.assertRaises(pm.TransportAmbiguousError):
                self.client(response=_FakeHttpResponse(body)).post_json("/send", {})

    def test_environment_proxies_are_disabled_and_redirects_are_rejected(self):
        with mock.patch.object(pm.urllib.request, "build_opener", wraps=pm.urllib.request.build_opener) as build:
            pm.LoopbackHttpClient(self.BASE_URL)
        handlers = build.call_args.args
        proxy = next(handler for handler in handlers if isinstance(handler, pm.urllib.request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(any(isinstance(handler, pm.NoRedirectHandler) for handler in handlers))

    def test_end_to_end_whatsapp_send_through_transport(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = pm.ActionStore(Path(tmp.name) / "actions.sqlite3")
        prepared = pm.build_payload(
            platform="whatsapp", action="send", target="+4915112345678", message="hello"
        )
        intent = store.create_intent(prepared)
        client = self.client()
        config = pm.ExecutionConfig(client_factory=lambda _url, _timeout, _allow: client)
        result = pm.execute_intent(
            store, intent["intent_id"], acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN, config=config
        )
        self.assertEqual(result["status"], pm.STATUS_SUCCEEDED)
        self.assertEqual(result["remote_ref"], "OK-1")


# --------------------------------------------------------------------------
# status and redaction
# --------------------------------------------------------------------------


class StatusTests(TempCaseMixin, unittest.TestCase):
    def offline_prober(self, url, timeout):
        return {"reachable": False, "detail": "ConnectionRefusedError"}

    def test_status_reports_offline_bridges(self):
        status = pm.build_status(
            context_db=self.context_db,
            actions_db=self.actions_db,
            signal_account="+4915000000000",
            probe=True,
            prober=self.offline_prober,
        )
        self.assertFalse(status["whatsapp"]["reachable"])
        self.assertFalse(status["signal"]["reachable"])
        self.assertTrue(status["context_inbox"]["available"])
        self.assertEqual(status["context_inbox"]["events"], len(EVENTS))
        self.assertEqual(status["context_inbox"]["platforms"]["signal"]["events"], 2)

    def test_passive_status_and_listing_do_not_create_or_chmod_actions_db(self):
        missing = self.tmp / "does-not-exist" / "actions.sqlite3"
        status = pm.build_status(
            context_db=self.context_db,
            actions_db=missing,
            signal_account="",
            probe=False,
        )
        listed = pm.list_intents_readonly(missing)

        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        self.assertFalse(status["actions_db"]["available"])
        self.assertEqual(listed, [])

    def test_status_summary_omits_local_database_paths(self):
        status = pm.build_status(
            context_db=self.context_db,
            actions_db=self.actions_db,
            signal_account="",
        )
        rendered = json.dumps(status)
        self.assertNotIn(str(self.context_db), rendered)
        self.assertNotIn(str(self.actions_db), rendered)

    def test_status_is_passive_and_connectivity_probe_is_separate(self):
        def forbidden(*_args):
            raise AssertionError("passive status probed a socket")

        status = pm.build_status(
            context_db=self.context_db,
            actions_db=self.actions_db,
            signal_account="",
            prober=forbidden,
        )
        self.assertIsNone(status["whatsapp"]["reachable"])
        probe = pm.build_connectivity_probe(
            whatsapp_url=pm.DEFAULT_WHATSAPP_URL,
            signal_url=pm.DEFAULT_SIGNAL_URL,
            prober=self.offline_prober,
        )
        self.assertFalse(probe["whatsapp"]["reachable"])

    def test_status_never_prints_the_full_signal_account(self):
        account = "+4915000000000"
        status = pm.build_status(
            context_db=self.context_db,
            actions_db=self.actions_db,
            signal_account=account,
            probe=False,
        )
        rendered = json.dumps(status)
        self.assertNotIn(account, rendered)
        self.assertNotIn("4915000000000", rendered)
        self.assertTrue(status["signal"]["account_configured"])
        self.assertEqual(status["signal"]["account_hint"], "…00")

    def test_status_flags_unconfigured_signal_account(self):
        status = pm.build_status(
            context_db=self.context_db, actions_db=self.actions_db, signal_account="", probe=False
        )
        self.assertFalse(status["signal"]["account_configured"])
        self.assertEqual(status["signal"]["account_hint"], "")

    def test_status_when_context_db_is_missing(self):
        status = pm.build_status(
            context_db=self.tmp / "nope.sqlite3", actions_db=self.actions_db, probe=False
        )
        self.assertFalse(status["context_inbox"]["available"])
        self.assertEqual(status["status"], "ok")

    def test_status_declares_slack_as_external(self):
        status = pm.build_status(
            context_db=self.context_db, actions_db=self.actions_db, probe=False
        )
        self.assertFalse(status["slack"]["local_writes"])
        self.assertEqual(status["slack"]["route"], "composio")
        self.assertIn("delete", status["slack"]["supported_external_actions"])
        self.assertIn("send-file", status["slack"]["unsupported_external_actions"])
        self.assertIn("send", status["slack"]["supported_external_actions"])

    def test_status_flags_non_loopback_bridge_url(self):
        status = pm.build_status(
            context_db=self.context_db,
            actions_db=self.actions_db,
            whatsapp_url="http://192.168.0.9:3000",
            probe=False,
        )
        self.assertFalse(status["whatsapp"]["loopback"])
        self.assertIsNone(status["whatsapp"]["reachable"])

    def test_status_states_native_approval_and_same_uid_limit(self):
        status = pm.build_status(
            context_db=self.context_db, actions_db=self.actions_db, signal_account=""
        )
        boundary = status["approval_boundary"]
        self.assertEqual(boundary["enforced_by"], "Hermes native tool approval layer")
        self.assertIn("allow-once", boundary["required"])
        self.assertFalse(boundary["execute_action_registered_as_model_tool"])
        self.assertFalse(boundary["acknowledgement_is_authorization"])
        self.assertFalse(boundary["same_uid_adversary_protection"])


class RedactionHelperTests(unittest.TestCase):
    def test_mask_account(self):
        self.assertEqual(pm._mask_account("+4915112345678"), "…78")
        self.assertEqual(pm._mask_account(""), "")

    def test_sanitize_detail_strips_control_chars_and_bounds(self):
        detail = pm._sanitize_detail("line1\nline2\x00\x07" + "x" * 500)
        self.assertNotIn("\n", detail)
        self.assertNotIn("\x00", detail)
        self.assertEqual(len(detail), 200)

    def test_snippet_bounds(self):
        self.assertEqual(len(pm._snippet("a" * 1000)), pm.SNIPPET_CHARS)

    def test_epoch_millis_variants(self):
        self.assertEqual(pm._epoch_millis("1753031400000"), 1753031400000)
        self.assertEqual(pm._epoch_millis("1753031400"), 1753031400000)
        self.assertIsNone(pm._epoch_millis("not a time"))
        self.assertIsNone(pm._epoch_millis(""))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


class CliTests(TempCaseMixin, unittest.TestCase):
    def run_cli(self, argv) -> tuple[int, dict]:
        buffer = io.StringIO()
        stdout = __import__("sys").stdout
        __import__("sys").stdout = buffer
        try:
            code = pm.main(argv)
        finally:
            __import__("sys").stdout = stdout
        text = buffer.getvalue().strip()
        try:
            return code, json.loads(text)
        except json.JSONDecodeError:
            return code, {"_text": text}

    def base(self, *extra):
        return ["--db", str(self.context_db), *extra]

    def test_search_cli_json(self):
        code, data = self.run_cli(["search", *self.base(), "--query", "voucher", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(data["results"][0]["event_id"], "ev-wa-1")

    def test_search_cli_human(self):
        code, data = self.run_cli(["search", *self.base(), "--query", "voucher"])
        self.assertEqual(code, 0)
        self.assertIn("ev-wa-1", data["_text"])

    def test_conversations_cli(self):
        code, data = self.run_cli(["conversations", *self.base(), "--platform", "signal", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(data["count"], 2)

    def test_show_cli_omits_raw_json(self):
        code, data = self.run_cli(["show", *self.base(), "--event-id", "ev-wa-1", "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("raw_json", data["event"])
        self.assertNotIn("should-not-leak", json.dumps(data))

    def test_missing_context_db_is_a_clean_error(self):
        code, data = self.run_cli(
            ["search", "--db", str(self.tmp / "gone.sqlite3"), "--query", "x", "--json"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(data["code"], "context_db_missing")

    def test_prepare_then_execute_refuses_without_acknowledgement(self):
        code, prepared = self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "reply",
                "--event-id",
                "ev-wa-1",
                "--message",
                "on my way",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(prepared["status"], "prepared")
        intent_id = prepared["intent"]["intent_id"]

        code, refused = self.run_cli(
            [
                "execute-action",
                "--actions-db",
                str(self.actions_db),
                "--intent-id",
                intent_id,
                "--acknowledgement",
                "assistant-initiative",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(refused["code"], "acknowledgement_required")

    def test_prepare_ambiguous_conversation_via_cli(self):
        code, data = self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "send",
                "--conversation",
                "Family",
                "--message",
                "hi",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(data["code"], "ambiguous_conversation")
        self.assertEqual(len(data["candidates"]), 2)

    def test_event_id_rejects_conflicting_conversation_account_and_workspace(self):
        cases = (
            ("--conversation", "Family"),
            ("--target", "+4915199999999"),
            ("--account", "wa-other"),
            ("--workspace", "other-workspace"),
        )
        for flag, value in cases:
            with self.subTest(flag=flag):
                code, data = self.run_cli(
                    [
                        "prepare-action",
                        *self.base(),
                        "--actions-db",
                        str(self.actions_db),
                        "--platform",
                        "whatsapp",
                        "--action",
                        "reply",
                        "--event-id",
                        "ev-wa-1",
                        flag,
                        value,
                        "--message",
                        "reply",
                    ]
                )
                self.assertEqual(code, 2)
                self.assertIn(data["code"], {"event_target_conflict", "event_identity_conflict"})

    def test_prepare_resolves_unique_conversation_name(self):
        code, data = self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "send",
                "--conversation",
                "Team Standup",
                "--message",
                "hi",
            ]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("target", data["intent"])
        self.assertIn("target_handle", data["intent"])

    def test_message_stdin_and_safe_message_file_avoid_command_line_body(self):
        with mock.patch("sys.stdin", io.StringIO("from stdin\n")):
            code, stdin_result = self.run_cli(
                [
                    "prepare-action",
                    *self.base(),
                    "--actions-db",
                    str(self.actions_db),
                    "--platform",
                    "whatsapp",
                    "--action",
                    "send",
                    "--target",
                    "+4915112345678",
                    "--message-stdin",
                ]
            )
        message_file = self.tmp / "message.txt"
        message_file.write_text("from file", encoding="utf-8")
        code_file, file_result = self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "send",
                "--target",
                "+4915112345678",
                "--message-file",
                str(message_file),
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(code_file, 0)
        stored = pm.ActionStore(self.actions_db)
        bodies = {
            json.loads(stored.load_intent(result["intent"]["intent_id"])["payload_json"])["message"]
            for result in (stdin_result, file_result)
        }
        self.assertEqual(bodies, {"from stdin\n", "from file"})

    def test_message_file_symlink_is_refused(self):
        target = self.tmp / "body.txt"
        link = self.tmp / "body-link.txt"
        target.write_text("secret", encoding="utf-8")
        link.symlink_to(target)
        code, result = self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "send",
                "--target",
                "+4915112345678",
                "--message-file",
                str(link),
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(result["code"], "unsafe_message_file")

    def test_prepare_output_does_not_echo_quote_body_or_raw_target(self):
        secret_quote = EVENTS[0]["body"]
        with mock.patch("sys.stdin", io.StringIO("reply")):
            code, result = self.run_cli(
                [
                    "prepare-action",
                    *self.base(),
                    "--actions-db",
                    str(self.actions_db),
                    "--platform",
                    "whatsapp",
                    "--action",
                    "reply",
                    "--event-id",
                    "ev-wa-1",
                    "--message-stdin",
                ]
            )
        rendered = json.dumps(result)
        self.assertEqual(code, 0)
        self.assertNotIn(secret_quote, rendered)
        self.assertNotIn("4915112345678", rendered)

    def test_dry_run_execute_via_cli_makes_no_call(self):
        _, prepared = self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "send",
                "--target",
                "+4915112345678",
                "--message",
                "hi",
            ]
        )
        code, data = self.run_cli(
            [
                "execute-action",
                "--actions-db",
                str(self.actions_db),
                "--intent-id",
                prepared["intent"]["intent_id"],
                "--acknowledgement",
                pm.ACKNOWLEDGEMENT_TOKEN,
                "--dry-run",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "dry_run")
        self.assertFalse(data["claimed"])

    def test_status_cli_without_probe(self):
        code, data = self.run_cli(
            [
                "status",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(data["slack"]["route"], "composio")

    def test_list_intents_shows_metadata_only(self):
        self.run_cli(
            [
                "prepare-action",
                *self.base(),
                "--actions-db",
                str(self.actions_db),
                "--platform",
                "whatsapp",
                "--action",
                "send",
                "--target",
                "+4915112345678",
                "--message",
                "top secret",
            ]
        )
        code, data = self.run_cli(
            ["list-intents", "--actions-db", str(self.actions_db), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(data["intents"]), 1)
        self.assertNotIn("top secret", json.dumps(data))

    def test_unknown_event_id_is_a_clean_error(self):
        code, data = self.run_cli(["show", *self.base(), "--event-id", "missing", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(data["code"], "event_not_found")


# --------------------------------------------------------------------------
# Signal Desktop history is search-only and must never become an action target
# --------------------------------------------------------------------------

# Signal Desktop history events carry no action binding (empty action_target_id
# / action_account_id). Their conversation_id is a raw Signal Desktop identifier
# that can *resemble* an E.164 number or a UUID, so the generic
# ``action_target_id or conversation_id`` fallback would otherwise let a passive
# history row masquerade as a live send target. These rows are tagged with
# account="signal-desktop" and source_path="signal-desktop".
SIGNAL_DESKTOP_EVENTS = [
    {
        "event_id": "ev-sd-e164",
        "platform": "signal",
        "account": "signal-desktop",
        "action_target_id": "",
        "action_account_id": "",
        "conversation_id": "+15551234567",
        "conversation_name": "Desktop Ines",
        "conversation_type": "dm",
        "sender_id": "+15551234567",
        "sender_display_name": "Desktop Ines",
        "direction": "inbound",
        "body": "history-only mirror: dinner at eight?",
        "message_ts": "2026-07-20T17:30:00Z",
        "source_message_id": "signal-desktop:desk-e164",
        "thread_id": "",
        "source_path": "signal-desktop",
    },
    {
        "event_id": "ev-sd-uuid",
        "platform": "signal",
        "account": "signal-desktop",
        "action_target_id": "",
        "action_account_id": "",
        "conversation_id": "11111111-1111-4111-8111-111111111111",
        "conversation_name": "Desktop Climbing",
        "conversation_type": "dm",
        "sender_id": "22222222-2222-4222-8222-222222222222",
        "sender_display_name": "Desktop Ana",
        "direction": "inbound",
        "body": "history-only mirror: session tomorrow",
        "message_ts": "2026-07-20T12:00:00Z",
        "source_message_id": "signal-desktop:desk-uuid",
        "thread_id": "",
        "source_path": "signal-desktop",
    },
]

# A live signal-cli event with the SAME empty-binding shape but *not* sourced
# from the desktop mirror. Its conversation_id fallback must keep working.
LIVE_SIGNAL_EVENT_NO_BINDING = {
    "event_id": "ev-sig-live-nobind",
    "platform": "signal",
    "account": "signal-account:live-primary",
    "action_target_id": "",
    "action_account_id": "",
    "conversation_id": "+4915109999999",
    "conversation_name": "Live Ines",
    "conversation_type": "dm",
    "sender_id": "+4915109999999",
    "sender_display_name": "Live Ines",
    "direction": "inbound",
    "body": "live signal-cli: are we still on?",
    "message_ts": "2026-07-20T19:00:00Z",
    "source_message_id": "signal-1753038000000",
    "thread_id": "",
    "source_path": "signal-cli",
}


class SignalDesktopSearchOnlyTests(TempCaseMixin, unittest.TestCase):
    """Signal Desktop history is passive-only: it can be read but never mutated."""

    def insert_events(self, events) -> None:
        conn = sqlite3.connect(self.context_db)
        try:
            for index, event in enumerate(events):
                row = {
                    "identity_key": f"ik-extra-{index}-{event['event_id']}",
                    "account": "",
                    "workspace": "",
                    "action_target_id": "",
                    "action_account_id": "",
                    "received_ts": event.get("message_ts", ""),
                    "ingested_ts": event.get("message_ts", ""),
                    "permalink": "",
                    "attachment_metadata_json": "[]",
                    "raw_json": json.dumps({"id": event["event_id"]}),
                    "source_path": "",
                    "flags": "[]",
                    "updated_at": 0.0,
                    **event,
                }
                columns = ", ".join(row)
                placeholders = ", ".join(f":{name}" for name in row)
                conn.execute(
                    f"INSERT INTO context_events({columns}) VALUES({placeholders})", row
                )
            conn.commit()
        finally:
            conn.close()

    # -- passive retrieval still works --------------------------------------

    def test_signal_desktop_history_is_searchable_and_showable(self):
        self.insert_events(SIGNAL_DESKTOP_EVENTS)
        conn = self.conn()
        result = pm.search_events(conn, query="history-only mirror")
        ids = {hit["event_id"] for hit in result["results"]}
        self.assertEqual(ids, {"ev-sd-e164", "ev-sd-uuid"})
        event = pm.get_event(conn, "ev-sd-uuid")["event"]
        self.assertTrue(event["body"].startswith("history-only mirror"))
        conversations = pm.list_conversations(conn, platform="signal")["conversations"]
        desktop = {
            item["conversation_id"]
            for item in conversations
            if item["account"] == "signal-desktop"
        }
        self.assertEqual(
            desktop, {"+15551234567", "11111111-1111-4111-8111-111111111111"}
        )

    # -- action preparation fails fail-closed -------------------------------

    def test_build_payload_refuses_every_action_from_a_desktop_event(self):
        self.insert_events(SIGNAL_DESKTOP_EVENTS)
        conn = self.conn()
        mutating_actions = (
            "send",
            "send-file",
            "reply",
            "react",
            "unreact",
            "edit",
            "delete",
            "mark-read",
            "typing-start",
            "typing-stop",
        )
        for event_id in ("ev-sd-e164", "ev-sd-uuid"):
            event = pm.get_event(conn, event_id, for_action=True)["event"]
            for action in mutating_actions:
                with self.assertRaises(pm.ValidationError) as ctx:
                    pm.build_payload(
                        platform="signal",
                        action=action,
                        event=event,
                        message="hi",
                        emoji="👍",
                        files=["/dev/null"],
                    )
                self.assertEqual(
                    ctx.exception.code,
                    "search_only_source",
                    msg=f"{action} from {event_id} was not refused fail-closed",
                )

    def test_build_payload_refuses_desktop_event_without_for_action_flag(self):
        # Even a lightly-populated event dict (no _action_target_id) must be
        # refused: the account/source_path provenance is enough.
        self.insert_events(SIGNAL_DESKTOP_EVENTS)
        conn = self.conn()
        event = pm.get_event(conn, "ev-sd-uuid")["event"]
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.build_payload(platform="signal", action="send", event=event, message="hi")
        self.assertEqual(ctx.exception.code, "search_only_source")

    def test_resolve_conversation_refuses_desktop_by_id_and_by_name(self):
        self.insert_events(SIGNAL_DESKTOP_EVENTS)
        conn = self.conn()
        for conversation in (
            "+15551234567",
            "11111111-1111-4111-8111-111111111111",
            "Desktop Ines",
            "Desktop Climbing",
        ):
            with self.assertRaises(pm.ValidationError) as ctx:
                pm.resolve_conversation(conn, "signal", conversation)
            self.assertEqual(
                ctx.exception.code,
                "search_only_source",
                msg=f"resolving {conversation!r} was not refused fail-closed",
            )

    def test_prepare_action_cli_refuses_and_persists_no_intent(self):
        self.insert_events(SIGNAL_DESKTOP_EVENTS)
        buffer = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buffer
        try:
            code = pm.main(
                [
                    "prepare-action",
                    "--db",
                    str(self.context_db),
                    "--actions-db",
                    str(self.actions_db),
                    "--platform",
                    "signal",
                    "--action",
                    "send",
                    "--event-id",
                    "ev-sd-uuid",
                    "--message",
                    "hi",
                ]
            )
        finally:
            sys.stdout = real_stdout
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "search_only_source")
        # Fail-closed before any connector I/O: no mutation intent was stored.
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_prepare_action_cli_refuses_desktop_conversation_resolution(self):
        self.insert_events(SIGNAL_DESKTOP_EVENTS)
        buffer = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buffer
        try:
            code = pm.main(
                [
                    "prepare-action",
                    "--db",
                    str(self.context_db),
                    "--actions-db",
                    str(self.actions_db),
                    "--platform",
                    "signal",
                    "--action",
                    "send",
                    "--conversation",
                    "Desktop Ines",
                    "--message",
                    "hi",
                ]
            )
        finally:
            sys.stdout = real_stdout
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "search_only_source")
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    # -- live / manual Signal behaviour is retained -------------------------

    def test_manual_signal_target_still_prepares(self):
        prepared = pm.build_payload(
            platform="signal", action="send", target="+15551234567", message="hi"
        )
        self.assertEqual(prepared.target, "+15551234567")

    def test_live_signal_event_without_binding_still_falls_back_to_conversation(self):
        self.insert_events([LIVE_SIGNAL_EVENT_NO_BINDING])
        conn = self.conn()
        event = pm.get_event(conn, "ev-sig-live-nobind", for_action=True)["event"]
        prepared = pm.build_payload(
            platform="signal", action="send", event=event, message="hi"
        )
        self.assertEqual(prepared.target, "+4915109999999")
        # The same live conversation still resolves for actions.
        resolved = pm.resolve_conversation(conn, "signal", "+4915109999999")
        self.assertEqual(resolved["conversation_id"], "+4915109999999")


class WhatsAppConvenienceHelperTests(TempCaseMixin, unittest.TestCase):
    """prepare_whatsapp / send_whatsapp: the one-call ergonomic wrapper."""

    def test_send_whatsapp_resolves_group_and_calls_bridge_once(self):
        client = FakeClient({"success": True, "id": "3EB0GROUP"})
        result = pm.send_whatsapp(
            text="standup moved",
            group="Team Standup",
            context_db=self.context_db,
            actions_db=self.actions_db,
            config=self.config(client),
        )
        self.assertEqual(result["status"], pm.STATUS_SUCCEEDED)
        self.assertEqual(result["remote_ref"], "3EB0GROUP")
        self.assertEqual(len(client.calls), 1)
        path, body = client.calls[0]
        self.assertEqual(path, "/send")
        self.assertEqual(body, {"chatId": ("111111111111111111" + "@g.us"), "message": "standup moved"})

    def test_send_whatsapp_group_selector_refuses_a_dm(self):
        client = FakeClient()
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.send_whatsapp(
                text="hi",
                group="Mara",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "not_a_group")
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_ambiguous_group_name_refuses_before_network(self):
        client = FakeClient()
        with self.assertRaises(pm.AmbiguousTargetError):
            pm.send_whatsapp(
                text="hi",
                group="Family",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=self.config(client),
            )
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_explicit_target_never_opens_context_inbox(self):
        client = FakeClient({"success": True, "id": "3EB0TARGET"})
        missing_context_db = self.tmp / "does-not-exist.sqlite3"
        result = pm.send_whatsapp(
            text="hi",
            target="+4915112345678",
            context_db=missing_context_db,
            actions_db=self.actions_db,
            config=self.config(client),
        )
        self.assertEqual(result["status"], pm.STATUS_SUCCEEDED)
        path, body = client.calls[0]
        self.assertEqual(body["chatId"], ("4915112345678" + "@s.whatsapp.net"))

    def test_prepare_whatsapp_rejects_zero_selectors(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.prepare_whatsapp(text="hi", context_db=self.context_db, actions_db=self.actions_db)
        self.assertEqual(ctx.exception.code, "destination_selector_invalid")
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_prepare_whatsapp_rejects_multiple_selectors(self):
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.prepare_whatsapp(
                text="hi",
                group="Team Standup",
                target="+4915112345678",
                context_db=self.context_db,
                actions_db=self.actions_db,
            )
        self.assertEqual(ctx.exception.code, "destination_selector_invalid")
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_rejects_blank_group(self):
        client = FakeClient()
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.send_whatsapp(
                text="hi",
                group="",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "destination_selector_invalid")
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_rejects_whitespace_group(self):
        client = FakeClient()
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.send_whatsapp(
                text="hi",
                group="   ",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "destination_selector_invalid")
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_rejects_blank_conversation(self):
        client = FakeClient()
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.send_whatsapp(
                text="hi",
                conversation="",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "destination_selector_invalid")
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_rejects_whitespace_conversation(self):
        client = FakeClient()
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.send_whatsapp(
                text="hi",
                conversation="   ",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=self.config(client),
            )
        self.assertEqual(ctx.exception.code, "destination_selector_invalid")
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_conflicting_urls_refuse_before_intent_creation(self):
        client = FakeClient()
        config = pm.ExecutionConfig(
            whatsapp_url="http://127.0.0.1:3000",
            client_factory=lambda url, timeout, allow: client,
        )
        with self.assertRaises(pm.ValidationError) as ctx:
            pm.send_whatsapp(
                text="hi",
                target="+4915112345678",
                whatsapp_url="http://127.0.0.1:4000",
                context_db=self.context_db,
                actions_db=self.actions_db,
                config=config,
            )
        self.assertEqual(ctx.exception.code, "execution_binding_mismatch")
        self.assertEqual(client.calls, [])
        self.assertEqual(pm.ActionStore(self.actions_db).list_intents(), [])

    def test_send_whatsapp_identical_normalized_urls_still_work(self):
        client = FakeClient({"success": True, "id": "3EB0SAME"})
        config = pm.ExecutionConfig(
            whatsapp_url="http://127.0.0.1:3000",
            client_factory=lambda url, timeout, allow: client,
        )
        result = pm.send_whatsapp(
            text="hi",
            target="+4915112345678",
            whatsapp_url="http://localhost:3000",
            context_db=self.context_db,
            actions_db=self.actions_db,
            config=config,
        )
        self.assertEqual(result["status"], pm.STATUS_SUCCEEDED)
        self.assertEqual(len(client.calls), 1)

    def test_prepare_whatsapp_is_offline_and_leaves_a_pending_intent(self):
        def _raise_if_invoked(url, timeout, allow):
            raise AssertionError("prepare_whatsapp must not build a transport client")

        result = pm.prepare_whatsapp(
            text="hi",
            group="Team Standup",
            context_db=self.context_db,
            actions_db=self.actions_db,
            config=pm.ExecutionConfig(client_factory=_raise_if_invoked),
        )
        self.assertEqual(result["status"], "prepared")
        intent_id = result["intent"]["intent_id"]
        store = pm.ActionStore(self.actions_db)
        self.assertEqual(store.load_intent(intent_id)["status"], pm.STATUS_PENDING)

    def test_prepare_and_send_never_echo_message_or_raw_target(self):
        prepared = pm.prepare_whatsapp(
            text="super secret plan",
            group="Team Standup",
            context_db=self.context_db,
            actions_db=self.actions_db,
        )
        dumped_prepare = json.dumps(prepared)
        self.assertNotIn("super secret plan", dumped_prepare)
        self.assertNotIn(("111111111111111111" + "@g.us"), dumped_prepare)

        client = FakeClient({"success": True, "id": "3EB0SECRET"})
        result = pm.send_whatsapp(
            text="another secret",
            group="Team Standup",
            context_db=self.context_db,
            actions_db=self.actions_db,
            config=self.config(client),
        )
        dumped_send = json.dumps(result)
        self.assertNotIn("another secret", dumped_send)
        self.assertNotIn(("111111111111111111" + "@g.us"), dumped_send)

    def test_send_whatsapp_transport_ambiguous_is_inherited_without_retry(self):
        client = FakeClient(error=pm.TransportAmbiguousError("bridge request timed out"))
        result = pm.send_whatsapp(
            text="hi",
            target="+4915112345678",
            context_db=self.context_db,
            actions_db=self.actions_db,
            config=self.config(client),
        )
        self.assertEqual(result["status"], pm.STATUS_UNCERTAIN)
        self.assertEqual(len(client.calls), 1)
        intent_id = result["intent"]["intent_id"]
        store = pm.ActionStore(self.actions_db)
        with self.assertRaises(pm.IntentStateError):
            pm.execute_intent(
                store,
                intent_id,
                acknowledgement=pm.ACKNOWLEDGEMENT_TOKEN,
                config=self.config(client),
            )
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
