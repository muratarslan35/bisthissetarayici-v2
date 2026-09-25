import math
import os
import threading
import time
from collections import defaultdict, deque

import requests

TRADINGVIEW_SCAN_URL = os.getenv(
    "TRADINGVIEW_SCAN_URL",
    "https://scanner.tradingview.com/turkey/scan",
)

FAST_POLL_SECONDS = max(10, int(os.getenv("FAST_POLL_SECONDS", "20")))
FAST_QUOTE_MAX_AGE_SECONDS = max(
    20,
    int(os.getenv("FAST_QUOTE_MAX_AGE_SECONDS", "45")),
)
FAST_BATCH_SIZE = max(20, int(os.getenv("FAST_QUOTE_BATCH_SIZE", "80")))

_LOCK = threading.RLock()
_HISTORY = defaultdict(lambda: deque(maxlen=24))
_FAILURES = 0
_NEXT_ALLOWED = 0.0


def _num(value, default=None):
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return default


def _chunks(values, size):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _clean_symbol(value):
    raw = str(value or "").upper().strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    raw = raw.replace(".IS", "")
    if not raw:
        return None
    return raw + ".IS"


def _post_batch(symbols):
    payload = {
        "markets": ["turkey"],
        "symbols": {
            "tickers": [
                "BIST:" + str(symbol).upper().replace(".IS", "")
                for symbol in symbols
            ],
            "query": {"types": []},
        },
        "columns": [
            "name",
            "close",
            "volume",
            "relative_volume_10d_calc",
            "change",
        ],
        "range": [0, len(symbols)],
    }
    response = requests.post(TRADINGVIEW_SCAN_URL, json=payload, timeout=7)
    response.raise_for_status()
    data = response.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("unexpected TradingView quote payload")
    return rows


def _history_change(symbol, now, price, seconds):
    with _LOCK:
        samples = list(_HISTORY.get(symbol) or [])

    if not samples:
        return 0.0

    target = now - seconds
    candidate = None
    for sample in samples:
        if sample["ts"] <= target:
            candidate = sample
        else:
            break

    if candidate is None:
        candidate = samples[0]

    old = _num(candidate.get("price"), 0.0)
    if not old:
        return 0.0
    return round((price / old - 1.0) * 100.0, 4)


def _volume_delta(symbol, now, volume, seconds=60):
    with _LOCK:
        samples = list(_HISTORY.get(symbol) or [])

    if not samples:
        return 0.0

    target = now - seconds
    candidate = None
    for sample in samples:
        if sample["ts"] <= target:
            candidate = sample
        else:
            break

    if candidate is None:
        candidate = samples[0]

    old = _num(candidate.get("volume"), 0.0) or 0.0
    return max(0.0, volume - old)


def fetch_fast_quotes(symbols):
    global _FAILURES, _NEXT_ALLOWED

    symbols = list(dict.fromkeys(s for s in symbols if s))
    if not symbols:
        return {}

    now = time.time()
    if now < _NEXT_ALLOWED:
        return {}

    quotes = {}

    try:
        for batch in _chunks(symbols, FAST_BATCH_SIZE):
            rows = _post_batch(batch)

            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = _clean_symbol(row.get("s"))
                values = row.get("d")
                if not symbol or not isinstance(values, list) or len(values) < 5:
                    continue

                price = _num(values[1])
                volume = _num(values[2], 0.0) or 0.0
                rvol = _num(values[3], 0.0) or 0.0
                day_change = _num(values[4], 0.0) or 0.0

                if price is None or price <= 0:
                    continue

                ch15 = _history_change(symbol, now, price, 15)
                ch30 = _history_change(symbol, now, price, 30)
                ch60 = _history_change(symbol, now, price, 60)
                vol_delta60 = _volume_delta(symbol, now, volume, 60)

                quote = {
                    "symbol": symbol,
                    "price": round(price, 4),
                    "volume": round(volume, 2),
                    "rvol": round(rvol, 3),
                    "day_change_pct": round(day_change, 3),
                    "change_15s_pct": ch15,
                    "change_30s_pct": ch30,
                    "change_60s_pct": ch60,
                    "volume_delta_60s": round(vol_delta60, 2),
                    "observed_at": now,
                    "source": "TRADINGVIEW_FAST",
                }
                quotes[symbol] = quote

                with _LOCK:
                    _HISTORY[symbol].append({
                        "ts": now,
                        "price": price,
                        "volume": volume,
                    })

        _FAILURES = 0
        _NEXT_ALLOWED = 0.0
        return quotes

    except Exception as exc:
        _FAILURES += 1
        _NEXT_ALLOWED = time.time() + min(120, 10 * (2 ** min(_FAILURES - 1, 4)))
        print(f"FAST QUOTE ERROR: {exc}", flush=True)
        return {}


def quote_is_fresh(quote):
    if not quote:
        return False
    observed = _num(quote.get("observed_at"), 0.0) or 0.0
    return observed > 0 and time.time() - observed <= FAST_QUOTE_MAX_AGE_SECONDS


def is_early_mover(quote):
    if not quote_is_fresh(quote):
        return False

    rvol = _num(quote.get("rvol"), 0.0) or 0.0
    day = _num(quote.get("day_change_pct"), 0.0) or 0.0
    ch15 = _num(quote.get("change_15s_pct"), 0.0) or 0.0
    ch30 = _num(quote.get("change_30s_pct"), 0.0) or 0.0
    ch60 = _num(quote.get("change_60s_pct"), 0.0) or 0.0

    if day >= 9.7:
        return False

    return bool(
        (rvol >= 1.20 and ch60 >= 0.22)
        or (rvol >= 1.35 and ch30 >= 0.16)
        or (rvol >= 1.60 and ch15 >= 0.10)
        or (day >= 2.0 and rvol >= 1.10 and ch60 > 0.0)
    )


def overlay_quote(item, quote, universe_metrics=None):
    if not item or not quote_is_fresh(quote):
        return None

    out = dict(item)
    out["current_price"] = quote["price"]
    out["fast_quote"] = dict(quote)
    out["fast_source"] = quote.get("source")
    out["fast_age_seconds"] = round(
        max(0.0, time.time() - float(quote.get("observed_at") or 0.0)),
        1,
    )

    metrics = dict(universe_metrics or {})
    out["universe_metrics"] = metrics
    return out


def fast_status():
    with _LOCK:
        tracked = len(_HISTORY)
    return {
        "tracked_symbols": tracked,
        "poll_seconds": FAST_POLL_SECONDS,
        "max_age_seconds": FAST_QUOTE_MAX_AGE_SECONDS,
        "failures": _FAILURES,
        "backoff_seconds": round(max(0.0, _NEXT_ALLOWED - time.time()), 1),
    }
