"""SQLite layer for QAFLOW AI.

Stores users, sessions, manual bugs (reported by manual testers, worked on by
developers), and bug comments. AI auto-fix runs/bugs remain in-memory in
main.py for the demo — only persistent role/account/manual-bug data lives here.
"""

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "qaflow.sqlite3"

SEED_USERS = [
    # username, password, role, full_name
    ("dev1",  "12345678", "developer",          "Dev One"),
    ("auto1", "12345678", "automation_engineer", "Auto One"),
    ("pm1",   "12345678", "project_manager",    "PM One"),
    ("m1",    "12345678", "manual_tester",      "Manual Tester One"),
]

ROLES = {"developer", "automation_engineer", "project_manager", "manual_tester"}


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor():
    conn = _connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL,
  full_name     TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  token       TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_bugs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  title               TEXT NOT NULL,
  description         TEXT NOT NULL,
  severity            TEXT NOT NULL,
  page_url            TEXT,
  steps_to_reproduce  TEXT,
  reporter_id         INTEGER NOT NULL REFERENCES users(id),
  assignee_id         INTEGER REFERENCES users(id),
  status              TEXT NOT NULL DEFAULT 'OPEN',
  created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bug_comments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  bug_id      INTEGER NOT NULL REFERENCES manual_bugs(id) ON DELETE CASCADE,
  author_id   INTEGER NOT NULL REFERENCES users(id),
  body        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bugs_assignee ON manual_bugs(assignee_id);
CREATE INDEX IF NOT EXISTS idx_bugs_reporter ON manual_bugs(reporter_id);
CREATE INDEX IF NOT EXISTS idx_bugs_status   ON manual_bugs(status);
"""


def init_db():
    """Create tables and seed users if the DB is fresh."""
    with cursor() as cur:
        cur.executescript(SCHEMA)
        cur.execute("SELECT COUNT(*) AS n FROM users")
        if cur.fetchone()["n"] == 0:
            for username, password, role, full_name in SEED_USERS:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), role, full_name),
                )


# ---------------------------------------------------------------------------
# Password / token utilities
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${h}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return secrets.compare_digest(candidate, h)


def issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with cursor() as cur:
        cur.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    return token


def revoke_token(token: str):
    with cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token = ?", (token,))


def user_for_token(token: str) -> dict | None:
    with cursor() as cur:
        cur.execute(
            """SELECT u.id, u.username, u.role, u.full_name
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ?""",
            (token,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# User queries
# ---------------------------------------------------------------------------

def find_user_by_username(username: str) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with cursor() as cur:
        cur.execute("SELECT id, username, role, full_name, created_at FROM users ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def find_users_by_role(role: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT id, username, role, full_name FROM users WHERE role = ? ORDER BY username",
            (role,),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Manual bug queries
# ---------------------------------------------------------------------------

def create_manual_bug(*, title: str, description: str, severity: str,
                      page_url: str | None, steps: str | None,
                      reporter_id: int, assignee_id: int | None) -> dict:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO manual_bugs
                 (title, description, severity, page_url, steps_to_reproduce,
                  reporter_id, assignee_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, description, severity, page_url, steps, reporter_id, assignee_id),
        )
        bug_id = cur.lastrowid
    return get_manual_bug(bug_id)


def get_manual_bug(bug_id: int) -> dict | None:
    with cursor() as cur:
        cur.execute(
            """SELECT b.*,
                      r.username AS reporter_username, r.full_name AS reporter_name,
                      a.username AS assignee_username, a.full_name AS assignee_name
               FROM manual_bugs b
               JOIN users r ON r.id = b.reporter_id
               LEFT JOIN users a ON a.id = b.assignee_id
               WHERE b.id = ?""",
            (bug_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        bug = dict(row)
        cur.execute(
            """SELECT c.id, c.body, c.created_at,
                      u.username AS author_username, u.full_name AS author_name
               FROM bug_comments c JOIN users u ON u.id = c.author_id
               WHERE c.bug_id = ? ORDER BY c.id""",
            (bug_id,),
        )
        bug["comments"] = [dict(r) for r in cur.fetchall()]
        return bug


def list_manual_bugs(*, assignee_id: int | None = None,
                     reporter_id: int | None = None) -> list[dict]:
    sql = """SELECT b.*,
                    r.username AS reporter_username, r.full_name AS reporter_name,
                    a.username AS assignee_username, a.full_name AS assignee_name
             FROM manual_bugs b
             JOIN users r ON r.id = b.reporter_id
             LEFT JOIN users a ON a.id = b.assignee_id"""
    where, params = [], []
    if assignee_id is not None:
        where.append("b.assignee_id = ?")
        params.append(assignee_id)
    if reporter_id is not None:
        where.append("b.reporter_id = ?")
        params.append(reporter_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY b.id DESC"
    with cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def update_manual_bug_status(bug_id: int, status: str) -> dict | None:
    with cursor() as cur:
        cur.execute(
            "UPDATE manual_bugs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, bug_id),
        )
    return get_manual_bug(bug_id)


def assign_manual_bug(bug_id: int, assignee_id: int | None) -> dict | None:
    with cursor() as cur:
        cur.execute(
            "UPDATE manual_bugs SET assignee_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (assignee_id, bug_id),
        )
    return get_manual_bug(bug_id)


def add_comment(bug_id: int, author_id: int, body: str) -> dict:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO bug_comments (bug_id, author_id, body) VALUES (?, ?, ?)",
            (bug_id, author_id, body),
        )
        cur.execute(
            "UPDATE manual_bugs SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (bug_id,),
        )
    return get_manual_bug(bug_id)


# Initialize on import — single source of truth
init_db()
