"""
news.py — RSS feed fetching, keyword scoring, classification, sentiment
detection, and AI summarization for the energy news tracker.
"""

import re
import os
import html
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import feedparser
import requests

log = logging.getLogger("energy-tracker.news")

# ---------------------------------------------------------------------------
# RSS Feed Registry — organised by tier
#   Tier 1: High-signal, energy-focused (always alert)
#   Tier 2: Contextual / general economy (alert if score is high)
#   Tier 3: Optional / blogs (store but only alert if very relevant)
# ---------------------------------------------------------------------------

RSS_FEEDS: list[dict] = [
    # ── 🇳🇱 NATIONAAL — Tier 1 ──
    {"name": "Solar Magazine NL", "url": "https://solarmagazine.nl/rss", "tier": 1, "region": "NL"},
    {"name": "Energeia", "url": "https://energeia.nl/rss", "tier": 1, "region": "NL"},

    # ── 🇳🇱 NATIONAAL — Tier 2 ──
    {"name": "NOS Economie", "url": "https://feeds.nos.nl/nosnieuwseconomie", "tier": 2, "region": "NL"},
    {"name": "NU.nl Economie", "url": "https://www.nu.nl/rss/Economie", "tier": 2, "region": "NL"},
    {"name": "Rijksoverheid Nieuws", "url": "https://feeds.rijksoverheid.nl/nieuws.rss", "tier": 2, "region": "NL"},

    # ── 🌍 INTERNATIONAAL — Tier 1 ──
    {"name": "PV Magazine", "url": "https://www.pv-magazine.com/feed/", "tier": 1, "region": "INT"},
    {"name": "Energy Storage News", "url": "https://www.energy-storage.news/feed/", "tier": 1, "region": "INT"},
    {"name": "CleanTechnica", "url": "https://cleantechnica.com/feed/", "tier": 1, "region": "INT"},
    {"name": "PV Tech", "url": "https://www.pv-tech.org/feed/", "tier": 1, "region": "INT"},

    # ── 🌍 INTERNATIONAAL — Tier 2 ──
    {"name": "Solar Power World", "url": "https://www.solarpowerworldonline.com/feed/", "tier": 2, "region": "INT"},
    {"name": "Energy Live News", "url": "https://www.energylivenews.com/feed/", "tier": 2, "region": "INT"},
    {"name": "Utility Dive", "url": "https://www.utilitydive.com/feeds/news/", "tier": 2, "region": "INT"},
    {"name": "Power Technology", "url": "https://www.power-technology.com/feed/", "tier": 2, "region": "INT"},
    {"name": "Electrek", "url": "https://electrek.co/feed/", "tier": 2, "region": "INT"},


    # ── 🌍 INTERNATIONAAL — Tier 3 ──
    {"name": "EIA Today in Energy", "url": "https://www.eia.gov/rss/todayinenergy.xml", "tier": 3, "region": "INT"},
    {"name": "Carbon Brief", "url": "https://www.carbonbrief.org/feed", "tier": 3, "region": "INT"},
]

# ---------------------------------------------------------------------------
# Keywords & weights
# ---------------------------------------------------------------------------

KEYWORDS: dict[str, int] = {
    # Dutch — energie & installatiebranche
    "zonnepanelen": 3,
    "netcongestie": 3,
    "dynamisch tarief": 3,
    "dynamische tarieven": 3,
    "onbalansmarkt": 3,
    "onbalanshandel": 3,
    "salderingsregeling": 3,
    "teruglevering": 2,
    "energiebedrijf": 2,
    "energiemaatschappij": 2,
    "energieprijs": 2,
    "energierekening": 2,
    "warmtepomp": 2,
    "installateur": 2,
    "installatiebranche": 3,
    "verduurzam": 2,
    "thuisbatterij": 3,
    "batterij": 2,
    "netbeheer": 2,
    "tennet": 2,
    "distributeur": 2,
    "zonnepaneel": 2,
    # English / universal
    "battery storage": 3,
    "energy management system": 3,
    "energy management": 2,
    "solar": 2,
    "battery": 2,
    "storage": 1,
    "ems": 2,
    "pv": 1,
    "grid congestion": 3,
    "energy price": 2,
    "energy bill": 2,
    "heat pump": 2,
    "inverter": 1,
    "renewable": 1,
    "net metering": 2,
    "geopolitics": 2,
    "geopolitical": 2,
    "tariff": 1,
    "imbalance market": 3,
    "smart grid": 2,
    "flexibility": 2,
    "energy storage": 3,
    "rooftop solar": 2,
    "home battery": 3,
}

MIN_RELEVANCE_SCORE = 2

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

NL_INDICATORS = {
    "nederland", "dutch", "nederlandse", "holland", "rijksoverheid",
    "amsterdam", "rotterdam", "den haag", "utrecht", "nos", "nu.nl",
    "zonnepanelen", "netcongestie", "energieprijs", "dynamisch tarief",
    "onbalansmarkt", "onbalanshandel", "warmtepomp", "installateur",
    "energiebedrijf", "energiemaatschappij", "energierekening",
    "salderingsregeling", "teruglevering", "tennet", "netbeheer",
    "installatiebranche", "verduurzam", "distributeur",
    "belastingdienst", "afm", "acm", "milieu centraal",
}

POSITIVE_WORDS = {
    "groei", "stijging", "subsidie", "investering", "doorbraak", "record",
    "kans", "winst", "growth", "rise", "boost", "opportunity", "innovation",
    "milestone", "breakthrough",
}
NEGATIVE_WORDS = {
    "daling", "crisis", "tekort", "stijgende kosten", "faillissement",
    "storing", "probleem", "verlies", "decline", "shortage", "failure",
    "risk", "cut", "delay", "bankruptcy", "outage",
}

# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------


def _clean_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    text = html.unescape(text)
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_feed(feed_cfg: dict) -> list[dict]:
    """Parse a single RSS feed and return normalised article dicts."""
    url = feed_cfg["url"]
    name = feed_cfg["name"]
    tier = feed_cfg.get("tier", 2)
    region = feed_cfg.get("region", "INT")
    articles: list[dict] = []

    try:
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(15)
        try:
            parsed = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old_timeout)

        if parsed.bozo and not parsed.entries:
            log.warning("Feed '%s' parse error: %s", name, parsed.bozo_exception)
            return []

        for entry in parsed.entries:
            title = _clean_html(entry.get("title", ""))
            link = entry.get("link", "").strip()
            summary = _clean_html(
                entry.get("summary", entry.get("description", ""))
            )
            published = entry.get("published", entry.get("updated", ""))

            if not title or not link:
                continue

            # Parse publication date to ISO string
            published_at = ""
            if published:
                try:
                    # feedparser stores parsed time as published_parsed (time.struct_time UTC)
                    pt = entry.get("published_parsed") or entry.get("updated_parsed")
                    if pt:
                        published_at = datetime(
                            pt.tm_year, pt.tm_mon, pt.tm_mday,
                            pt.tm_hour, pt.tm_min, pt.tm_sec,
                            tzinfo=timezone.utc
                        ).isoformat()
                except Exception:
                    pass

            articles.append({
                "source": name,
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
                "published_at": published_at,
                "tier": tier,
                "region": region,
            })
    except Exception:
        log.exception("Error fetching feed '%s'", name)

    return articles


def fetch_all_feeds() -> list[dict]:
    """Fetch articles from every configured feed."""
    all_articles: list[dict] = []
    for cfg in RSS_FEEDS:
        arts = fetch_feed(cfg)
        if arts:
            log.info("  %s: %d articles", cfg["name"], len(arts))
        all_articles.extend(arts)
    log.info("Total articles fetched: %d", len(all_articles))
    return all_articles


# ---------------------------------------------------------------------------
# Topic classification
# ---------------------------------------------------------------------------

# Topics in priority order — first match wins
TOPICS: list[tuple[str, list[str]]] = [
    ("Zonnepanelen",      ["zonnepanelen", "zonnepaneel", "solar", "pv", "salderingsregeling", "teruglevering", "net metering", "rooftop solar", "plug-in solar"]),
    ("Thuisbatterijen",   ["batterij", "battery storage", "battery", "thuisbatterij", "home battery", "opslag", "energy storage"]),
    ("Netcongestie",      ["netcongestie", "grid congestion", "congestie", "net vol", "netverzwaring", "netbeheer", "tennet"]),
    ("Warmtepompen",      ["warmtepomp", "heat pump"]),
    ("Energieprijzen",    ["energieprijs", "energierekening", "energy price", "energy bill", "dynamisch tarief", "dynamische tarieven", "stroomprijs", "gasprijs", "gas price", "electricity price"]),
    ("Onbalansmarkt",     ["onbalansmarkt", "onbalanshandel", "imbalance market", "flexibility", "smart grid", "vrm"]),
    ("Energiebeheer",     ["ems", "energy management system", "energy management"]),
    ("Installatiebranche",["installateur", "installatiebranche", "distributeur", "verduurzam"]),
    ("Markt & Beleid",    ["subsidie", "beleid", "wet", "regelgeving", "renewable", "wind", "offshore", "geopolit", "tariff", "belastingdienst", "afm"]),
]

TOPIC_LABELS_NL = {t[0]: t[0] for t in TOPICS}


def detect_topic(matched_keywords: list[str], title: str, summary: str) -> str:
    """Assign the most relevant topic based on matched keywords + text."""
    text = f"{title} {summary}".lower()
    for topic, indicators in TOPICS:
        for ind in indicators:
            if ind in text:
                return topic
    return "Algemeen"


# ---------------------------------------------------------------------------
# Scoring & filtering
# ---------------------------------------------------------------------------


def score_article(article: dict) -> tuple[int, list[str]]:
    """Return (weighted_score, matched_keywords) for an article."""
    text = f"{article['title']} {article['summary']}".lower()
    total = 0
    matched: list[str] = []
    for keyword, weight in KEYWORDS.items():
        if keyword in text:
            total += weight
            matched.append(keyword)
    return total, matched


def classify(article: dict) -> str:
    """Return 'NL' or 'INT'."""
    if article.get("region") == "NL":
        return "NL"
    text = f"{article['title']} {article['summary']} {article['source']}".lower()
    for ind in NL_INDICATORS:
        if ind in text:
            return "NL"
    return "INT"


def detect_sentiment(article: dict) -> str:
    """Simple rule-based sentiment: Positief / Negatief / Neutraal."""
    text = f"{article['title']} {article['summary']}".lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    if pos > neg:
        return "📈 Positief"
    if neg > pos:
        return "📉 Negatief"
    return "➡️ Neutraal"


def filter_and_enrich(articles: list[dict]) -> list[dict]:
    """Score, filter, classify and enrich articles. Returns relevant ones."""
    now_utc = datetime.now(timezone.utc)
    hard_cutoff = now_utc - timedelta(hours=36)  # reject articles older than 36 h
    relevant: list[dict] = []
    for article in articles:
        # Skip articles published more than 36 hours ago (prevents stale RSS entries
        # from appearing in today's digest after a service restart).
        pub_at = article.get("published_at", "")
        if pub_at:
            try:
                dt = datetime.fromisoformat(pub_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < hard_cutoff:
                    continue
            except Exception:
                pass  # unparseable date — keep the article

        score, matched = score_article(article)
        if score < MIN_RELEVANCE_SCORE:
            continue
        article["score"] = score
        article["keywords"] = ", ".join(matched)
        article["category"] = classify(article)
        article["sentiment"] = detect_sentiment(article)
        article["topic"] = detect_topic(matched, article["title"], article.get("summary", ""))
        relevant.append(article)

    relevant.sort(key=lambda a: a["score"], reverse=True)
    log.info("Relevant articles after filtering: %d", len(relevant))
    return relevant


# List of all topic names for the UI
ALL_TOPICS = [t[0] for t in TOPICS] + ["Algemeen"]


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------


def generate_digest_intro(articles: list[dict]) -> str:
    """
    Use Gemini Flash via OpenRouter to write a short Dutch executive summary
    of the day's articles, focused on what's relevant for Voltera:
    energy prices, solar panels, home batteries, and heat pumps.
    Falls back to an empty string on failure.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key or not articles:
        return ""

    # Build a compact feed — NL articles first, then INT, capped at 40
    sorted_articles = sorted(articles, key=lambda a: 0 if a.get("category") == "NL" else 1)
    lines: list[str] = []
    for a in sorted_articles[:40]:  # cap at 40 to stay within context
        title   = a.get("title", "")
        summary = (a.get("summary") or "")[:200].replace("\n", " ")
        cat     = "NL" if a.get("category") == "NL" else "INT"
        lines.append(f"[{cat}] {title} — {summary}")
    feed = "\n".join(lines)

    prompt = (
        "Je bent een commercieel strateeg voor Voltera, een Nederlands installatiebedrijf "
        "dat zonnepanelen, thuisbatterijen en warmtepompen verkoopt en installeert bij "
        "particulieren en MKB. Jouw enige doel: bepalen welk nieuws van vandaag de "
        "verkoop van Voltera's producten beïnvloedt.\n\n"
        "Schrijf een BEKNOPTE Nederlandse samenvatting van EXACT 4 alinea's. "
        "Elke alinea is maximaal 2-3 zinnen. Totaal maximaal 150 woorden.\n\n"
        "Alinea 1 — VERKOOPKANSEN: Welk nieuws creëert directe vraag naar zonnepanelen, "
        "thuisbatterijen of warmtepompen? (subsidies, hoge energieprijzen, regelgeving)\n\n"
        "Alinea 2 — MARKT & BELEID: Veranderingen in wet- of regelgeving, "
        "salderingsregeling, netcongestie of overheidsbeleid die klanten activeren of remmen.\n\n"
        "Alinea 3 — TECHNOLOGIE: Nieuwe producten, prijsdalingen of innovaties waarmee "
        "Voltera een beter of goedkoper aanbod kan doen.\n\n"
        "Alinea 4 — ACTIESIGNAAL: Één concrete zin — wat moet Voltera's salesteam "
        "VANDAAG doen of benadrukken richting klanten?\n\n"
        "Schrijf zakelijk en direct. Geen opsommingstekens, geen markdown, geen asterisken. "
        "Begin elke alinea met de cursieve koptitel gevolgd door een dubbele punt.\n\n"
        f"Artikelen van de afgelopen 24 uur:\n{feed}"
    )

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://vrssnews.onrender.com",
                "X-Title": "Voltera News Tracker",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.0-flash-lite-001",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 350,
                "temperature": 0.4,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        log.exception("OpenRouter digest intro generation failed")
        return ""


def _summarize_openai(article: dict) -> Optional[str]:
    """Use OpenAI for a structured summary. Returns None on failure."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        prompt = (
            "You are an energy market analyst. Summarize this article in exactly "
            "3 concise bullet points (same language as article). Then add:\n"
            "- 'Impact on energy market:' (1 sentence)\n"
            "- 'Impact on consumers/installers:' (1 sentence)\n\n"
            f"Title: {article['title']}\n"
            f"Content: {article['summary'][:1500]}\n"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        log.exception("OpenAI summarization failed")
        return None


def _summarize_fallback(article: dict) -> str:
    """Simple truncation-based summary."""
    text = article.get("summary", "")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    bullets = sentences[:3] if len(sentences) >= 3 else (sentences or [text[:200]])
    bullets = [b[:140] + ("…" if len(b) > 140 else "") for b in bullets]
    return (
        "\n".join(f"• {b}" for b in bullets)
        + "\n\nImpact on energy market: May affect energy pricing and grid stability."
        + "\nImpact on consumers/installers: May affect energy costs and installation demand."
    )


def summarize(article: dict) -> str:
    """Generate a summary — AI if available, else fallback."""
    result = _summarize_openai(article)
    if result is None:
        result = _summarize_fallback(article)
    return result
