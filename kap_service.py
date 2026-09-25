import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from database import get_connection

TR_TZ = ZoneInfo("Europe/Istanbul")

# Public JSON backend used by the KAP web application.
KAP_LIST_API = os.getenv(
    "KAP_LIST_API",
    "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria",
)
KAP_DETAIL_API = os.getenv(
    "KAP_DETAIL_API",
    "https://www.kap.org.tr/tr/api/notification/attachment-detail/{disclosure_index}",
)
KAP_DISCLOSURE_URL = "https://www.kap.org.tr/tr/Bildirim/{disclosure_index}"

HTTP_TIMEOUT = max(4, int(os.getenv("KAP_HTTP_TIMEOUT_SECONDS", "10")))
IGS_INTERVAL = max(15, int(os.getenv("KAP_IGS_POLL_SECONDS", "20")))
DDK_INTERVAL = max(120, int(os.getenv("KAP_DDK_POLL_SECONDS", "300")))
OFF_HOURS_INTERVAL = max(30, int(os.getenv("KAP_OFF_HOURS_POLL_SECONDS", "60")))
MAX_BACKOFF = max(300, int(os.getenv("KAP_MAX_BACKOFF_SECONDS", "900")))
DETAIL_VERIFY_BUDGET = max(1, int(os.getenv("KAP_DETAIL_VERIFY_BUDGET", "10")))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
    "Referer": "https://www.kap.org.tr/tr/bildirim-sorgu",
    "Origin": "https://www.kap.org.tr",
    "Cache-Control": "no-cache",
})

_LAST_ATTEMPT = {
    "KAP_API_IGS": 0.0,
    "KAP_API_DDK": 0.0,
}
_NEXT_ALLOWED = {
    "KAP_API_IGS": 0.0,
    "KAP_API_DDK": 0.0,
}
_FAILURES = {
    "KAP_API_IGS": 0,
    "KAP_API_DDK": 0,
}
_BOOTSTRAPPED = set()

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
    value = str(text or "").lower().replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip()


def kap_score(text):
    normalized = _normalize_text(text)
    return sum(weight for term, weight in KEYWORD_WEIGHTS.items() if term in normalized)


def is_trade_relevant(text):
    normalized = _normalize_text(text)
    return any(term in normalized for term in TRADE_RELEVANT_TERMS)


def _official_kap_url(url):
    try:
        host = (urlparse(str(url)).hostname or "").lower()
        return host == "kap.org.tr" or host.endswith(".kap.org.tr")
    except Exception:
        return False


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


def _extract_symbols_from_codes(codes, allowed):
    if not codes:
        return []

    tokens = set(
        re.findall(
            r"(?<![A-Z0-9])[A-Z0-9]{2,8}(?![A-Z0-9])",
            str(codes).upper(),
        )
    )
    return sorted({allowed[token] for token in tokens if token in allowed})


def _parse_publish_time(value):
    if not value:
        return None

    raw = str(value).strip()
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=TR_TZ)
        except Exception:
            pass

    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TR_TZ)
        return parsed.astimezone(TR_TZ)
    except Exception:
        return None


def init_kap_store():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kap_events (
        event_key TEXT PRIMARY KEY,
        kap_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        company_title TEXT,
        disclosure_class TEXT,
        disclosure_type TEXT,
        disclosure_category TEXT,
        subject TEXT,
        summary TEXT,
        link TEXT NOT NULL,
        event_time TEXT NOT NULL,
        received_at TEXT NOT NULL,
        score REAL NOT NULL DEFAULT 0,
        trade_relevant INTEGER NOT NULL DEFAULT 0,
        verified INTEGER NOT NULL DEFAULT 0,
        detail_verified INTEGER NOT NULL DEFAULT 0,
        discovery_source TEXT,
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


def _source_health(
    source,
    ok,
    status,
    latency_ms,
    entry_count,
    verified_count,
    error=None,
    http_status=None,
):
    now = _now().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT consecutive_failures, last_success FROM kap_source_health WHERE source = ?",
        (source,),
    )
    previous = cur.fetchone()

    failures = (
        0
        if ok
        else (int(previous["consecutive_failures"] or 0) + 1 if previous else 1)
    )
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


def _set_backoff(source, ok):
    now_ts = time.time()
    if ok:
        _FAILURES[source] = 0
        _NEXT_ALLOWED[source] = 0.0
        return

    _FAILURES[source] = int(_FAILURES.get(source, 0)) + 1
    delay = min(MAX_BACKOFF, 30 * (2 ** min(_FAILURES[source] - 1, 5)))
    _NEXT_ALLOWED[source] = now_ts + delay


def _base_interval(member_type):
    if member_type == "DDK":
        return DDK_INTERVAL

    hour = _now().hour
    if 8 <= hour <= 23:
        return IGS_INTERVAL
    return OFF_HOURS_INTERVAL


def _criteria(member_type, day):
    date_str = day.strftime("%Y-%m-%d")
    return {
        "fromDate": date_str,
        "toDate": date_str,
        "memberType": member_type,
        "mkkMemberOidList": [],
        "inactiveMkkMemberOidList": [],
        "disclosureClass": "",
        "subjectList": [],
        "isLate": "",
        "mainSector": "",
        "sector": "",
        "subSector": "",
        "marketOid": "",
        "index": "",
        "bdkReview": "",
        "bdkMemberOidList": [],
        "year": "",
        "term": "",
        "ruleType": "",
        "period": "",
        "fromSrc": False,
        "srcCategory": "",
        "disclosureIndexList": [],
    }


def _response_items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "content", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise RuntimeError("unexpected KAP list API schema")


def _event_from_item(item, allowed, source):
    kap_id = item.get("disclosureIndex")
    if kap_id is None:
        return []

    event_time = _parse_publish_time(item.get("publishDate"))
    if event_time is None:
        return []

    codes = " ".join(
        str(item.get(key) or "")
        for key in ("stockCodes", "relatedStocks", "fundCode")
    )
    symbols = _extract_symbols_from_codes(codes, allowed)

    # Some company disclosures have a missing stockCodes field. As a final
    # deterministic fallback, intersect code-like tokens from visible text
    # against our configured universe.
    if not symbols:
        text_probe = " ".join(
            str(item.get(key) or "")
            for key in ("kapTitle", "subject", "summary", "relatedStocks")
        )
        symbols = _extract_symbols_from_codes(text_probe, allowed)

    if not symbols:
        return []

    subject = str(item.get("subject") or "").strip()
    summary = str(item.get("summary") or "").strip()
    company_title = str(item.get("kapTitle") or "").strip()
    combined = " ".join([company_title, subject, summary, codes]).strip()
    link = KAP_DISCLOSURE_URL.format(disclosure_index=kap_id)

    events = []
    for symbol in symbols:
        events.append({
            "kap_id": str(kap_id),
            "symbol": symbol,
            "company_title": company_title,
            "disclosure_class": str(item.get("disclosureClass") or ""),
            "disclosure_type": str(item.get("disclosureType") or ""),
            "disclosure_category": str(item.get("disclosureCategory") or ""),
            "subject": subject or "KAP Bildirimi",
            "summary": summary,
            "link": link,
            "event_time": event_time,
            "score": kap_score(combined),
            "trade_relevant": is_trade_relevant(combined),
            "discovery_source": source,
            "raw_item": item,
        })

    return events


def fetch_public_api_events(fallback_symbols, member_type="IGS", day=None):
    source = f"KAP_API_{member_type}"
    started = time.monotonic()
    allowed = _allowed_symbol_map(fallback_symbols)
    target_day = day or _now().date()

    try:
        response = SESSION.post(
            KAP_LIST_API,
            json=_criteria(member_type, target_day),
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
            headers={"Content-Type": "application/json"},
        )
        latency = (time.monotonic() - started) * 1000

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        if not _official_kap_url(response.url):
            raise RuntimeError(f"unexpected redirect host: {response.url}")

        items = _response_items(response.json())
        events = []
        verified_items = 0

        for item in items:
            if not isinstance(item, dict) or item.get("disclosureIndex") is None:
                continue
            verified_items += 1
            events.extend(_event_from_item(item, allowed, source))

        _source_health(
            source,
            ok=True,
            status="healthy",
            latency_ms=latency,
            entry_count=len(items),
            verified_count=verified_items,
            http_status=response.status_code,
        )
        _set_backoff(source, True)
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
        _set_backoff(source, False)
        return []


def _upsert_event(event):
    event_key = f"{event['kap_id']}:{event['symbol']}"
    now = _now().isoformat()

    raw_hash = hashlib.sha256(
        json.dumps(
            event.get("raw_item") or {
                "kap_id": event["kap_id"],
                "symbol": event["symbol"],
                "subject": event.get("subject"),
                "summary": event.get("summary"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT event_key, detail_verified FROM kap_events WHERE event_key = ?",
        (event_key,),
    )
    previous = cur.fetchone()
    existed = previous is not None

    cur.execute("""
    INSERT INTO kap_events (
        event_key, kap_id, symbol, company_title, disclosure_class,
        disclosure_type, disclosure_category, subject, summary, link,
        event_time, received_at, score, trade_relevant, verified,
        detail_verified, discovery_source, source_last, raw_hash
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
    ON CONFLICT(event_key) DO UPDATE SET
        company_title = excluded.company_title,
        disclosure_class = excluded.disclosure_class,
        disclosure_type = excluded.disclosure_type,
        disclosure_category = excluded.disclosure_category,
        subject = excluded.subject,
        summary = excluded.summary,
        link = excluded.link,
        event_time = excluded.event_time,
        score = excluded.score,
        trade_relevant = excluded.trade_relevant,
        verified = 1,
        source_last = excluded.source_last,
        raw_hash = excluded.raw_hash
    """, (
        event_key,
        event["kap_id"],
        event["symbol"],
        event.get("company_title"),
        event.get("disclosure_class"),
        event.get("disclosure_type"),
        event.get("disclosure_category"),
        event.get("subject") or "KAP Bildirimi",
        event.get("summary"),
        event["link"],
        event["event_time"].isoformat(),
        now,
        float(event.get("score") or 0),
        1 if event.get("trade_relevant") else 0,
        event.get("discovery_source"),
        event.get("discovery_source"),
        raw_hash,
    ))

    conn.commit()
    conn.close()
    return not existed, event_key


def _extract_detail_basic(payload):
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        return None

    disclosure = payload.get("disclosure")
    if isinstance(disclosure, dict):
        basic = disclosure.get("disclosureBasic")
        if isinstance(basic, dict):
            return basic

    for key in ("disclosureBasic", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    return None


def verify_detail(event):
    """
    Second-level verification for a newly discovered event.

    Primary truth is the official list API. Detail API/HTML is a reconciliation
    layer and never creates a disclosure on its own.
    """
    kap_id = event["kap_id"]
    symbol_clean = _clean_symbol(event["symbol"])
    detail_url = KAP_DETAIL_API.format(disclosure_index=kap_id)

    started = time.monotonic()
    try:
        response = SESSION.get(
            detail_url,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
            headers={"Referer": event["link"]},
        )
        latency = (time.monotonic() - started) * 1000

        if response.status_code == 200 and _official_kap_url(response.url):
            basic = _extract_detail_basic(response.json())
            if basic:
                detail_index = basic.get("disclosureIndex")
                codes = " ".join(
                    str(basic.get(key) or "")
                    for key in ("stockCode", "stockCodes", "relatedStocks")
                )
                id_matches = str(detail_index) == str(kap_id)
                symbol_matches = (
                    not codes
                    or symbol_clean in set(
                        re.findall(r"[A-Z0-9]{2,8}", codes.upper())
                    )
                )
                if id_matches and symbol_matches:
                    _source_health(
                        "KAP_DETAIL_API",
                        ok=True,
                        status="healthy",
                        latency_ms=latency,
                        entry_count=1,
                        verified_count=1,
                        http_status=response.status_code,
                    )
                    return True
    except Exception as exc:
        api_error = exc
    else:
        api_error = RuntimeError("detail API verification mismatch")

    _source_health(
        "KAP_DETAIL_API",
        ok=False,
        status="error",
        latency_ms=(time.monotonic() - started) * 1000,
        entry_count=0,
        verified_count=0,
        error=api_error,
    )

    # Last-resort verification: official disclosure HTML page.
    started = time.monotonic()
    try:
        response = SESSION.get(
            event["link"],
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        latency = (time.monotonic() - started) * 1000

        if response.status_code != 200 or not _official_kap_url(response.url):
            raise RuntimeError(f"HTTP {response.status_code}")

        text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        upper = text.upper()

        if symbol_clean not in upper:
            raise RuntimeError("symbol not found on official disclosure page")

        _source_health(
            "KAP_HTML_DETAIL",
            ok=True,
            status="healthy",
            latency_ms=latency,
            entry_count=1,
            verified_count=1,
            http_status=response.status_code,
        )
        return True

    except Exception as exc:
        _source_health(
            "KAP_HTML_DETAIL",
            ok=False,
            status="error",
            latency_ms=(time.monotonic() - started) * 1000,
            entry_count=0,
            verified_count=0,
            error=exc,
        )
        return False


def _mark_detail_verified(event_key):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE kap_events SET detail_verified = 1 WHERE event_key = ?",
        (event_key,),
    )
    conn.commit()
    conn.close()


def _should_poll(source, interval, force):
    now_ts = time.time()
    if force:
        return True
    if now_ts < _NEXT_ALLOWED.get(source, 0.0):
        return False
    return now_ts - _LAST_ATTEMPT.get(source, 0.0) >= interval


def poll_kap(fallback_symbols, force=False):
    """
    Discover disclosures from KAP's official public JSON backend.

    IGS = listed-company disclosures (primary, ~60s in active hours)
    DDK = regulatory/market notices (lower frequency, used by restrictions too)

    A first process-start poll also queries yesterday separately so an overnight
    server restart does not lose after-close disclosures.
    """
    init_kap_store()
    candidates = []

    for member_type in ("IGS", "DDK"):
        source = f"KAP_API_{member_type}"
        interval = _base_interval(member_type)

        if not _should_poll(source, interval, force):
            continue

        _LAST_ATTEMPT[source] = time.time()
        today = _now().date()
        candidates.extend(fetch_public_api_events(
            fallback_symbols,
            member_type=member_type,
            day=today,
        ))

        if source not in _BOOTSTRAPPED:
            yesterday = today - timedelta(days=1)
            candidates.extend(fetch_public_api_events(
                fallback_symbols,
                member_type=member_type,
                day=yesterday,
            ))
            _BOOTSTRAPPED.add(source)

    new_trade_events = []
    detail_budget = DETAIL_VERIFY_BUDGET
    seen = set()

    # Newest first so detail budget is spent on freshest events.
    candidates.sort(key=lambda e: e["event_time"], reverse=True)

    for event in candidates:
        key = f"{event['kap_id']}:{event['symbol']}"
        if key in seen:
            continue
        seen.add(key)

        was_new, event_key = _upsert_event(event)
        if not was_new:
            continue

        try:
            age_minutes = (_now() - event["event_time"]).total_seconds() / 60.0
        except Exception:
            continue

        # Verify fresh/overnight trade-relevant events through a second official
        # endpoint where budget allows. Primary-list verification remains valid.
        if (
            event.get("trade_relevant")
            and -2 <= age_minutes <= 1080
            and detail_budget > 0
        ):
            detail_budget -= 1
            if verify_detail(event):
                _mark_detail_verified(event_key)
                event["detail_verified"] = True
            else:
                event["detail_verified"] = False

        # Trade engines only receive genuinely fresh events that were
        # independently verified through KAP detail API or the official
        # disclosure HTML page. A list-only event stays in the audit store.
        if (
            event.get("trade_relevant")
            and event.get("detail_verified")
            and -2 <= age_minutes <= 30
        ):
            new_trade_events.append(event)

    return new_trade_events


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
        "SELECT COUNT(*) AS c FROM kap_events WHERE verified = 1 AND event_time >= ?",
        (since,),
    )
    verified_24h = int(cur.fetchone()["c"])

    cur.execute("""
    SELECT kap_id, symbol, company_title, subject, summary, link, event_time,
           score, trade_relevant, detail_verified, discovery_source
    FROM kap_events
    WHERE verified = 1
    ORDER BY event_time DESC
    LIMIT 20
    """)
    recent = [dict(row) for row in cur.fetchall()]

    conn.close()

    igs = next((s for s in sources if s.get("source") == "KAP_API_IGS"), None)
    ddk = next((s for s in sources if s.get("source") == "KAP_API_DDK"), None)

    overall = "down"
    if igs and igs.get("status") == "healthy":
        overall = "healthy"
        if ddk and ddk.get("status") == "error":
            overall = "degraded"
    elif igs and igs.get("last_success"):
        try:
            last_success = datetime.fromisoformat(igs["last_success"])
            if last_success.tzinfo is None:
                last_success = last_success.replace(tzinfo=TR_TZ)
            if (_now() - last_success).total_seconds() <= 600:
                overall = "degraded"
        except Exception:
            pass

    return {
        "overall": overall,
        "verified_events_24h": verified_24h,
        "sources": sources,
        "recent": recent,
    }


def get_recent_verified_events(hours=72, contains=None, limit=100):
    init_kap_store()
    cutoff = (_now() - timedelta(hours=hours)).isoformat()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT kap_id, symbol, company_title, subject, summary, link, event_time,
           score, trade_relevant, detail_verified, discovery_source
    FROM kap_events
    WHERE verified = 1
      AND event_time >= ?
    ORDER BY event_time DESC
    LIMIT ?
    """, (cutoff, int(limit)))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    if contains:
        needle = _normalize_text(contains)
        rows = [
            row
            for row in rows
            if needle in _normalize_text(
                f"{row.get('subject', '')} {row.get('summary', '')} "
                f"{row.get('company_title', '')}"
            )
        ]

    return rows


def recent_kap_cache(minutes=30):
    init_kap_store()
    cutoff = (_now() - timedelta(minutes=minutes)).isoformat()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT kap_id, symbol, company_title, subject, summary, link, event_time,
           score, detail_verified, discovery_source
    FROM kap_events
    WHERE verified = 1
      AND detail_verified = 1
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
            "title": d["subject"] or d["company_title"] or "KAP Bildirimi",
            "summary": d["summary"],
            "link": d["link"],
            "time": event_time.replace(tzinfo=None),
            "published_at": event_time.isoformat(),
            "score": d["score"],
            "verified": True,
            "detail_verified": bool(d["detail_verified"]),
            "source": d["discovery_source"],
        }

    conn.close()
    return result
