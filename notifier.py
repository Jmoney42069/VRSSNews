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


def send_email(message: str, subject: str = "⚡ Energy Alert") -> bool:
    """Send an alert email. Returns True on success."""
    user = os.getenv("EMAIL_USER", "")
    password = os.getenv("EMAIL_PASS", "")
    to_addr = os.getenv("EMAIL_TO", user)
    if not user or not password:
        log.debug("Email not configured")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(user, password)
            server.send_message(msg)
        log.info("Email sent to %s", to_addr)
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
        or (os.getenv("EMAIL_USER") and os.getenv("EMAIL_PASS"))
        or (os.getenv("TWILIO_SID") and os.getenv("TWILIO_AUTH"))
    )


def send_alert(article: dict, summary: str) -> bool:
    """
    Format and send an alert through all configured channels.
    Returns True if at least one channel succeeded.
    Respects rate limiting.
    """
    if not _any_channel_configured():
        log.debug("No notification channels configured — skipping alert for: %s", article["title"][:60])
        return False

    if not _rate_limiter.allow():
        log.warning("Rate limit reached — skipping alert for: %s", article["title"][:60])
        return False

    message = format_alert(article, summary)

    sent = send_telegram(message)

    if not sent:
        log.info("Telegram unavailable — trying email fallback")
        sent = send_email(message)

    # WhatsApp attempted independently
    send_whatsapp(message)

    if not sent:
        log.warning("No channel delivered alert — printing to stdout")
        print("=" * 60)
        print(message)
        print("=" * 60)

    return sent
