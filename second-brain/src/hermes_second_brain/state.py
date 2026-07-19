"""Small local SQLite state store."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import time

from .ids import sha256_text
from .scanner import Resource

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
  resource_id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  root_name TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  summary_hash TEXT NOT NULL,
  last_seen INTEGER NOT NULL,
  exported_at INTEGER
);
"""


class State:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def changed(self, resources: list[Resource]) -> list[Resource]:
        changed: list[Resource] = []
        for resource in resources:
            row = self.conn.execute(
                "SELECT content_hash, summary_hash, exported_at FROM resources WHERE resource_id = ?",
                (resource.resource_id,),
            ).fetchone()
            summary_hash = sha256_text(resource.summary)
            if row is None or row[:2] != (resource.content_hash, summary_hash) or row[2] is None:
                changed.append(resource)
        return changed

    def mark_seen(self, resources: list[Resource], exported: bool) -> None:
        now = int(time.time())
        rows = [
            (
                resource.resource_id,
                resource.namespace,
                resource.root_name,
                resource.relative_path,
                resource.content_hash,
                sha256_text(resource.summary),
                now,
                now if exported else None,
            )
            for resource in resources
        ]
        self.conn.executemany(
            """
            INSERT INTO resources (
              resource_id, namespace, root_name, relative_path, content_hash,
              summary_hash, last_seen, exported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
              namespace = excluded.namespace,
              root_name = excluded.root_name,
              relative_path = excluded.relative_path,
              content_hash = excluded.content_hash,
              summary_hash = excluded.summary_hash,
              last_seen = excluded.last_seen,
              exported_at = COALESCE(excluded.exported_at, resources.exported_at)
            """,
            rows,
        )
        self.conn.commit()

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0])

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
