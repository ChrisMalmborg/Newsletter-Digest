"""Persist and retrieve OAuth tokens for users.

Manages a ``users`` table with columns: id, email, oauth_tokens (JSON),
created_at, updated_at.
"""

import json
import sqlite3
from datetime import datetime
from typing import Optional

from src.config import DATABASE_PATH


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_users_table():
    """Create the users table if it doesn't already exist."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            oauth_tokens TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_user_tokens(user_email: str, credentials: dict):
    """Store (or update) OAuth tokens for a user.

    If the user already exists, their tokens and ``updated_at`` timestamp are
    refreshed.  Otherwise a new row is inserted.
    """
    _ensure_users_table()
    conn = _get_connection()
    cursor = conn.cursor()

    tokens_json = json.dumps(credentials)
    now = datetime.utcnow().isoformat()

    cursor.execute("SELECT id FROM users WHERE email = ?", (user_email,))
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE users SET oauth_tokens = ?, updated_at = ? WHERE id = ?",
            (tokens_json, now, row["id"]),
        )
    else:
        cursor.execute(
            "INSERT INTO users (email, oauth_tokens, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_email, tokens_json, now, now),
        )

    conn.commit()
    conn.close()


def get_user_tokens(user_email: str) -> Optional[dict]:
    """Retrieve stored OAuth tokens for a user, or ``None`` if not found."""
    _ensure_users_table()
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT oauth_tokens FROM users WHERE email = ?", (user_email,))
    row = cursor.fetchone()
    conn.close()

    if row and row["oauth_tokens"]:
        return json.loads(row["oauth_tokens"])
    return None


def get_user_id_by_email(user_email: str) -> Optional[int]:
    """Return the user's database ID, or ``None`` if not found."""
    _ensure_users_table()
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE email = ?", (user_email,))
    row = cursor.fetchone()
    conn.close()

    return row["id"] if row else None


def get_all_users_with_tokens() -> list[str]:
    """Return email addresses of all users who have stored OAuth tokens."""
    _ensure_users_table()
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT email FROM users WHERE oauth_tokens IS NOT NULL AND oauth_tokens != '{}'"
    )
    rows = cursor.fetchall()
    conn.close()

    return [row["email"] for row in rows]
