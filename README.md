# ⚡ Energy News Tracker

24/7 service that monitors 25+ energy news sources (Dutch + international), filters by relevance, stores articles in SQLite, sends real-time alerts via Telegram/Email, and displays everything in a web dashboard.

## Architecture

```
main.py        ─ Flask web server + background worker thread
db.py          ─ SQLite database layer (articles table, cleanup)
news.py        ─ RSS fetching, keyword scoring, classification, summarization
notifier.py    ─ Telegram, Email, WhatsApp alert delivery
templates/     ─ Jinja2 HTML dashboard
static/        ─ CSS styling
```

## Features

- **25+ RSS feeds** — Dutch (Solar Magazine, NOS, NU.nl, TenneT, …) + international (PV Magazine, CleanTechnica, …)
- **Tiered feeds** — Tier 1 (high-signal) always alert, Tier 2 contextual, Tier 3 optional
- **Weighted keyword scoring** — configurable keywords with relevance threshold
- **SQLite persistence** — survives restarts, auto-cleans articles older than 7 days
- **Web dashboard** — card-based UI with search, NL/INT filter, mobile-friendly
- **JSON API** — `/api/articles` endpoint
- **AI summaries** — OpenAI-powered (falls back to text truncation)
- **Classification** — 🇳🇱 NL / 🌍 International auto-tagging
- **Sentiment detection** — positive/negative/neutral
- **Rate limiting** — configurable max alerts per hour
- **Multi-channel alerts** — Telegram primary, Email fallback, WhatsApp optional

## Quick Start

```bash
cd "nieuws rss feed tracker"

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux
# → Edit .env with your credentials

# Run
python main.py
# → Dashboard: http://localhost:10000
# → Background worker starts polling immediately
```

## Configuration

Edit `.env`:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Recommended | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Recommended | Your chat/group ID |
| `EMAIL_USER` | No | Gmail address (fallback channel) |
| `EMAIL_PASS` | No | Gmail [App Password](https://myaccount.google.com/apppasswords) |
| `OPENAI_API_KEY` | No | Enables AI summaries (gpt-4o-mini) |
| `POLL_INTERVAL` | No | Seconds between polls (default: 300) |
| `MAX_ALERTS_PER_HOUR` | No | Rate limit (default: 20) |
| `PORT` | No | Web server port (default: 10000) |

## Deploy to Render

1. Push to GitHub
2. Create a **Web Service** on [render.com](https://render.com)
3. Build command: `pip install -r requirements.txt`
4. Start command: `python main.py`
5. Add environment variables in Render dashboard
6. Deploy — dashboard runs on the Render URL, worker polls in background

## Dashboard

- **`/`** — Article dashboard with NL/INT sections, search, filters
- **`/api/articles`** — JSON API (supports `?cat=NL`, `?cat=INT`, `?q=search`)

## Adding Feeds

Add entries to `RSS_FEEDS` in [news.py](news.py):

```python
{"name": "My Source", "url": "https://example.com/rss", "tier": 2, "region": "INT"},
```

## Adding Keywords

Edit `KEYWORDS` in [news.py](news.py):

```python
"my_keyword": 2,  # weight 1-3
```

## Project Structure

```
├── main.py              # Flask app + background worker
├── db.py                # SQLite database layer
├── news.py              # RSS fetching + filtering + scoring
├── notifier.py          # Telegram / Email / WhatsApp alerts
├── templates/
│   └── index.html       # Dashboard template
├── static/
│   └── style.css        # Dashboard styling
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── .env                 # Your config (git-ignored)
├── .gitignore
└── energy_news.db       # Auto-created SQLite database
```
