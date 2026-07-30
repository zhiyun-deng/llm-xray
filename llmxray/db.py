"""SQLite storage — the single canonical store for all responses.

One row per (prompt_id, model). Re-running overwrites only with --force.
Full response text lives here too, so there is exactly one file to manage
regardless of how many prompt x model combinations exist.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id         TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    response          TEXT    NOT NULL,
    source            TEXT    NOT NULL DEFAULT 'openrouter',  -- 'openrouter' | 'manual'
    prompt_hash       TEXT,                                   -- prompt text hash at capture time
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    cost_usd          REAL,
    latency_ms        INTEGER,
    finish_reason     TEXT,
    error             TEXT,                                   -- set if the call failed
    created_at        TEXT    NOT NULL,
    raw_json          TEXT,                                   -- full API payload (auto only)
    reasoning_tokens  INTEGER,                                -- thinking tokens billed as output
    reasoning         TEXT,                                   -- trace, when the provider returns one
    reasoning_config  TEXT,                                   -- the reasoning object we sent (JSON)
    UNIQUE(prompt_id, model)
);
CREATE INDEX IF NOT EXISTS idx_responses_prompt ON responses(prompt_id);
CREATE INDEX IF NOT EXISTS idx_responses_model  ON responses(model);
"""

# Columns added after the first release; existing results.db files predate them.
_MIGRATIONS = (
    ("reasoning_tokens", "INTEGER"),
    ("reasoning", "TEXT"),
    ("reasoning_config", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns missing from an older results.db, preserving its rows."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(responses)")}
    for name, decl in _MIGRATIONS:
        if name not in have:
            conn.execute(f"ALTER TABLE responses ADD COLUMN {name} {decl}")
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def get_response(conn: sqlite3.Connection, prompt_id: str, model: str) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT * FROM responses WHERE prompt_id = ? AND model = ?",
        (prompt_id, model),
    )
    return cur.fetchone()


def has_success(conn: sqlite3.Connection, prompt_id: str, model: str) -> bool:
    row = get_response(conn, prompt_id, model)
    return row is not None and not row["error"]


def upsert_response(conn: sqlite3.Connection, **fields) -> None:
    """Insert or replace the single row for (prompt_id, model)."""
    fields.setdefault("created_at", now_iso())
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    updates = ", ".join(f"{c}=excluded.{c}" for c in fields if c not in ("prompt_id", "model"))
    sql = (
        f"INSERT INTO responses ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(prompt_id, model) DO UPDATE SET {updates}"
    )
    conn.execute(sql, tuple(fields.values()))
    conn.commit()


def responses_for_prompt(conn: sqlite3.Connection, prompt_id: str) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM responses WHERE prompt_id = ? ORDER BY model",
        (prompt_id,),
    )
    return cur.fetchall()


def all_responses(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM responses ORDER BY prompt_id, model").fetchall()


def stored_summary(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """One row per stored prompt_id, newest first."""
    return conn.execute(
        """
        SELECT prompt_id,
               COUNT(*)                                          AS n,
               SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
               MAX(created_at)                                   AS last_at
        FROM responses
        GROUP BY prompt_id
        ORDER BY last_at DESC
        """
    ).fetchall()


def model_totals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT model,
               COUNT(*)                            AS n,
               SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
               COALESCE(SUM(total_tokens), 0)      AS tokens,
               COALESCE(SUM(cost_usd), 0.0)        AS cost_usd,
               AVG(latency_ms)                     AS avg_latency_ms
        FROM responses
        GROUP BY model
        ORDER BY model
        """
    ).fetchall()
