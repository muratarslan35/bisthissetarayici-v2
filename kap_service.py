import calendar
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup

from database import get_connection

TR_TZ = ZoneInfo("Europe/Istanbul")

KAP_RSS_URL = os.getenv("KAP_RSS_URL", "https://www.kap.org.tr/tr/rss/all")
KAP_HTML_URL = os.getenv(
    "KAP_HTML_URL",
    "https://www.kap.org.tr/tr/bildirim-sorgu-sonuc?cat=6&cmp=Y&slf=ALL&srcbar=Y",
)

# KAP's documented REST dissemination service is a subscriber integration.
# It is therefore opt-in only in this free-data project.
KAP_API_ENABLED = os.getenv("KAP_API_ENABLED", "0") == "1"
KAP_API_URL = os.getenv("KAP_API_URL", "https://www.kap.org.tr/tr/api/disclosures")

RSS_MIN_INTERVAL = max(20, int(os.getenv("KAP_RSS_MIN_INTERVAL_SECONDS", "45")))
HTML_RECONCILE_INTERVAL = max(
    180, int(os.getenv("KAP_HTML_RECONCILE_SECONDS", "300"))
)
HTTP_TIMEOUT = max(4, int(os.getenv("KAP_HTTP_TIMEOUT_SECONDS", "10")))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; BIST-Trade-Engine/3.0; +https://kap.org.tr)",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
})

_LAST_ATTEMPT = {"KAP_RSS": 0.0, "KAP_HTML": 0.0, "KAP_API": 0.0}

KEYWORD_WEIGHTS = {
    "bedelsiz": 30,
    "geri alim": 25,
    "pay geri alim": 25,
    "yeni is iliskisi": 24,
    "sozlesme": 20,
    "ihale": 20,
    "yatirim": 18,
    "tesvik": 16,
    "birlesme": 18,
    "devralma": 18,
    "ortaklik": 14,
    "temettu": 15,
    "kar payi": 15,
    "finansal rapor": 12,
    "faaliyet raporu": 8,
    "kredi derecelendirme": 8,
    "sermaye artirimi": 6,
    "bedelli": -10,
}

TRADE_RELEVANT_TERMS = {
    "bedelsiz",
    "geri alim",
    "pay geri alim",
    "yeni is iliskisi",
    "sozlesme",
    "ihale",
    "yatirim",
    "tesvik",
    "birlesme",
    "devralma",
    "ortaklik",
    "temettu",
    "kar payi",
    "finansal rapor",
    "sermaye artirimi",
    "bedelli",
}


def _now():
    return datetime.now(TR_TZ)


def _normalize_text(text):
    value = str(text or "").lower()
    value = value.replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip()


def kap_score(text):
    normalized = _normalize_text(text)
    score = 0
    for term, weight in KEYWORD_WEIGHTS.items():
        if term in normalized:
            score += weight
    return score


def is_trade_relevant(text):
    normalized = _normalize_text(text)
    return any(term in normalized for term in TRADE_RELEVANT_TERMS)


def _official_kap_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "kap.org.tr" or host.endswith(".kap.org.tr")
    except Exception:
        return False


def _kap_id_from_link(link):
    if not link:
        return None
    match = re.search(r"/(?:tr/|en/)?Bildirim/(\d+)", str(link), re.I)
    return match.group(1) if match else None


def _clean_symbol(value):
    return str(value or "").strip().upper().replace(".IS", "")


def _allowed_symbol_map(fallback_symbols):
    return {
        _clean_symbol(symbol): (
            str(symbol).upper()
            if str(symbol).upper().endswith(".IS")
            else f"{_clean_symbol(symbol)}.IS"
        )
        for symbol in fallback_symbols
        if _clean_symbol(symbol)
    }


def _extract_symbols(text, allowed):
    if not text:
        return []

    tokens = set(re.findall(r"(?<![A-Z0-9])[A-Z0-9]{2,8}(?![A-Z0-9])", str(text).upper()))
    found = [allowed[token] for token in tokens if token in allowed]
    return sorted(set(found))


def _entry_time(entry):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(
                    calendar.timegm(parsed), tz=timezone.utc
                ).astimezone(TR_TZ)
            except Exception:
                pass
    return _now()


def _plain_html(value):
    if not value:
        return ""
    try:
        return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    except Exception:
        return str(value)


def init_kap_store():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kap_events (
        event_key TEXT PRIMARY KEY,
        kap_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        link TEXT NOT NULL,
        event_time TEXT NOT NULL,
        received_at TEXT NOT NULL,
        score REAL NOT NULL DEFAULT 0,
        trade_relevant INTEGER NOT NULL DEFAULT 0,
        verified INTEGER NOT NULL DEFAULT 0,
        rss_seen INTEGER NOT NULL DEFAULT 0,
        html_seen INTEGER NOT NULL DEFAULT 0,
        api_seen INTEGER NOT NULL DEFAULT 0,
        source_first TEXT,
        source_last TEXT,
        raw_hash TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_kap_events_time
    ON kap_events(event_time DESC)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_kap_events_symbol_time
    ON kap_events(symbol, event_time DESC)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kap_source_health (
        source TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        last_attempt TEXT,
        last_success TEXT,
        http_status INTEGER,
        latency_ms REAL,
        entry_count INTEGER NOT NULL DEFAULT 0,
        verified_count INTEGER NOT NULL DEFAULT 0,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        updated_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


def _source_health(source, ok, status, latency_ms, entry_count, verified_count, error=None, http_status=None):
    now = _now().isoformat()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT consecutive_failures, last_success FROM kap_source_health WHERE source = ?",
        (source,),
    )
    previous = cur.fetchone()

    failures = 0 if ok else int(previous["consecutive_failures"] or 0) + 1 if previous else 1
    last_success = now if ok else (previous["last_success"] if previous else None)

    cur.execute("""
    INSERT INTO kap_source_health (
        source, status, last_attempt, last_success, http_status, latency_ms,
        entry_count, verified_count, consecutive_failures, error, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source) DO UPDATE SET
        status = excluded.status,
        last_attempt = excluded.last_attempt,
        last_success = excluded.last_success,
        http_status = excluded.http_status,
        latency_ms = excluded.latency_ms,
        entry_count = excluded.entry_count,
        verified_count = excluded.verified_count,
        consecutive_failures = excluded.consecutive_failures,
        error = excluded.error,
        updated_at = excluded.updated_at
    """, (
        source,
        status,
        now,
        last_success,
        http_status,
        round(float(latency_ms or 0), 1),
        int(entry_count or 0),
        int(verified_count or 0),
        failures,
        str(error)[:500] if error else None,
        now,
    ))

    conn.commit()
    conn.close()


def _upsert_event(event, source):
    event_key = f"{event['kap_id']}:{event['symbol']}"
    now = _now().isoformat()
    source_flags = {
        "rss_seen": 1 if source == "KAP_RSS" else 0,
        "html_seen": 1 if source == "KAP_HTML" else 0,
        "api_seen": 1 if source == "KAP_API" else 0,
    }

    raw_hash = hashlib.sha256(
        json.dumps(
            {
                "kap_id": event["kap_id"],
                "symbol": event["symbol"],
                "title": event["title"],
                "summary": event.get("summary"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT event_key FROM kap_events WHERE event_key = ?", (event_key,))
    existed = cur.fetchone() is not None

    cur.execute("""
    INSERT INTO kap_events (
        event_key, kap_id, symbol, title, summary, link, event_time,
        received_at, score, trade_relevant, verified,
        rss_seen, html_seen, api_seen, source_first, source_last, raw_hash
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_key) DO UPDATE SET
        title = excluded.title,
        summary = CASE
            WHEN excluded.summary IS NOT NULL AND excluded.summary != ''
            THEN excluded.summary ELSE kap_events.summary END,
        link = excluded.link,
        event_time = CASE
            WHEN excluded.event_time < kap_events.event_time
            THEN excluded.event_time ELSE kap_events.event_time END,
        score = excluded.score,
        trade_relevant = MAX(kap_events.trade_relevant, excluded.trade_relevant),
        verified = 1,
        rss_seen = MAX(kap_events.rss_seen, excluded.rss_seen),
        html_seen = MAX(kap_events.html_seen, excluded.html_seen),
        api_seen = MAX(kap_events.api_seen, excluded.api_seen),
        source_last = excluded.source_last,
        raw_hash = excluded.raw_hash
    """, (
        event_key,
        event["kap_id"],
        event["symbol"],
        event["title"],
        event.get("summary"),
        event["link"],
        event["event_time"].isoformat(),
        now,
        float(event.get("score") or 0),
        1 if event.get("trade_relevant") else 0,
        source_flags["rss_seen"],
        source_flags["html_seen"],
        source_flags["api_seen"],
        source,
        source,
        raw_hash,
    ))

    conn.commit()
    conn.close()
    return not existed


def _build_events(kap_id, title, summary, link, event_time, allowed):
    combined = f"{title} {summary or ''}"
    symbols = _extract_symbols(combined, allowed)

    events = []
    for symbol in symbols:
        events.append({
            "kap_id": str(kap_id),
            "symbol": symbol,
            "title": str(title or "").strip() or "KAP Bildirimi",
            "summary": str(summary or "").strip(),
            "link": link,
            "event_time": event_time,
            "score": kap_score(combined),
            "trade_relevant": is_trade_relevant(combined),
        })
    return events


def fetch_rss_events(fallback_symbols):
    source = "KAP_RSS"
    started = time.monotonic()
    allowed = _allowed_symbol_map(fallback_symbols)

    try:
        response = SESSION.get(KAP_RSS_URL, timeout=HTTP_TIMEOUT, allow_redirects=True)
        latency = (time.monotonic() - started) * 1000

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        if not _official_kap_url(response.url):
            raise RuntimeError(f"unexpected redirect host: {response.url}")

        content = response.content
        probe = content[:1000].lower()
        if b"<rss" not in probe and b"<feed" not in probe and b"<?xml" not in probe:
            raise RuntimeError("response is not an RSS/Atom document")

        feed = feedparser.parse(content)
        entries = list(feed.entries or [])
        events = []
        verified_disclosures = 0

        for entry in entries[:150]:
            link = str(entry.get("link") or "").strip()
            kap_id = _kap_id_from_link(link)
            if not kap_id or not _official_kap_url(link):
                continue

            verified_disclosures += 1
            title = _plain_html(entry.get("title"))
            summary = _plain_html(
                entry.get("summary")
                or entry.get("description")
                or entry.get("content")
            )
            row_events = _build_events(
                kap_id=kap_id,
                title=title,
                summary=summary,
                link=link,
                event_time=_entry_time(entry),
                allowed=allowed,
            )
            for event in row_events:
                event["time_verified"] = True
            events.extend(row_events)

        _source_health(
            source,
            ok=True,
            status="healthy",
            latency_ms=latency,
            entry_count=len(entries),
            verified_count=verified_disclosures,
            http_status=response.status_code,
        )
        return events

    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        _source_health(
            source,
            ok=False,
            status="error",
            latency_ms=latency,
            entry_count=0,
            verified_count=0,
            error=exc,
        )
        return []


def _parse_html_event_time(cells):
    if not cells:
        return None

    normalized = " ".join(cells[:5]).strip()
    low = normalized.lower()

    match = re.search(r"(\d{2}[.-]\d{2}[.-]\d{4})\s+(\d{2}:\d{2})", normalized)
    if match:
        raw = f"{match.group(1)} {match.group(2)}".replace("-", ".")
        try:
            return datetime.strptime(raw, "%d.%m.%Y %H:%M").replace(tzinfo=TR_TZ)
        except Exception:
            pass

    tm = re.search(r"(\d{2}:\d{2})", normalized)
    if tm:
        try:
            parsed_time = datetime.strptime(tm.group(1), "%H:%M").time()
            if "bugün" in low or "today" in low:
                return datetime.combine(_now().date(), parsed_time, tzinfo=TR_TZ)
            if "dün" in low or "yesterday" in low:
                return datetime.combine(
                    (_now() - timedelta(days=1)).date(),
                    parsed_time,
                    tzinfo=TR_TZ,
                )
        except Exception:
            pass

    return None

def fetch_html_events(fallback_symbols):
    source = "KAP_HTML"
    started = time.monotonic()
    allowed = _allowed_symbol_map(fallback_symbols)

    try:
        response = SESSION.get(KAP_HTML_URL, timeout=HTTP_TIMEOUT, allow_redirects=True)
        latency = (time.monotonic() - started) * 1000

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        if not _official_kap_url(response.url):
            raise RuntimeError(f"unexpected redirect host: {response.url}")

        html = response.text
        marker = _normalize_text(html[:250000])
        if "bildirim" not in marker:
            raise RuntimeError("official KAP query marker not found")

        soup = BeautifulSoup(html, "html.parser")
        events = []
        seen_ids = set()

        for row in soup.find_all("tr"):
            link_tag = row.find("a", href=re.compile(r"/(?:tr/|en/)?Bildirim/\d+", re.I))
            if not link_tag:
                continue

            href = link_tag.get("href") or ""
            link = requests.compat.urljoin("https://www.kap.org.tr", href)
            kap_id = _kap_id_from_link(link)
            if not kap_id or not _official_kap_url(link):
                continue

            seen_ids.add(kap_id)
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            row_text = " | ".join(cells)
            title = cells[5] if len(cells) > 5 else row_text
            summary = cells[6] if len(cells) > 6 else row_text
            parsed_event_time = _parse_html_event_time(cells)
            event_time = parsed_event_time or _now()

            row_events = _build_events(
                kap_id=kap_id,
                title=title,
                summary=summary + " " + row_text,
                link=link,
                event_time=event_time,
                allowed=allowed,
            )
            for event in row_events:
                event["time_verified"] = parsed_event_time is not None
            events.extend(row_events)

        # Some KAP versions embed links outside a literal <tr>. Count those too
        # for source-health validation even if row extraction cannot map symbols.
        for match in re.finditer(r"/(?:tr/|en/)?Bildirim/(\d+)", html, re.I):
            seen_ids.add(match.group(1))

        _source_health(
            source,
            ok=True,
            status="healthy" if seen_ids else "reachable_no_rows",
            latency_ms=latency,
            entry_count=len(seen_ids),
            verified_count=len(seen_ids),
            http_status=response.status_code,
        )
        return events

    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        _source_health(
            source,
            ok=False,
            status="error",
            latency_ms=latency,
            entry_count=0,
            verified_count=0,
            error=exc,
        )
        return []


def fetch_api_events(fallback_symbols):
    if not KAP_API_ENABLED:
        return []

    source = "KAP_API"
    started = time.monotonic()
    allowed = _allowed_symbol_map(fallback_symbols)

    try:
        response = SESSION.get(KAP_API_URL, timeout=HTTP_TIMEOUT, allow_redirects=True)
        latency = (time.monotonic() - started) * 1000

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        if not _official_kap_url(response.url):
            raise RuntimeError(f"unexpected redirect host: {response.url}")

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("unexpected API schema")

        events = []
        verified_count = 0

        for item in payload[:200]:
            kap_id = item.get("disclosureIndex")
            if kap_id is None:
                continue

            kap_id = str(kap_id)
            verified_count += 1
            title = str(item.get("title") or "")
            summary = str(item.get("summary") or "")
            codes = str(item.get("stockCodes") or "")
            link = f"https://www.kap.org.tr/tr/Bildirim/{kap_id}"

            event_time = _now()
            row_events = _build_events(
                kap_id=kap_id,
                title=title,
                summary=f"{summary} {codes}",
                link=link,
                event_time=event_time,
                allowed=allowed,
            )
            for event in row_events:
                event["time_verified"] = False
            events.extend(row_events)

        _source_health(
            source,
            ok=True,
            status="healthy",
            latency_ms=latency,
            entry_count=len(payload),
            verified_count=verified_count,
            http_status=response.status_code,
        )
        return events

    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        _source_health(
            source,
            ok=False,
            status="error",
            latency_ms=latency,
            entry_count=0,
            verified_count=0,
            error=exc,
        )
        return []


def poll_kap(fallback_symbols, force=False):
    """
    Poll official KAP sources and return only newly discovered, verified,
    trade-relevant events. All verified disclosures are persisted for audit.

    Priority:
      1) KAP official RSS (frequent)
      2) KAP official public disclosure-query HTML (5-minute reconciliation)
      3) subscriber REST API only when explicitly enabled
    """
    init_kap_store()
    now_ts = time.time()
    candidates = []

    if force or now_ts - _LAST_ATTEMPT["KAP_RSS"] >= RSS_MIN_INTERVAL:
        _LAST_ATTEMPT["KAP_RSS"] = now_ts
        candidates.extend(("KAP_RSS", e) for e in fetch_rss_events(fallback_symbols))

    if force or now_ts - _LAST_ATTEMPT["KAP_HTML"] >= HTML_RECONCILE_INTERVAL:
        _LAST_ATTEMPT["KAP_HTML"] = now_ts
        candidates.extend(("KAP_HTML", e) for e in fetch_html_events(fallback_symbols))

    if KAP_API_ENABLED and (force or now_ts - _LAST_ATTEMPT["KAP_API"] >= HTML_RECONCILE_INTERVAL):
        _LAST_ATTEMPT["KAP_API"] = now_ts
        candidates.extend(("KAP_API", e) for e in fetch_api_events(fallback_symbols))

    new_events = []
    seen = set()

    for source, event in candidates:
        key = f"{event['kap_id']}:{event['symbol']}"

        was_new = _upsert_event(event, source)
        if not was_new or key in seen:
            continue

        seen.add(key)
        if not event.get("trade_relevant"):
            continue
        if not event.get("time_verified"):
            continue

        try:
            age_minutes = (_now() - event["event_time"]).total_seconds() / 60.0
        except Exception:
            continue

        if -2 <= age_minutes <= 30:
            new_events.append(event)

    return new_events


def get_kap_health():
    init_kap_store()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    SELECT source, status, last_attempt, last_success, http_status, latency_ms,
           entry_count, verified_count, consecutive_failures, error
    FROM kap_source_health
    ORDER BY source
    """)
    sources = [dict(row) for row in cur.fetchall()]

    since = (_now() - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT COUNT(*) AS c FROM kap_events WHERE verified = 1 AND received_at >= ?",
        (since,),
    )
    verified_24h = int(cur.fetchone()["c"])

    cur.execute("""
    SELECT kap_id, symbol, title, link, event_time, score, trade_relevant,
           rss_seen, html_seen, api_seen
    FROM kap_events
    WHERE verified = 1
    ORDER BY event_time DESC
    LIMIT 20
    """)
    recent = [dict(row) for row in cur.fetchall()]

    conn.close()

    healthy_sources = [
        s for s in sources
        if s.get("status") in ("healthy", "reachable_no_rows")
        and s.get("last_success")
    ]

    overall = "healthy" if healthy_sources else "down"
    if healthy_sources and all(s.get("source") != "KAP_RSS" for s in healthy_sources):
        overall = "degraded"

    return {
        "overall": overall,
        "verified_events_24h": verified_24h,
        "sources": sources,
        "recent": recent,
    }


def recent_kap_cache(minutes=30):
    init_kap_store()
    cutoff = (_now() - timedelta(minutes=minutes)).isoformat()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT kap_id, symbol, title, summary, link, event_time, score
    FROM kap_events
    WHERE verified = 1
      AND trade_relevant = 1
      AND event_time >= ?
    ORDER BY event_time DESC
    """, (cutoff,))

    result = {}
    for row in cur.fetchall():
        d = dict(row)
        try:
            event_time = datetime.fromisoformat(d["event_time"]).astimezone(TR_TZ)
        except Exception:
            event_time = _now()

        symbol = d["symbol"]
        if symbol in result:
            continue

        result[symbol] = {
            "kap_id": d["kap_id"],
            "title": d["title"],
            "summary": d["summary"],
            "link": d["link"],
            # Keep legacy consumers safe: local naive datetime.
            "time": event_time.replace(tzinfo=None),
            "published_at": event_time.isoformat(),
            "score": d["score"],
            "verified": True,
        }

    conn.close()
    return result
