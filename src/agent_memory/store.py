"""
store.py — the durable SQLite backing store for the agent's cross-call memory.

This is the "store" stage of the claude-mem-style pipeline (capture → compress → store →
retrieve). It owns the database and nothing else: no LLM, no scrubbing, no formatting —
just schema + small CRUD helpers. Everything is plain stdlib `sqlite3`, no extra deps.

Design notes
------------
* ONE file at <project>/data/agent_memory.db (the dir is created on first use). It's local
  + private — `.gitignore` excludes `data/` and `*.db` so it never lands in the repo.
* Thread-safety: a Pipecat/Twilio call lives on an asyncio loop but our writes happen from
  `asyncio.to_thread(...)` worker threads (mirroring how mac_tools dispatch runs blocking
  osascript off the loop). So every connection is opened `check_same_thread=False`, each
  helper uses a SHORT-LIVED connection (open → do → commit → close), and a module-level
  Lock serializes writers. SQLite + WAL + a process-wide lock is plenty for a single
  phone call at a time; we are not building a high-QPS service.
* Tables (exactly as specced):
    calls   (call_sid PK, started_at, ended_at, direction, caller, summary)
    turns   (id, call_sid, ts, role, text)
    actions (id, call_sid, ts, tool, args, result)
    facts   (id, ts, kind, text, weight REAL, last_seen, source_call)  -- durable prefs/facts
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# data/agent_memory.db lives at the PROJECT ROOT (../../data from this src/agent_memory/ file).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DB_PATH = _DATA_DIR / "agent_memory.db"

# Process-wide writer lock. SQLite handles its own file locking, but serializing here keeps
# "read-modify-write" helpers (e.g. fact upsert) atomic across our worker threads.
_LOCK = threading.RLock()


def db_path() -> Path:
    """Where the store lives. Exposed so tests can point the module at a temp DB."""
    return _DB_PATH


def set_db_path(path: str | Path) -> None:
    """Override the DB location (used by tests to write under /tmp and not pollute data/).

    Re-initialization is the caller's job: call init_store() after pointing it somewhere new.
    """
    global _DB_PATH, _DATA_DIR
    _DB_PATH = Path(path)
    _DATA_DIR = _DB_PATH.parent


def _connect() -> sqlite3.Connection:
    """Open a short-lived connection. check_same_thread=False so a worker thread can use it;
    WAL keeps a reader from blocking our single writer. Always close after the helper."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_store() -> None:
    """Create data/ and the schema if absent. Idempotent — safe to call on every startup."""
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    call_sid   TEXT PRIMARY KEY,
                    started_at REAL,
                    ended_at   REAL,
                    direction  TEXT,
                    caller     TEXT,
                    summary    TEXT
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_sid TEXT,
                    ts       REAL,
                    role     TEXT,
                    text     TEXT
                );

                CREATE TABLE IF NOT EXISTS actions (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_sid TEXT,
                    ts       REAL,
                    tool     TEXT,
                    args     TEXT,
                    result   TEXT
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          REAL,
                    kind        TEXT,
                    text        TEXT,
                    weight      REAL,
                    last_seen   REAL,
                    source_call TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_turns_call   ON turns(call_sid);
                CREATE INDEX IF NOT EXISTS idx_actions_call ON actions(call_sid);
                CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller);
                """
            )
            conn.commit()
        finally:
            conn.close()


# --------------------------------------------------------------------------- calls

def upsert_call(
    call_sid: str,
    *,
    started_at: float | None = None,
    direction: str | None = None,
    caller: str | None = None,
) -> None:
    """Create the row for a call at start (or no-op-update if it already exists). We only set
    columns we were given so a later finalize doesn't clobber start-time metadata."""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO calls (call_sid, started_at, direction, caller)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(call_sid) DO UPDATE SET
                    started_at = COALESCE(calls.started_at, excluded.started_at),
                    direction  = COALESCE(excluded.direction, calls.direction),
                    caller     = COALESCE(excluded.caller, calls.caller)
                """,
                (call_sid, started_at if started_at is not None else time.time(), direction, caller),
            )
            conn.commit()
        finally:
            conn.close()


def finalize_call(call_sid: str, *, ended_at: float | None = None, summary: str | None = None) -> None:
    """Stamp the end time + store the call summary once the call is over."""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE calls
                   SET ended_at = ?, summary = ?
                 WHERE call_sid = ?
                """,
                (ended_at if ended_at is not None else time.time(), summary, call_sid),
            )
            conn.commit()
        finally:
            conn.close()


def get_call(call_sid: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM calls WHERE call_sid = ?", (call_sid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def recent_calls_for_caller(caller: str, limit: int = 3) -> list[dict]:
    """Most-recent finished calls for a caller that actually have a summary (so retrieval
    only surfaces calls we have something to say about)."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM calls
             WHERE caller = ? AND summary IS NOT NULL AND summary != ''
             ORDER BY COALESCE(ended_at, started_at) DESC
             LIMIT ?
            """,
            (caller, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- turns

def add_turn(call_sid: str, role: str, text: str, ts: float | None = None) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO turns (call_sid, ts, role, text) VALUES (?, ?, ?, ?)",
                (call_sid, ts if ts is not None else time.time(), role, text),
            )
            conn.commit()
        finally:
            conn.close()


def get_turns(call_sid: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM turns WHERE call_sid = ? ORDER BY id", (call_sid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- actions

def add_action(call_sid: str, tool: str, args: str, result: str, ts: float | None = None) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO actions (call_sid, ts, tool, args, result) VALUES (?, ?, ?, ?, ?)",
                (call_sid, ts if ts is not None else time.time(), tool, args, result),
            )
            conn.commit()
        finally:
            conn.close()


def get_actions(call_sid: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM actions WHERE call_sid = ? ORDER BY id", (call_sid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- facts

def upsert_fact(
    kind: str,
    text: str,
    *,
    source_call: str | None = None,
    weight_bump: float = 1.0,
    ts: float | None = None,
) -> None:
    """Durable fact/preference about the caller. Dedupe: if a fact with the same (kind, text)
    already exists (case-insensitive), BUMP its weight and refresh last_seen instead of adding
    a duplicate — so things mentioned across calls float to the top of retrieval."""
    now = ts if ts is not None else time.time()
    text = (text or "").strip()
    if not text:
        return
    with _LOCK:
        conn = _connect()
        try:
            existing = conn.execute(
                """
                SELECT id, weight FROM facts
                 WHERE kind = ? AND lower(text) = lower(?)
                 LIMIT 1
                """,
                (kind, text),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE facts SET weight = ?, last_seen = ?, source_call = ? WHERE id = ?",
                    (float(existing["weight"]) + weight_bump, now, source_call, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO facts (ts, kind, text, weight, last_seen, source_call)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now, kind, text, weight_bump, now, source_call),
                )
            conn.commit()
        finally:
            conn.close()


def top_facts(limit: int = 8) -> list[dict]:
    """Top facts ranked by weight, then recency. Retrieval re-ranks finely (weight*recency);
    this just hands back a generous, already-sorted candidate set."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM facts
             ORDER BY weight DESC, last_seen DESC
             LIMIT ?
            """,
            (max(limit * 4, limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def all_facts() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM facts ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recent_calls(limit: int = 3) -> list[dict]:
    """Most-recent calls across ALL callers that have a summary. Used by the memory lookup
    tools / CLI to surface "the last few calls" regardless of who they were with."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM calls
             WHERE summary IS NOT NULL AND summary != ''
             ORDER BY COALESCE(ended_at, started_at) DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- search

def search(query: str, limit: int = 8) -> dict:
    """Case-insensitive substring search across durable facts (facts.text) and call summaries
    (calls.summary). Returns {"facts": [...], "calls": [...]} — both lists of plain dicts,
    most-relevant-first (facts by weight then recency, calls by recency). Minimal by design:
    plain SQL LIKE over the two text columns, reusing the short-lived connection helper.

    An empty/whitespace query returns the top facts + most recent summarized calls so callers
    have a sensible "what do you remember in general?" default."""
    q = (query or "").strip()
    conn = _connect()
    try:
        if not q:
            facts = conn.execute(
                "SELECT * FROM facts ORDER BY weight DESC, last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
            calls = conn.execute(
                """
                SELECT * FROM calls
                 WHERE summary IS NOT NULL AND summary != ''
                 ORDER BY COALESCE(ended_at, started_at) DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            like = f"%{q}%"
            facts = conn.execute(
                """
                SELECT * FROM facts
                 WHERE text LIKE ? COLLATE NOCASE
                 ORDER BY weight DESC, last_seen DESC
                 LIMIT ?
                """,
                (like, limit),
            ).fetchall()
            calls = conn.execute(
                """
                SELECT * FROM calls
                 WHERE summary LIKE ? COLLATE NOCASE
                 ORDER BY COALESCE(ended_at, started_at) DESC
                 LIMIT ?
                """,
                (like, limit),
            ).fetchall()
        return {"facts": [dict(r) for r in facts], "calls": [dict(r) for r in calls]}
    finally:
        conn.close()
