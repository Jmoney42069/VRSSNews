"""
notifier.py — Alert delivery via Telegram, Email, and (optionally) WhatsApp.

Includes rate limiting and message formatting.
"""

import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

log = logging.getLogger("energy-tracker.notifier")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window rate limiter (max N alerts per hour)."""

    def __init__(self, max_per_hour: int = 20):
        self.max_per_hour = max_per_hour
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        if len(self._timestamps) >= self.max_per_hour:
            return False
        self._timestamps.append(now)
        return True


_rate_limiter = RateLimiter(
    max_per_hour=int(os.getenv("MAX_ALERTS_PER_HOUR", "20"))
)

# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


def format_alert(article: dict, summary: str) -> str:
    """Build the alert message for an article."""
    cat = "🇳🇱 NL" if article.get("category") == "NL" else "🌍 International"
    sentiment = article.get("sentiment", "")
    keywords = article.get("keywords", "")
    score = article.get("score", 0)

    return (
        f"⚡ ENERGY ALERT\n"
        f"\n"
        f"{cat}\n"
        f"{sentiment}\n"
        f"\n"
        f"📰 {article['title']}\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"🔑 Keywords: {keywords} (score: {score})\n"
        f"📡 Source: {article.get('source', '')}\n"
        f"👉 {article['link']}\n"
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.debug("Telegram not configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram has a 4096 char limit
    truncated = message[:4000]
    payload = {
        "chat_id": chat_id,
        "text": truncated,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            log.info("Telegram message sent")
            return True
        log.error("Telegram error %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception:
        log.exception("Telegram send failed")
        return False


# ---------------------------------------------------------------------------
# Email (Gmail SMTP)
# ---------------------------------------------------------------------------


def send_email(article: dict, summary: str) -> bool:
    """Send a nicely formatted HTML alert email. Returns True on success."""
    user     = os.getenv("GMAIL_USER", "")
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    to_addr  = os.getenv("EMAIL_TO", user)
    if not user or not password:
        log.debug("Email not configured")
        return False

    cat       = "🇳🇱 Nederland" if article.get("category") == "NL" else "🌍 Internationaal"
    sentiment = article.get("sentiment", "")
    keywords  = article.get("keywords", "")
    score     = article.get("score", 0)
    source    = article.get("source", "")
    title     = article.get("title", "")
    link      = article.get("link", "#")
    pub_time  = article.get("time_ago") or article.get("published_at") or ""

    subject = f"⚡ Voltera Alert — {title[:80]}"

    html_body = f"""\
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#0f0f14; margin:0; padding:0; }}
    .wrap {{ max-width:600px; margin:32px auto; background:#18181f; border-radius:16px; overflow:hidden; border:1px solid rgba(255,255,255,0.07); }}
    .header {{ background:linear-gradient(135deg,#1e1030,#18181f); padding:28px 32px 20px; border-bottom:1px solid rgba(255,255,255,0.07); }}
    .logo {{ font-size:22px; font-weight:800; color:#ededf4; letter-spacing:-0.03em; }}
    .logo span {{ color:#8b5cf6; }}
    .tag {{ display:inline-block; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.1em; padding:3px 10px; border-radius:999px; margin-top:10px; }}
    .tag-priority {{ background:rgba(251,191,36,0.12); color:#fbbf24; border:1px solid rgba(251,191,36,0.25); }}
    .tag-cat {{ background:rgba(139,92,246,0.12); color:#c4b5fd; border:1px solid rgba(139,92,246,0.22); margin-left:6px; }}
    .body {{ padding:28px 32px; }}
    .title {{ font-size:20px; font-weight:700; color:#ededf4; line-height:1.4; margin:0 0 14px; letter-spacing:-0.02em; }}
    .summary {{ font-size:14px; color:#8888a8; line-height:1.7; margin:0 0 22px; }}
    .meta-row {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:22px; }}
    .pill {{ font-size:11px; font-weight:600; padding:3px 10px; border-radius:999px; }}
    .pill-score {{ background:rgba(139,92,246,0.12); color:#c4b5fd; }}
    .pill-sent {{ background:rgba(255,255,255,0.05); color:#8888a8; }}
    .pill-kw {{ background:rgba(255,255,255,0.05); color:#8888a8; }}
    .divider {{ height:1px; background:rgba(255,255,255,0.07); margin:0 0 22px; }}
    .source-row {{ font-size:12px; color:#44445a; margin-bottom:20px; }}
    .source-row strong {{ color:#8888a8; }}
    .cta {{ display:inline-block; background:linear-gradient(135deg,#8b5cf6,#6d28d9); color:#fff !important; text-decoration:none; font-size:14px; font-weight:700; padding:12px 28px; border-radius:999px; letter-spacing:0.01em; }}
    .footer {{ padding:18px 32px; border-top:1px solid rgba(255,255,255,0.07); font-size:11px; color:#44445a; text-align:center; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="logo">Volt<span>era</span> News</div>
      <div>
        <span class="tag tag-priority">HOGE PRIORITEIT</span>
        <span class="tag tag-cat">{cat}</span>
      </div>
    </div>
    <div class="body">
      <h1 class="title">{title}</h1>
      <p class="summary">{summary}</p>
      <div class="meta-row">
        <span class="pill pill-score">Score: {score} pts</span>
        <span class="pill pill-sent">{sentiment}</span>
        {chr(10).join(f'<span class="pill pill-kw">{kw.strip()}</span>' for kw in keywords.split(',') if kw.strip())}
      </div>
      <div class="divider"></div>
      <div class="source-row">Bron: <strong>{source}</strong>{f' &nbsp;·&nbsp; {pub_time}' if pub_time else ''}</div>
      <a href="{link}" class="cta">Artikel lezen →</a>
    </div>
    <div class="footer">Voltera News Tracker &nbsp;·&nbsp; Alleen hoge prioriteit artikelen &nbsp;·&nbsp; Automatisch gegenereerd</div>
  </div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"Voltera News <{user}>"
        msg["To"]      = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(user, password)
            server.send_message(msg)
        log.info("Email sent to %s — %s", to_addr, title[:60])
        return True
    except Exception:
        log.exception("Email send failed")
        return False


# ---------------------------------------------------------------------------
# WhatsApp (Twilio placeholder)
# ---------------------------------------------------------------------------


def send_whatsapp(message: str) -> bool:
    """
    Placeholder for WhatsApp via Twilio.
    Set TWILIO_SID, TWILIO_AUTH, TWILIO_FROM, TWILIO_TO to activate.
    """
    sid = os.getenv("TWILIO_SID", "")
    auth = os.getenv("TWILIO_AUTH", "")
    from_num = os.getenv("TWILIO_FROM", "")
    to_num = os.getenv("TWILIO_TO", "")
    if not all([sid, auth, from_num, to_num]):
        return False

    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        resp = requests.post(
            url,
            data={
                "From": f"whatsapp:{from_num}",
                "To": f"whatsapp:{to_num}",
                "Body": message[:1600],
            },
            auth=(sid, auth),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info("WhatsApp message sent")
            return True
        log.error("Twilio error %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception:
        log.exception("WhatsApp send failed")
        return False


# ---------------------------------------------------------------------------
# Unified send
# ---------------------------------------------------------------------------


def _any_channel_configured() -> bool:
    """Return True if at least one notification channel has credentials set."""
    return bool(
        (os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        or (os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD"))
        or (os.getenv("TWILIO_SID") and os.getenv("TWILIO_AUTH"))
    )


def send_alert(article: dict, summary: str) -> bool:
    """
    Format and send an alert through all configured channels.
    Only sends for Tier 1 (high priority) articles.
    Returns True if at least one channel succeeded.
    Respects rate limiting.
    """
    # Only email high priority (tier 1) articles
    if article.get("tier", 2) != 1:
        log.debug("Skipping alert — not tier 1: %s", article["title"][:60])
        return False

    if not _any_channel_configured():
        log.debug("No notification channels configured — skipping alert for: %s", article["title"][:60])
        return False

    if not _rate_limiter.allow():
        log.warning("Rate limit reached — skipping alert for: %s", article["title"][:60])
        return False

    message = format_alert(article, summary)

    sent = send_telegram(message)

    if not sent:
        log.info("Telegram unavailable — trying email")

    sent_email = send_email(article, summary)
    sent = sent or sent_email

    # WhatsApp attempted independently
    send_whatsapp(message)

    if not sent:
        log.warning("No channel delivered alert — printing to stdout")
        print("=" * 60)
        print(message)
        print("=" * 60)

    return sent
