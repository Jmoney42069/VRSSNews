"""
db.py — SQLite database layer for the energy news tracker.

Handles table creation, article storage, deduplication, querying,
and automatic cleanup of articles older than 7 days.
"""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager

log = logging.getLogger("energy-tracker.db")

DB_PATH = Path("energy_news.db")
RETENTION_DAYS = 7


@contextmanager
def get_connection():
    """Thread-safe SQLite connection context manager."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the articles table if it doesn't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                link        TEXT    NOT NULL UNIQUE,
                summary     TEXT    DEFAULT '',
                source      TEXT    DEFAULT '',
                category    TEXT    DEFAULT 'INT',
                score       INTEGER DEFAULT 0,
                keywords    TEXT    DEFAULT '',
                sentiment   TEXT    DEFAULT '',
                tier        INTEGER DEFAULT 2,
                alerted     INTEGER DEFAULT 0,
                created_at  TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_created
            ON articles (created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_category
            ON articles (category)
        """)
    log.info("Database initialised at %s", DB_PATH)


def link_exists(link: str) -> bool:
    """Check if an article link is already stored."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM articles WHERE link = ?", (link,)
        ).fetchone()
        return row is not None


def insert_article(article: dict) -> bool:
    """
    Insert a new article. Returns True if inserted, False if duplicate.
    Expected keys: title, link, summary, source, category, score, keywords,
    sentiment, tier.
    """
    if link_exists(article["link"]):
        return False

    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO articles
                   (title, link, summary, source, category, score, keywords,
                    sentiment, tier, alerted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    article["title"],
                    article["link"],
                    article.get("summary", ""),
                    article.get("source", ""),
                    article.get("category", "INT"),
                    article.get("score", 0),
                    article.get("keywords", ""),
                    article.get("sentiment", ""),
                    article.get("tier", 2),
                    now,
                ),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def mark_alerted(link: str) -> None:
    """Mark an article as having been sent via notifications."""
    with get_connection() as conn:
        conn.execute("UPDATE articles SET alerted = 1 WHERE link = ?", (link,))


def get_unalerted_articles() -> list[dict]:
    """Return articles that haven't been alerted yet, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM articles
               WHERE alerted = 0
               ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_articles(
    category: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Return articles from the last 7 days for the dashboard.
    Optionally filter by category and/or search term.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    query = "SELECT * FROM articles WHERE created_at > ?"
    params: list = [cutoff]

    if category and category in ("NL", "INT"):
        query += " AND category = ?"
        params.append(category)

    if search:
        query += " AND (title LIKE ? OR summary LIKE ?)"
        term = f"%{search}%"
        params.append(term)
        params.append(term)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_article_count() -> dict:
    """Return article counts by category."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM articles GROUP BY category"
        ).fetchall()
        counts = {r["category"]: r["cnt"] for r in rows}
        counts["total"] = sum(counts.values())
        return counts


def cleanup_old_articles() -> int:
    """Delete articles older than RETENTION_DAYS. Returns count deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM articles WHERE created_at < ?", (cutoff,)
        )
        deleted = cursor.rowcount
    if deleted:
        log.info("Cleaned up %d old articles", deleted)
    return deleted
