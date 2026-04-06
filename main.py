"""
main.py — Entry point for the Energy News Tracker.

Runs a Flask web dashboard and a background worker thread that polls
RSS feeds, filters articles, stores them in SQLite, and sends alerts.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify
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

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _time_ago(iso_str: str) -> str:
    """Convert an ISO timestamp to a human-readable 'time ago' string."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = seconds // 60
            return f"{m}m ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h}h ago"
        d = seconds // 86400
        return f"{d}d ago"
    except Exception:
        return ""


@app.route("/")
def index():
    """Dashboard — shows all articles from the last 7 days."""
    search = request.args.get("q", "").strip()
    cat_filter = request.args.get("cat", "").strip().upper()

    category = cat_filter if cat_filter in ("NL", "INT") else None
    articles = db.get_recent_articles(category=category, search=search or None)

    # Split into NL / INT
    nl_articles = [a for a in articles if a["category"] == "NL"]
    int_articles = [a for a in articles if a["category"] == "INT"]

    # Add time-ago to each
    for a in articles:
        a["time_ago"] = _time_ago(a["created_at"])

    counts = db.get_article_count()

    return render_template(
        "index.html",
        nl_articles=nl_articles,
        int_articles=int_articles,
        counts=counts,
        search=search,
        cat_filter=cat_filter,
    )


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
        a["time_ago"] = _time_ago(a["created_at"])
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
    new_count = 0
    to_alert: list[dict] = []
    for article in relevant:
        was_new = db.insert_article(article)
        if was_new:
            new_count += 1
            # Only alert for tier 1 & 2 articles
            if article.get("tier", 2) <= 2:
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


def _ensure_started():
    """Initialise DB and start background worker (once)."""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    db.init_db()

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
