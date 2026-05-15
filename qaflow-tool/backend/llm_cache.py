"""Persistent cache for LLM responses.

Same prompt + same model = same JSON we got last time. Saves money and
makes the demo near-instant on a re-run.

Backed by SQLite (the same db file the app already uses). Keys are
sha256 hashes of (model, prompt) so prompts can stay arbitrarily large.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from db import _connect  # reuses the existing qaflow.sqlite3

CACHE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key         TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    last_hit_at REAL,
    hits        INTEGER DEFAULT 0
);
"""


def init() -> None:
    conn = _connect()
    try:
        conn.execute(CACHE_TABLE_DDL)
        conn.commit()
    finally:
        conn.close()


def _key(model: str, prompt: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


def get(model: str, prompt: str) -> str | None:
    """Return cached response, or None on miss. Bumps hit counters on hit."""
    key = _key(model, prompt)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT response FROM llm_cache WHERE key = ?", (key,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE llm_cache SET hits = hits + 1, last_hit_at = ? WHERE key = ?",
            (time.time(), key),
        )
        conn.commit()
        return row["response"] if hasattr(row, "keys") else row[0]
    finally:
        conn.close()


def put(model: str, prompt: str, response: str) -> None:
    key = _key(model, prompt)
    now = time.time()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache "
            "(key, model, response, created_at, hits) "
            "VALUES (?, ?, ?, ?, COALESCE("
            "  (SELECT hits FROM llm_cache WHERE key = ?), 0))",
            (key, model, response, now, key),
        )
        conn.commit()
    finally:
        conn.close()


def stats() -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(hits),0) AS total_hits FROM llm_cache",
        ).fetchone()
        return {
            "entries":   row["entries"] if hasattr(row, "keys") else row[0],
            "total_hits":row["total_hits"] if hasattr(row, "keys") else row[1],
        }
    finally:
        conn.close()


def clear() -> int:
    conn = _connect()
    try:
        n = conn.execute("DELETE FROM llm_cache").rowcount
        conn.commit()
        return n
    finally:
        conn.close()
