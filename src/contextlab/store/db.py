"""SQLite event store + in-process pub/sub.

Writes happen off the proxy hot path (see proxy/app.py). Bodies are stored
zlib-compressed since tool-heavy requests routinely reach hundreds of KB.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import zlib
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT,
    context_key TEXT,
    agent_id TEXT,
    parent_agent_id TEXT,
    model TEXT,
    status INTEGER,
    streaming INTEGER,
    stop_reason TEXT,
    -- latency (seconds)
    t_upstream_sent REAL,
    t_first_byte REAL,
    t_done REAL,
    -- usage (from the API response; ground truth, not estimates)
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    -- computed analysis (metrics/analyze.py), JSON
    metrics TEXT,
    request_body BLOB,
    response_body BLOB
);
CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id, id);
CREATE INDEX IF NOT EXISTS idx_requests_context ON requests(context_key, id);
"""


class EventStore:
    """Thread-safe store; sqlite work runs on a dedicated writer thread."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._local = threading.local()
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def insert_request(self, record: dict[str, Any]) -> int:
        row = dict(record)
        for key in ("request_body", "response_body"):
            if row.get(key) is not None:
                row[key] = zlib.compress(row[key])
        if isinstance(row.get("metrics"), dict):
            row["metrics"] = json.dumps(row["metrics"])
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        conn = self._conn()
        with conn:
            cur = conn.execute(
                f"INSERT INTO requests ({cols}) VALUES ({marks})", list(row.values())
            )
        return cur.lastrowid

    def previous_request_body(self, context_key: str | None) -> bytes | None:
        """Most recent request body in the same conversation stream (session +
        system-prompt hash), for turn-delta analysis."""
        if not context_key:
            return None
        conn = self._conn()
        row = conn.execute(
            "SELECT request_body FROM requests WHERE context_key = ?"
            " ORDER BY id DESC LIMIT 1",
            (context_key,),
        ).fetchone()
        return zlib.decompress(row[0]) if row and row[0] else None

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, ts, session_id, agent_id, model, status, streaming,"
            " stop_reason, t_upstream_sent, t_first_byte, t_done, input_tokens,"
            " output_tokens, cache_read_tokens, cache_creation_tokens, metrics"
            " FROM requests ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("metrics"):
                d["metrics"] = json.loads(d["metrics"])
            out.append(d)
        return out

    # -- pub/sub for the live dashboard -------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a slow dashboard client must never block ingestion
