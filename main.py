"""
main.py — Entry point for the Energy News Tracker.

Runs a Flask web dashboard and a background worker thread that polls
RSS feeds, filters articles, stores them in SQLite, and sends alerts.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, jsonify, make_response
from dotenv import load_dotenv

import db
import news
import notifier

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("energy-tracker")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
DIGEST_HOUR   = int(os.getenv("DIGEST_HOUR", "12"))  # clock hour in Amsterdam time

_AMS = ZoneInfo("Europe/Amsterdam")

# Last successful poll timestamp — loaded from DB on startup, persisted after each cycle
_last_poll_at: datetime | None = None
# Date on which the last digest was sent (Amsterdam date)
_last_digest_date: date | None = None

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _time_ago(iso_str: str) -> str:
    """Convert an ISO timestamp to a Dutch date/time string."""
    _NL_MONTHS = ["jan","feb","mrt","apr","mei","jun","jul","aug","sep","okt","nov","dec"]
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        today = now.date()
        yesterday = (now - timedelta(days=1)).date()
        d = dt.date()
        hhmm = dt.strftime("%H:%M")
        if d == today:
            return f"Vandaag {hhmm}"
        if d == yesterday:
            return f"Gisteren {hhmm}"
        return f"{d.day} {_NL_MONTHS[d.month - 1]}"
    except Exception:
        return ""


@app.route("/")
def index():
    """Dashboard — toont alle artikelen van de afgelopen 7 dagen."""
    search = request.args.get("q", "").strip()
    cat_filter = request.args.get("cat", "").strip().upper()
    topic_filter = request.args.get("topic", "").strip()

    category = cat_filter if cat_filter in ("NL", "INT") else None
    articles = db.get_recent_articles(
        category=category,
        search=search or None,
        topic=topic_filter or None,
    )

    # Split into NL / INT
    nl_articles = [a for a in articles if a["category"] == "NL"]
    int_articles = [a for a in articles if a["category"] == "INT"]

    # Add time-ago to each
    for a in articles:
        a["time_ago"] = _time_ago(a.get("published_at") or a["created_at"])

    counts = db.get_article_count()
    topic_counts = db.get_topic_counts()
    today_count = db.get_today_count()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Collect unique active sources
    sources = sorted({a["source"] for a in articles if a.get("source")})

    resp = make_response(render_template(
        "index.html",
        nl_articles=nl_articles,
        int_articles=int_articles,
        counts=counts,
        search=search,
        cat_filter=cat_filter,
        topic_filter=topic_filter,
        topic_counts=topic_counts,
        all_topics=news.ALL_TOPICS,
        sources=sources,
        total_feeds=len(news.RSS_FEEDS),
        last_updated=_last_poll_at.strftime("%H:%M UTC") if _last_poll_at else "nog niet",
        today_count=today_count,
        today_str=today_str,
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/api/status")
def api_status():
    """JSON endpoint: returns last poll time, article count, and worker health."""
    count = db.get_article_count()
    now = datetime.now(timezone.utc)
    seconds_since_poll = (now - _last_poll_at).total_seconds() if _last_poll_at else None
    return jsonify({
        "last_poll_utc": _last_poll_at.strftime("%Y-%m-%dT%H:%M:%SZ") if _last_poll_at else None,
        "seconds_since_last_poll": round(seconds_since_poll) if seconds_since_poll is not None else None,
        "worker_healthy": seconds_since_poll is not None and seconds_since_poll < POLL_INTERVAL * 3,
        "article_count": count.get("total", 0),
        "poll_interval_s": POLL_INTERVAL,
    })


@app.route("/api/articles")
def api_articles():
    """JSON API — return all recent articles."""
    category = request.args.get("cat")
    search = request.args.get("q")
    articles = db.get_recent_articles(
        category=category if category in ("NL", "INT") else None,
        search=search or None,
    )
    for a in articles:
        a["time_ago"] = _time_ago(a.get("published_at") or a["created_at"])
    return jsonify(articles)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


def _worker_cycle() -> None:
    """Single poll-filter-store-alert-cleanup cycle."""
    global _last_poll_at
    log.info("─── Poll cycle start ───")
    try:
        # 1. Fetch from all feeds
        articles = news.fetch_all_feeds()

        # 2. Filter & enrich (score, classify, sentiment)
        relevant = news.filter_and_enrich(articles)

        # 3. Store new articles
        new_count = 0
        for article in relevant:
            if db.insert_article(article):
                new_count += 1

        log.info("Stored %d new articles (%d already existed)", new_count, len(relevant) - new_count)

        # 4. Cleanup old data
        db.cleanup_old_articles()
        log.info("─── Poll cycle done ───")
    except Exception:
        log.exception("Poll cycle crashed")
    finally:
        _last_poll_at = datetime.now(timezone.utc)
        db.set_meta("last_poll_at", _last_poll_at.isoformat())


def _send_daily_digest(now_ams: datetime) -> None:
    """Fetch articles from the last 24 h and email the digest."""
    _NL_MONTHS = ["jan","feb","mrt","apr","mei","jun","jul","aug","sep","okt","nov","dec"]
    until_ams = now_ams.replace(minute=0, second=0, microsecond=0)
    since_ams = until_ams - timedelta(hours=24)
    # Convert window to UTC ISO strings for DB query
    since_utc = since_ams.astimezone(timezone.utc).isoformat()
    until_utc = until_ams.astimezone(timezone.utc).isoformat()
    articles = db.get_digest_articles(since_utc, until_utc)
    log.info("Digest: %d articles between %s and %s", len(articles), since_ams, until_ams)
    since_label = f"{since_ams.day} {_NL_MONTHS[since_ams.month-1]} {since_ams.year} {since_ams.strftime('%H:%M')}"
    until_label = f"{until_ams.day} {_NL_MONTHS[until_ams.month-1]} {until_ams.year} {until_ams.strftime('%H:%M')}"
    period_label = f"{since_label} – {until_label}"
    notifier.send_digest_email(articles, period_label)


def _digest_worker() -> None:
    """Fires _send_daily_digest once per day at DIGEST_HOUR (Amsterdam time)."""
    global _last_digest_date
    log.info("Digest worker started (fires at %02d:00 Amsterdam time)", DIGEST_HOUR)
    while True:
        now_ams = datetime.now(_AMS)
        if now_ams.hour == DIGEST_HOUR and now_ams.date() != _last_digest_date:
            try:
                _send_daily_digest(now_ams)
                _last_digest_date = now_ams.date()
                db.set_meta("last_digest_date", _last_digest_date.isoformat())
            except Exception:
                log.exception("Digest worker failed")
        time.sleep(60)


def background_worker() -> None:
    """Infinite loop that runs _worker_cycle every POLL_INTERVAL seconds."""
    log.info("Background worker started (interval=%ds)", POLL_INTERVAL)
    while True:
        try:
            _worker_cycle()
        except Exception:
            log.exception("Worker cycle failed — will retry next interval")

        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Auto-start: init DB + launch worker on import (needed for gunicorn)
_worker_started = False


def _backfill_topics() -> None:
    """Assign topics to existing articles that have an empty topic field."""
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, summary FROM articles WHERE topic = '' OR topic IS NULL"
        ).fetchall()
        if not rows:
            return
        updates = []
        for row in rows:
            topic = news.detect_topic([], row["title"], row["summary"] or "")
            updates.append((topic, row["id"]))
        conn.executemany("UPDATE articles SET topic = ? WHERE id = ?", updates)
    log.info("Backfilled topics for %d articles", len(updates))


def _ensure_started():
    """Initialise DB and start background worker (once)."""
    global _worker_started, _last_poll_at
    if _worker_started:
        return
    _worker_started = True

    db.init_db()
    _backfill_topics()

    # Restore last poll time from DB so /api/status survives restarts
    stored = db.get_meta("last_poll_at")
    if stored:
        try:
            _last_poll_at = datetime.fromisoformat(stored)
            log.info("Restored last_poll_at from DB: %s", stored)
        except Exception:
            pass

    # Restore last digest date so we don't double-send after a restart
    stored_digest = db.get_meta("last_digest_date")
    if stored_digest:
        try:
            _last_digest_date = date.fromisoformat(stored_digest)
            log.info("Restored last_digest_date from DB: %s", stored_digest)
        except Exception:
            pass

    log.info("=" * 60)
    log.info("⚡ Energy News Tracker")
    log.info("  Feeds     : %d", len(news.RSS_FEEDS))
    log.info("  Interval  : %ds", POLL_INTERVAL)
    log.info("  Telegram  : %s", "✓" if os.getenv("TELEGRAM_TOKEN") else "✗")
    log.info("  Email     : %s", "✓" if os.getenv("EMAIL_USER") else "✗")
    log.info("  OpenAI    : %s", "✓" if os.getenv("OPENAI_API_KEY") else "✗ (fallback)")
    log.info("=" * 60)

    worker = threading.Thread(target=background_worker, daemon=True)
    worker.start()

    digest = threading.Thread(target=_digest_worker, daemon=True)
    digest.start()


# Start on import so gunicorn picks it up
_ensure_started()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
