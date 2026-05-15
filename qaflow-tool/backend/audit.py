"""Append-only AI audit log.

Every AI decision lands here so we can answer questions like:
  - which engine answered this bug? (rules / claude / cache hit)
  - how long did the whole loop take?
  - cumulative LLM spend for the week?
  - did this AI fix get rejected later?

Stored in the same sqlite db. Append-only — no updates, no deletes from the
hot path. Truncate manually for housekeeping.
"""

from __future__ import annotations

import time
from typing import Any

from db import _connect

AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS ai_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    event_type    TEXT    NOT NULL,        -- 'cypress_fix' | 'test_writer' | etc
    bug_uid       TEXT,
    framework_id  TEXT,
    engine        TEXT,                    -- 'rule-based' | 'claude' | 'mock' | 'cache-hit'
    cache_hit     INTEGER DEFAULT 0,
    success       INTEGER NOT NULL,
    duration_ms   INTEGER,
    summary       TEXT,
    error         TEXT
);
"""

AUDIT_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON ai_audit(ts);",
    "CREATE INDEX IF NOT EXISTS idx_audit_bug_uid ON ai_audit(bug_uid);",
    "CREATE INDEX IF NOT EXISTS idx_audit_event ON ai_audit(event_type);",
]


def init() -> None:
    conn = _connect()
    try:
        conn.execute(AUDIT_TABLE_DDL)
        for ddl in AUDIT_INDEX_DDL:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def record(
    event_type: str,
    *,
    success: bool,
    bug_uid: str | None = None,
    framework_id: str | None = None,
    engine: str | None = None,
    cache_hit: bool = False,
    duration_ms: int | None = None,
    summary: str | None = None,
    error: str | None = None,
) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO ai_audit "
            "(ts, event_type, bug_uid, framework_id, engine, cache_hit, "
            " success, duration_ms, summary, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(), event_type, bug_uid, framework_id, engine,
                1 if cache_hit else 0, 1 if success else 0,
                duration_ms, summary, error,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def list_recent(limit: int = 50, event_type: str | None = None) -> list[dict]:
    conn = _connect()
    try:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM ai_audit WHERE event_type = ? "
                "ORDER BY ts DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_audit ORDER BY ts DESC LIMIT ?", (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def stats(window_s: float = 24 * 3600) -> dict:
    """Coarse aggregates over the last `window_s` seconds (default = 24h)."""
    cutoff = time.time() - window_s
    conn = _connect()
    try:
        agg = conn.execute(
            "SELECT "
            " COUNT(*)               AS calls, "
            " SUM(success)           AS successes, "
            " SUM(cache_hit)         AS cache_hits, "
            " AVG(duration_ms)       AS avg_duration_ms, "
            " MIN(ts) AS oldest, MAX(ts) AS newest "
            "FROM ai_audit WHERE ts >= ?",
            (cutoff,),
        ).fetchone()
        by_engine = conn.execute(
            "SELECT engine, COUNT(*) AS n FROM ai_audit "
            "WHERE ts >= ? GROUP BY engine",
            (cutoff,),
        ).fetchall()
        d = _row_to_dict(agg) if agg else {}
        d["window_s"] = window_s
        d["by_engine"] = {(_row_to_dict(r) or {}).get("engine") or "—":
                          (_row_to_dict(r) or {}).get("n", 0)
                          for r in by_engine}
        return d
    finally:
        conn.close()


def _row_to_dict(row: Any) -> dict | None:
    if row is None:
        return None
    if hasattr(row, "keys"):  # sqlite3.Row
        return {k: row[k] for k in row.keys()}
    return dict(row)
