import json
import sqlite3
from datetime import datetime, date
from typing import Optional, Any

from .config import DATABASE_PATH, DATABASE_URL
from .models import Newsletter, Email, Summary, Cluster, Subscription

IS_POSTGRES = bool(DATABASE_URL)

# ---------------------------------------------------------------------------
# PostgreSQL connection pool (only initialised when DATABASE_URL is set)
# ---------------------------------------------------------------------------

if IS_POSTGRES:
    import psycopg2
    import psycopg2.pool
    from psycopg2.extras import RealDictCursor

    # Expose a DB-agnostic IntegrityError that callers can catch.
    IntegrityError = psycopg2.IntegrityError

    _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)

    class _PooledConnection:
        """Thin wrapper around a psycopg2 connection borrowed from the pool.

        Intercepts close() to return the connection to the pool instead of
        discarding it, so the rest of the code can call conn.close() freely.
        """

        def __init__(self, conn):
            self._conn = conn

        def cursor(self):  # noqa: D102
            return self._conn.cursor(cursor_factory=RealDictCursor)

        def commit(self):  # noqa: D102
            self._conn.commit()

        def rollback(self):  # noqa: D102
            self._conn.rollback()

        def close(self):  # returns connection to pool rather than closing it
            _pool.putconn(self._conn)

else:
    IntegrityError = sqlite3.IntegrityError


def get_connection():
    """Return a database connection (SQLite or pooled PostgreSQL)."""
    if IS_POSTGRES:
        return _PooledConnection(_pool.getconn())
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Query helpers for cross-database compatibility
# ---------------------------------------------------------------------------

def _q(query: str) -> str:
    """Replace SQLite-style ? placeholders with %s for PostgreSQL."""
    if IS_POSTGRES:
        return query.replace("?", "%s")
    return query


def _insert_and_get_id(cursor, query: str, params: tuple) -> int:
    """Execute an INSERT and return the generated primary key.

    PostgreSQL uses RETURNING id; SQLite uses cursor.lastrowid.
    The *query* must use ? placeholders (they are adapted automatically).
    """
    if IS_POSTGRES:
        cursor.execute(_q(query) + " RETURNING id", params)
        return cursor.fetchone()["id"]
    cursor.execute(query, params)
    return cursor.lastrowid


def _pk_col() -> str:
    """Return the DDL fragment for an auto-incrementing primary key column."""
    return "id SERIAL PRIMARY KEY" if IS_POSTGRES else "id INTEGER PRIMARY KEY AUTOINCREMENT"


def _date_cast(column: str) -> str:
    """Return a SQL expression that extracts the DATE portion of a timestamp column."""
    return f"{column}::date" if IS_POSTGRES else f"date({column})"


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse a datetime that may already be a datetime (PostgreSQL) or a string (SQLite)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    pk = _pk_col()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS newsletters (
            {pk},
            sender_email TEXT UNIQUE NOT NULL,
            sender_name TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS emails (
            {pk},
            newsletter_id INTEGER NOT NULL,
            message_id TEXT UNIQUE NOT NULL,
            subject TEXT NOT NULL,
            received_at TIMESTAMP NOT NULL,
            raw_html TEXT,
            plain_text TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (newsletter_id) REFERENCES newsletters(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS summaries (
            {pk},
            email_id INTEGER UNIQUE NOT NULL,
            key_points TEXT DEFAULT '[]',
            entities TEXT DEFAULT '[]',
            topic_tags TEXT DEFAULT '[]',
            notable_links TEXT DEFAULT '[]',
            importance_score INTEGER DEFAULT 5,
            one_line_summary TEXT DEFAULT '',
            FOREIGN KEY (email_id) REFERENCES emails(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS clusters (
            {pk},
            digest_date TEXT NOT NULL,
            cluster_name TEXT NOT NULL,
            summary TEXT DEFAULT '',
            email_ids TEXT DEFAULT '[]',
            source_count INTEGER DEFAULT 0
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS subscriptions (
            {pk},
            user_id INTEGER NOT NULL DEFAULT 1,
            sender_email TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            is_active SMALLINT NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, sender_email)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS digests (
            {pk},
            user_email TEXT NOT NULL,
            digest_date TEXT NOT NULL,
            subject TEXT NOT NULL,
            html_content TEXT NOT NULL,
            themes_count INTEGER DEFAULT 0,
            newsletters_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            {pk},
            email TEXT UNIQUE NOT NULL,
            oauth_tokens TEXT DEFAULT '{{}}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes for common queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clusters_date ON clusters(digest_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id, is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_digests_user ON digests(user_email, digest_date)")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Newsletter helpers
# ---------------------------------------------------------------------------

def get_or_create_newsletter(sender_email: str, sender_name: str) -> int:
    """Get existing newsletter ID or create new one."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(_q("SELECT id FROM newsletters WHERE sender_email = ?"), (sender_email,))
    row = cursor.fetchone()

    if row:
        newsletter_id = row["id"]
    else:
        newsletter_id = _insert_and_get_id(
            cursor,
            "INSERT INTO newsletters (sender_email, sender_name) VALUES (?, ?)",
            (sender_email, sender_name),
        )
        conn.commit()

    conn.close()
    return newsletter_id


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def save_email(email: Email) -> int:
    """Save an email and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()

    email_id = _insert_and_get_id(
        cursor,
        """INSERT INTO emails (newsletter_id, message_id, subject, received_at, raw_html, plain_text, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            email.newsletter_id,
            email.message_id,
            email.subject,
            email.received_at.isoformat(),
            email.raw_html,
            email.plain_text,
            email.status,
        ),
    )

    conn.commit()
    conn.close()
    return email_id


def get_unprocessed_emails() -> list[Email]:
    """Get all emails with status 'pending'."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM emails WHERE status = 'pending' ORDER BY received_at")
    rows = cursor.fetchall()
    conn.close()

    return [_row_to_email(row) for row in rows]


def get_email_by_id(email_id: int) -> Optional[Email]:
    """Get a single email by ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(_q("SELECT * FROM emails WHERE id = ?"), (email_id,))
    row = cursor.fetchone()
    conn.close()

    return _row_to_email(row) if row else None


def _row_to_email(row) -> Email:
    return Email(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        message_id=row["message_id"],
        subject=row["subject"],
        received_at=_parse_dt(row["received_at"]),
        raw_html=row["raw_html"],
        plain_text=row["plain_text"],
        status=row["status"],
        created_at=_parse_dt(row["created_at"]),
    )


def update_email_status(email_id: int, status: str):
    """Update the status of an email."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(_q("UPDATE emails SET status = ? WHERE id = ?"), (status, email_id))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def save_summary(summary: Summary) -> int:
    """Save a summary and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()

    summary_id = _insert_and_get_id(
        cursor,
        """INSERT INTO summaries (email_id, key_points, entities, topic_tags, notable_links, importance_score, one_line_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            summary.email_id,
            json.dumps(summary.key_points),
            json.dumps(summary.entities),
            json.dumps(summary.topic_tags),
            json.dumps(summary.notable_links),
            summary.importance_score,
            summary.one_line_summary,
        ),
    )

    conn.commit()
    conn.close()
    return summary_id


def get_todays_summaries() -> list[Summary]:
    """Get all summaries for emails received today."""
    return get_summaries_for_date(date.today().isoformat())


def get_summaries_by_email_ids(email_ids: list[int]) -> list[Summary]:
    """Get summaries for a specific set of email IDs."""
    if not email_ids:
        return []
    conn = get_connection()
    cursor = conn.cursor()

    if IS_POSTGRES:
        cursor.execute(
            "SELECT s.* FROM summaries s WHERE s.email_id = ANY(%s) ORDER BY s.importance_score DESC",
            (list(email_ids),),
        )
    else:
        placeholders = ",".join("?" for _ in email_ids)
        cursor.execute(
            f"SELECT s.* FROM summaries s WHERE s.email_id IN ({placeholders}) ORDER BY s.importance_score DESC",
            email_ids,
        )

    rows = cursor.fetchall()
    conn.close()

    return [_row_to_summary(row) for row in rows]


def get_summaries_for_date(target_date: str) -> list[Summary]:
    """Get all summaries for emails received on a specific date."""
    conn = get_connection()
    cursor = conn.cursor()

    date_expr = _date_cast("e.received_at")
    cursor.execute(
        _q(f"""
            SELECT s.* FROM summaries s
            JOIN emails e ON s.email_id = e.id
            WHERE {date_expr} = ?
            ORDER BY s.importance_score DESC
        """),
        (target_date,),
    )

    rows = cursor.fetchall()
    conn.close()

    return [_row_to_summary(row) for row in rows]


def _row_to_summary(row) -> Summary:
    return Summary(
        id=row["id"],
        email_id=row["email_id"],
        key_points=json.loads(row["key_points"]),
        entities=json.loads(row["entities"]),
        topic_tags=json.loads(row["topic_tags"]),
        notable_links=json.loads(row["notable_links"]),
        importance_score=row["importance_score"],
        one_line_summary=row["one_line_summary"],
    )


# ---------------------------------------------------------------------------
# Cluster helpers
# ---------------------------------------------------------------------------

def save_cluster(cluster: Cluster) -> int:
    """Save a cluster and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cluster_id = _insert_and_get_id(
        cursor,
        """INSERT INTO clusters (digest_date, cluster_name, summary, email_ids, source_count)
           VALUES (?, ?, ?, ?, ?)""",
        (
            cluster.digest_date,
            cluster.cluster_name,
            cluster.summary,
            json.dumps(cluster.email_ids),
            cluster.source_count,
        ),
    )

    conn.commit()
    conn.close()
    return cluster_id


def get_todays_clusters() -> list[Cluster]:
    """Get all clusters for today's digest."""
    return get_clusters_for_date(date.today().isoformat())


def get_clusters_for_date(target_date: str) -> list[Cluster]:
    """Get all clusters for a specific date."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("SELECT * FROM clusters WHERE digest_date = ? ORDER BY source_count DESC"),
        (target_date,),
    )

    rows = cursor.fetchall()
    conn.close()

    return [_row_to_cluster(row) for row in rows]


def _row_to_cluster(row) -> Cluster:
    return Cluster(
        id=row["id"],
        digest_date=row["digest_date"],
        cluster_name=row["cluster_name"],
        summary=row["summary"],
        email_ids=json.loads(row["email_ids"]),
        source_count=row["source_count"],
    )


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

def _row_to_subscription(row) -> Subscription:
    return Subscription(
        id=row["id"],
        user_id=row["user_id"],
        sender_email=row["sender_email"],
        sender_name=row["sender_name"],
        is_active=bool(row["is_active"]),
        created_at=_parse_dt(row["created_at"]),
    )


def get_active_subscriptions(user_id: int = 1) -> list[Subscription]:
    """Return active subscriptions for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("SELECT * FROM subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY sender_name"),
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    return [_row_to_subscription(row) for row in rows]


def get_all_subscriptions(user_id: int = 1) -> list[Subscription]:
    """Return all subscriptions (including inactive) for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY is_active DESC, sender_name"),
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    return [_row_to_subscription(row) for row in rows]


def add_subscription(sender_email: str, sender_name: str, user_id: int = 1) -> int:
    """Insert a new subscription or reactivate an existing one. Returns the subscription ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("SELECT id, is_active FROM subscriptions WHERE user_id = ? AND sender_email = ?"),
        (user_id, sender_email),
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            _q("UPDATE subscriptions SET is_active = 1, sender_name = ? WHERE id = ?"),
            (sender_name, row["id"]),
        )
        sub_id = row["id"]
    else:
        sub_id = _insert_and_get_id(
            cursor,
            "INSERT INTO subscriptions (user_id, sender_email, sender_name) VALUES (?, ?, ?)",
            (user_id, sender_email, sender_name),
        )

    conn.commit()
    conn.close()
    return sub_id


def deactivate_subscription(sender_email: str, user_id: int = 1) -> bool:
    """Deactivate a subscription. Returns True if a row was updated."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("UPDATE subscriptions SET is_active = 0 WHERE user_id = ? AND sender_email = ? AND is_active = 1"),
        (user_id, sender_email),
    )
    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()
    return updated


def is_subscribed(sender_email: str, user_id: int = 1) -> bool:
    """Check if a sender is actively subscribed."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("SELECT 1 FROM subscriptions WHERE user_id = ? AND sender_email = ? AND is_active = 1"),
        (user_id, sender_email),
    )
    row = cursor.fetchone()
    conn.close()

    return row is not None


def update_subscription_status(subscription_id: int, is_active: bool) -> bool:
    """Update a subscription's active status. Returns True if a row was updated."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        _q("UPDATE subscriptions SET is_active = ? WHERE id = ?"),
        (1 if is_active else 0, subscription_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_subscribed_sender_emails(user_id: int = 1) -> set[str]:
    """Return the set of active sender emails for fast lookups during processing."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _q("SELECT sender_email FROM subscriptions WHERE user_id = ? AND is_active = 1"),
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    return {row["sender_email"] for row in rows}


# ---------------------------------------------------------------------------
# Digest helpers
# ---------------------------------------------------------------------------

def save_digest(
    user_email: str,
    digest_date: str,
    subject: str,
    html_content: str,
    themes_count: int = 0,
    newsletters_count: int = 0,
) -> int:
    """Save a generated digest and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    digest_id = _insert_and_get_id(
        cursor,
        """INSERT INTO digests
           (user_email, digest_date, subject, html_content, themes_count, newsletters_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_email, digest_date, subject, html_content, themes_count, newsletters_count),
    )
    conn.commit()
    conn.close()
    return digest_id


def get_digests_for_user(user_email: str, limit: int = 30) -> list:
    """Return recent digests for a user, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        _q("""SELECT id, user_email, digest_date, subject, themes_count, newsletters_count, created_at
           FROM digests
           WHERE user_email = ?
           ORDER BY digest_date DESC
           LIMIT ?"""),
        (user_email, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_digest_by_id(digest_id: int) -> Optional[dict]:
    """Return a single digest (including html_content) by its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(_q("SELECT * FROM digests WHERE id = ?"), (digest_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None
