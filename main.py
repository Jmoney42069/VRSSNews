"""
main.py — Entry point for the Energy News Tracker.

Runs a Flask web dashboard and a background worker thread that polls
RSS feeds, filters articles, stores them in SQLite, and sends alerts.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta

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

# Last successful poll timestamp (updated by background worker)
_last_poll_at: datetime | None = None

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
    """JSON endpoint: returns last poll time and article count."""
    count = db.get_article_count()
    return jsonify({
        "last_poll_utc": _last_poll_at.strftime("%Y-%m-%dT%H:%M:%SZ") if _last_poll_at else None,
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
    log.info("─── Poll cycle start ───")

    # 1. Fetch from all feeds
    articles = news.fetch_all_feeds()

    # 2. Filter & enrich (score, classify, sentiment)
    relevant = news.filter_and_enrich(articles)

    # 3. Store new articles & collect those that need alerting
    global _last_poll_at
    # Skip alerts on the very first poll after startup (avoids duplicate mails
    # when the DB is fresh or when both local and Render start simultaneously)
    is_first_poll = _last_poll_at is None
    new_count = 0
    to_alert: list[dict] = []
    for article in relevant:
        was_new = db.insert_article(article)
        if was_new:
            new_count += 1
            if not is_first_poll and article.get("tier", 2) == 1:
                to_alert.append(article)

    log.info("Stored %d new articles (%d already existed)", new_count, len(relevant) - new_count)

    # 4. Send alerts for new articles
    for article in to_alert:
        try:
            summary = news.summarize(article)
            sent = notifier.send_alert(article, summary)
            if sent:
                db.mark_alerted(article["link"])
        except Exception:
            log.exception("Alert failed for: %s", article["title"][:60])

    # 5. Cleanup old data
    db.cleanup_old_articles()

    _last_poll_at = datetime.now(timezone.utc)
    log.info("─── Poll cycle done ───")


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
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    db.init_db()
    _backfill_topics()

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


# Start on import so gunicorn picks it up
_ensure_started()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
