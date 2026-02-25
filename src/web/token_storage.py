"""Persist and retrieve OAuth tokens for users.

Manages a ``users`` table with columns: id, email, oauth_tokens (JSON),
created_at, updated_at.

The users table is created by ``src.database.init_db()``.  This module
re-uses the same dual-database infrastructure (SQLite locally, PostgreSQL in
production via DATABASE_URL) defined there.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from src.database import get_connection, _q, _insert_and_get_id


def save_user_tokens(user_email: str, credentials: dict):
    """Store (or update) OAuth tokens for a user.

    If the user already exists, their tokens and ``updated_at`` timestamp are
    refreshed.  Otherwise a new row is inserted.
    """
    conn = get_connection()
    cursor = conn.cursor()

    tokens_json = json.dumps(credentials)
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute(_q("SELECT id FROM users WHERE email = ?"), (user_email,))
    row = cursor.fetchone()

    if row:
        cursor.execute(
            _q("UPDATE users SET oauth_tokens = ?, updated_at = ? WHERE id = ?"),
            (tokens_json, now, row["id"]),
        )
    else:
        _insert_and_get_id(
            cursor,
            "INSERT INTO users (email, oauth_tokens, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_email, tokens_json, now, now),
        )

    conn.commit()
    conn.close()


def get_user_tokens(user_email: str) -> Optional[dict]:
    """Retrieve stored OAuth tokens for a user, or ``None`` if not found."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(_q("SELECT oauth_tokens FROM users WHERE email = ?"), (user_email,))
    row = cursor.fetchone()
    conn.close()

    if row and row["oauth_tokens"]:
        return json.loads(row["oauth_tokens"])
    return None


def get_user_id_by_email(user_email: str) -> Optional[int]:
    """Return the user's database ID, or ``None`` if not found."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(_q("SELECT id FROM users WHERE email = ?"), (user_email,))
    row = cursor.fetchone()
    conn.close()

    return row["id"] if row else None


def get_all_users_with_tokens() -> list[str]:
    """Return email addresses of all users who have stored OAuth tokens."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT email FROM users WHERE oauth_tokens IS NOT NULL AND oauth_tokens != '{}'"
    )
    rows = cursor.fetchall()
    conn.close()

    return [row["email"] for row in rows]
