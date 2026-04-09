"""
notifier.py — Alert delivery via Telegram, Email, and (optionally) WhatsApp.

Includes rate limiting and message formatting.
"""

import os
import re as _re
import time
import html as _html
import smtplib
import logging
from collections import defaultdict
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
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#f0f4f1; margin:0; padding:0; }}
    .outer {{ padding:32px 16px; background:#f0f4f1; }}
    .wrap {{ max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08); }}
    .header {{ background:linear-gradient(135deg,#0d3320 0%,#166534 60%,#15803d 100%); padding:32px 36px 26px; }}
    .logo {{ font-size:24px; font-weight:800; color:#ffffff; letter-spacing:-0.03em; }}
    .logo-dot {{ color:#86efac; }}
    .header-sub {{ font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.13em; color:rgba(255,255,255,0.45); margin-top:2px; }}
    .badges {{ margin-top:18px; display:flex; gap:8px; flex-wrap:wrap; }}
    .badge {{ display:inline-block; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.1em; padding:4px 12px; border-radius:999px; }}
    .badge-priority {{ background:rgba(251,191,36,0.18); color:#fbbf24; border:1px solid rgba(251,191,36,0.35); }}
    .badge-cat {{ background:rgba(134,239,172,0.15); color:#86efac; border:1px solid rgba(134,239,172,0.3); }}
    .body {{ padding:32px 36px 28px; }}
    .label {{ font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.12em; color:#16a34a; margin-bottom:10px; }}
    .title {{ font-size:22px; font-weight:800; color:#0f1f14; line-height:1.35; margin:0 0 16px; letter-spacing:-0.02em; }}
    .summary {{ font-size:14px; color:#374151; line-height:1.75; margin:0 0 24px; border-left:3px solid #22c55e; padding-left:14px; }}
    .divider {{ height:1px; background:#e5e7eb; margin:0 0 20px; }}
    .meta-row {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:24px; }}
    .pill {{ font-size:11px; font-weight:600; padding:4px 11px; border-radius:999px; }}
    .pill-score {{ background:#dcfce7; color:#166534; }}
    .pill-sent {{ background:#f3f4f6; color:#6b7280; }}
    .pill-kw {{ background:#f3f4f6; color:#6b7280; }}
    .source-block {{ background:#f9fafb; border-radius:10px; padding:14px 18px; margin-bottom:26px; display:flex; justify-content:space-between; align-items:center; }}
    .source-label {{ font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.1em; color:#9ca3af; margin-bottom:3px; }}
    .source-name {{ font-size:13px; font-weight:700; color:#111827; }}
    .pub-time {{ font-size:12px; color:#9ca3af; font-weight:500; }}
    .cta-wrap {{ text-align:center; padding-bottom:4px; }}
    .cta {{ display:inline-block; background:linear-gradient(135deg,#16a34a,#15803d); color:#ffffff !important; text-decoration:none; font-size:14px; font-weight:700; padding:14px 36px; border-radius:999px; letter-spacing:0.01em; box-shadow:0 4px 14px rgba(22,163,74,0.35); }}
    .footer {{ background:#f9fafb; border-top:1px solid #e5e7eb; padding:18px 36px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }}
    .footer-brand {{ font-size:12px; font-weight:700; color:#16a34a; }}
    .footer-text {{ font-size:11px; color:#9ca3af; }}
  </style>
</head>
<body>
  <div class="outer">
  <div class="wrap">
    <div class="header">
      <div class="logo">Volt<span class="logo-dot">era</span></div>
      <div class="header-sub">Energie Nieuws Tracker</div>
      <div class="badges">
        <span class="badge badge-priority">⚡ Hoge Prioriteit</span>
        <span class="badge badge-cat">{cat}</span>
      </div>
    </div>
    <div class="body">
      <div class="label">Nieuw energie-artikel</div>
      <h1 class="title">{title}</h1>
      <p class="summary">{summary}</p>
      <div class="meta-row">
        <span class="pill pill-score">Score: {score} pts</span>
        <span class="pill pill-sent">{sentiment}</span>
        {chr(10).join(f'<span class="pill pill-kw">{kw.strip()}</span>' for kw in keywords.split(',') if kw.strip())}
      </div>
      <div class="divider"></div>
      <div class="source-block">
        <div>
          <div class="source-label">Bron</div>
          <div class="source-name">{source}</div>
        </div>
        {f'<div class="pub-time">{pub_time}</div>' if pub_time else ''}
      </div>
      <div class="cta-wrap">
        <a href="{link}" class="cta">Artikel lezen &rarr;</a>
      </div>
    </div>
    <div class="footer">
      <span class="footer-brand">Voltera News Tracker</span>
      <span class="footer-text">Alleen hoge prioriteit &nbsp;&middot;&nbsp; Automatisch gegenereerd</span>
    </div>
  </div>
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
    # Guard: only send when explicitly enabled (prevents local dev from sending)
    if os.getenv("ALERTS_ENABLED", "").lower() != "true":
        log.debug("Alerts disabled (ALERTS_ENABLED != true) — skipping: %s", article["title"][:60])
        return False

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


# ---------------------------------------------------------------------------
# Daily digest email
# ---------------------------------------------------------------------------

_TOPIC_ICONS: dict[str, str] = {
    "Zonnepanelen":       "🌞",
    "Thuisbatterijen":    "🔋",
    "Netcongestie":       "⚡",
    "Warmtepompen":       "♨️",
    "Energieprijzen":     "📈",
    "Onbalansmarkt":      "⚖️",
    "Energiebeheer":      "🖥️",
    "Installatiebranche": "🔧",
    "Markt & Beleid":     "📋",
    "Algemeen":           "📰",
}


def _build_digest_section(grouped: "dict[str, list[dict]]", flag: str, label: str, total: int) -> str:
    """Render one NL or INT section as HTML."""
    if not total:
        return ""

    topic_blocks = ""
    for topic in sorted(grouped, key=lambda t: -len(grouped[t])):
        arts = grouped[topic]
        icon = _TOPIC_ICONS.get(topic, "📌")
        items = ""
        for a in arts:
            title   = _html.escape(a.get("title", ""))
            link    = _html.escape(a.get("link", "#"), quote=True)
            source  = _html.escape(a.get("source", ""))
            summary = _html.escape((a.get("summary") or "")[:180])
            if len(a.get("summary") or "") > 180:
                summary += "…"
            items += f"""\
              <li style="padding:10px 0 10px 14px;border-left:3px solid #d1fae5;margin-bottom:6px;list-style:none;">
                <a href="{link}" style="font-size:14px;font-weight:700;color:#0f1f14;text-decoration:none;line-height:1.45;">{title}</a>
                <div style="font-size:11px;color:#9ca3af;margin-top:3px;">
                  <span style="font-weight:600;color:#6b7280;">[{source}]</span>
                </div>
                {"" if not summary else f'<p style="font-size:12px;color:#6b7280;line-height:1.6;margin:5px 0 4px;">{summary}</p>'}
                <a href="{link}" style="font-size:11px;font-weight:700;color:#16a34a;text-decoration:none;">Lees meer &rarr;</a>
              </li>"""

        topic_blocks += f"""\
        <div style="margin-bottom:22px;">
          <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.09em;color:#16a34a;margin-bottom:10px;">{_html.escape(icon + " " + topic)} <span style="font-weight:500;color:#9ca3af;">({len(arts)})</span></div>
          <ul style="padding:0;margin:0;">
            {items}
          </ul>
        </div>"""

    return f"""\
      <div style="margin-bottom:36px;">
        <div style="display:flex;align-items:center;gap:10px;padding-bottom:12px;border-bottom:2px solid #16a34a;margin-bottom:20px;">
          <span style="font-size:20px;">{flag}</span>
          <span style="font-size:18px;font-weight:800;color:#0f1f14;">{label}</span>
          <span style="font-size:12px;font-weight:600;background:#dcfce7;color:#166534;padding:3px 10px;border-radius:999px;margin-left:auto;">{total} artikelen</span>
        </div>
        {topic_blocks}
      </div>"""


def send_digest_email(articles: list[dict], period_label: str, intro: str = "") -> bool:
    """Build and send the daily HTML digest email. Returns True on success."""
    user     = os.getenv("GMAIL_USER", "")
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    # Support comma-separated list of recipients in EMAIL_TO
    to_raw   = os.getenv("EMAIL_TO", user)
    to_list  = [addr.strip() for addr in to_raw.split(",") if addr.strip()]
    if not user or not password:
        log.debug("Email not configured — digest not sent")
        return False
    if not to_list:
        log.warning("EMAIL_TO is empty — digest not sent")
        return False

    if not articles:
        no_articles_msg = "<p style='font-size:14px;color:#6b7280;'>Er zijn vandaag geen relevante energie-artikelen gevonden.</p>"
        log.info("Digest: geen artikelen in venster — stuur lege digest")
    else:
        no_articles_msg = ""

    # Group by category → topic
    nl:   "dict[str, list[dict]]" = defaultdict(list)
    intl: "dict[str, list[dict]]" = defaultdict(list)
    for a in articles:
        topic = a.get("topic") or "Algemeen"
        if a.get("category") == "NL":
            nl[topic].append(a)
        else:
            intl[topic].append(a)

    nl_total  = sum(len(v) for v in nl.values())
    int_total = sum(len(v) for v in intl.values())
    total     = nl_total + int_total

    nl_html   = _build_digest_section(nl,   "🇳🇱", "Nederland",     nl_total)
    int_html  = _build_digest_section(intl, "🌍", "Internationaal", int_total)
    if nl_html or int_html:
        no_articles_msg = ""

    intro_block = ""
    if intro:
        # Escape HTML first, then convert **bold** to <strong> (order matters!)
        clean = _html.escape(intro, quote=False)
        clean = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", clean)
        # Strip any leftover * or # characters
        clean = clean.replace("*", "").replace("#", "")
        # Split into paragraphs on blank lines
        paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
        p_style = "font-size:14px;color:#1a3a24;line-height:1.8;margin:0 0 10px;"
        intro_html = "".join(f'<p style="{p_style}">{p.replace(chr(10), "<br>")}</p>' for p in paragraphs)

        # Build sources list — NL first, then INT, top 15 by score
        source_articles = sorted(articles, key=lambda a: (0 if a.get("category") == "NL" else 1, -a.get("score", 0)))[:15]
        source_items = ""
        for i, a in enumerate(source_articles, 1):
            t    = _html.escape(a.get("title", ""))
            href = _html.escape(a.get("link", "#"), quote=True)
            src  = _html.escape(a.get("source", ""))
            flag = "🇳🇱" if a.get("category") == "NL" else "🌍"
            source_items += (
                f'<div style="padding:6px 0;border-bottom:1px solid #d1fae5;">'
                f'<span style="font-size:11px;color:#9ca3af;margin-right:6px;">{i}.</span>'
                f'<a href="{href}" style="font-size:13px;font-weight:600;color:#166534;text-decoration:none;">{t}</a>'
                f'<span style="font-size:11px;color:#9ca3af;margin-left:8px;">{flag} {src}</span>'
                f'</div>'
            )

        intro_block = f"""\
      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:20px 24px;margin-bottom:28px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#16a34a;margin-bottom:12px;">🤖 AI Samenvatting — Relevant voor Voltera</div>
        {intro_html}
        <div style="margin-top:16px;padding-top:14px;border-top:1px solid #bbf7d0;">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#16a34a;margin-bottom:8px;">📎 Bronnen gebruikt voor deze samenvatting</div>
          {source_items}
        </div>
      </div>"""

    subject = f"📋 Dagelijkse Energie Digest — {period_label}"

    html_body = f"""\
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f1;margin:0;padding:0;">
  <div style="padding:32px 16px;background:#f0f4f1;">
  <div style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

    <!-- HEADER -->
    <div style="background:linear-gradient(135deg,#0d3320 0%,#166534 60%,#15803d 100%);padding:32px 36px 28px;">
      <div style="font-size:24px;font-weight:800;color:#fff;letter-spacing:-0.03em;">Volt<span style="color:#86efac;">era</span></div>
      <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.13em;color:rgba(255,255,255,0.45);margin-top:2px;">Energie Nieuws Tracker</div>
      <div style="font-size:20px;font-weight:700;color:#fff;margin-top:18px;">📋 Dagelijkse Digest</div>
      <div style="font-size:12px;color:rgba(255,255,255,0.55);margin-top:6px;">{_html.escape(period_label)}</div>
      <div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap;">
        <span style="background:rgba(255,255,255,0.12);color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,0.2);">📰 {total} artikelen</span>
        <span style="background:rgba(255,255,255,0.12);color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,0.2);">🇳🇱 {nl_total} Nederland</span>
        <span style="background:rgba(255,255,255,0.12);color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,0.2);">🌍 {int_total} Internationaal</span>
      </div>
    </div>

    <!-- BODY -->
    <div style="padding:32px 36px;">
      {intro_block}
      {nl_html}
      {int_html}
      {no_articles_msg}
    </div>

    <!-- FOOTER -->
    <div style="background:#f9fafb;border-top:1px solid #e5e7eb;padding:18px 36px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
      <span style="font-size:12px;font-weight:700;color:#16a34a;">Voltera News Tracker</span>
      <span style="font-size:11px;color:#9ca3af;">Dagelijkse digest &nbsp;&middot;&nbsp; Automatisch gegenereerd</span>
    </div>

  </div>
  </div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"Voltera News <{user}>"
        msg["To"]      = ", ".join(to_list)
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(user, password)
            server.sendmail(user, to_list, msg.as_string())
        log.info("Digest email sent to %s — %d artikelen", ", ".join(to_list), total)
        return True
    except Exception:
        log.exception("Digest email send failed")
        return False
