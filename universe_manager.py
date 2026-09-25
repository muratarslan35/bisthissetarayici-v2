import json
import math
import os
import re
import threading
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from utils import FALLBACK_SYMBOLS

TRADINGVIEW_SCAN_URL = os.getenv(
    "TRADINGVIEW_SCAN_URL",
    "https://scanner.tradingview.com/turkey/scan",
)

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "data" / "dynamic_universe.json"

REFRESH_SECONDS = max(300, int(os.getenv("UNIVERSE_REFRESH_SECONDS", "900")))
DAILY_ENRICH_SECONDS = max(1800, int(os.getenv("UNIVERSE_DAILY_ENRICH_SECONDS", "3600")))
MAX_DISCOVERED = max(300, int(os.getenv("UNIVERSE_MAX_DISCOVERED", "900")))
PROMOTED_MAX = max(20, int(os.getenv("UNIVERSE_PROMOTED_MAX", "140")))
FAST_WATCHLIST_SIZE = max(15, int(os.getenv("FAST_WATCHLIST_SIZE", "60")))
ENRICH_MAX = max(100, int(os.getenv("UNIVERSE_ENRICH_MAX", "360")))

MIN_PRICE = max(0.10, float(os.getenv("UNIVERSE_MIN_PRICE_TL", "1.0")))
MIN_DAILY_TURNOVER = max(
    1_000_000.0,
    float(os.getenv("UNIVERSE_MIN_DAILY_TURNOVER_TL", "20000000")),
)
SLOW_BURN_MIN_TURNOVER = max(
    1_000_000.0,
    float(os.getenv("UNIVERSE_SLOW_BURN_MIN_TURNOVER_TL", "8000000")),
)

_LOCK = threading.RLock()
_STATE = {
    "refreshed_at": 0.0,
    "daily_enriched_at": 0.0,
    "source": "STATIC_FALLBACK",
    "discovered": list(dict.fromkeys(FALLBACK_SYMBOLS)),
    "promoted": [],
    "fast_watchlist": list(dict.fromkeys(FALLBACK_SYMBOLS))[:FAST_WATCHLIST_SIZE],
    "metrics": {},
}


def _num(value, default=0.0):
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return default


def _clean_symbol(value):
    raw = str(value or "").upper().strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    raw = raw.replace(".IS", "")
    if not re.fullmatch(r"[A-Z0-9]{2,8}", raw):
        return None
    return raw + ".IS"


def _load_state():
    global _STATE
    try:
        if not STATE_PATH.exists():
            return
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        with _LOCK:
            _STATE.update({
                "refreshed_at": _num(data.get("refreshed_at"), 0.0),
                "daily_enriched_at": _num(data.get("daily_enriched_at"), 0.0),
                "source": str(data.get("source") or "PERSISTED"),
                "discovered": list(dict.fromkeys(data.get("discovered") or FALLBACK_SYMBOLS)),
                "promoted": list(dict.fromkeys(data.get("promoted") or [])),
                "fast_watchlist": list(dict.fromkeys(data.get("fast_watchlist") or [])),
                "metrics": data.get("metrics") if isinstance(data.get("metrics"), dict) else {},
            })
    except Exception:
        return


def _save_state():
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            payload = dict(_STATE)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(STATE_PATH)
    except Exception as exc:
        print(f"UNIVERSE STATE SAVE ERROR: {exc}", flush=True)


def _scanner_payload(columns, strict=True):
    payload = {
        "markets": ["turkey"],
        "options": {"lang": "tr"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": columns,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, MAX_DISCOVERED],
    }
    if strict:
        payload["filter"] = [
            {"left": "exchange", "operation": "equal", "right": "BIST"},
            {"left": "type", "operation": "equal", "right": "stock"},
        ]
    return payload


def _post_scanner(payload):
    response = requests.post(TRADINGVIEW_SCAN_URL, json=payload, timeout=12)
    response.raise_for_status()
    data = response.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("unexpected TradingView scanner payload")
    return rows


def _discover_rows():
    variants = [
        (
            [
                "name",
                "type",
                "subtype",
                "close",
                "volume",
                "relative_volume_10d_calc",
                "change",
                "Perf.W",
                "market_cap_basic",
            ],
            True,
        ),
        (
            [
                "name",
                "close",
                "volume",
                "relative_volume_10d_calc",
                "change",
                "Perf.W",
                "market_cap_basic",
            ],
            True,
        ),
        (
            ["name", "close", "volume", "relative_volume_10d_calc", "change"],
            False,
        ),
    ]

    last_error = None
    for columns, strict in variants:
        try:
            rows = _post_scanner(_scanner_payload(columns, strict=strict))
            if rows:
                return rows, columns
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"TradingView discovery failed: {last_error}")


def _extract_metrics(rows, columns):
    metrics = {}
    idx = {name: i for i, name in enumerate(columns)}

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = _clean_symbol(row.get("s"))
        if not symbol:
            continue

        values = row.get("d")
        if not isinstance(values, list):
            continue

        def get(name, default=None):
            pos = idx.get(name)
            if pos is None or pos >= len(values):
                return default
            return values[pos]

        security_type = str(get("type") or "").lower().strip()
        subtype = str(get("subtype") or "").lower().strip()

        if security_type and security_type not in {"stock", "dr"}:
            continue
        if subtype in {"etf", "fund", "warrant", "right"}:
            continue

        close = _num(get("close"), 0.0)
        volume = _num(get("volume"), 0.0)
        turnover = max(0.0, close * volume)
        rvol = max(0.0, _num(get("relative_volume_10d_calc"), 0.0))
        day_change = _num(get("change"), 0.0)
        perf_week = _num(get("Perf.W"), 0.0)
        market_cap = max(0.0, _num(get("market_cap_basic"), 0.0))

        if close < MIN_PRICE or volume <= 0:
            continue

        metrics[symbol] = {
            "price": round(close, 4),
            "volume": round(volume, 2),
            "turnover": round(turnover, 2),
            "rvol": round(rvol, 3),
            "day_change_pct": round(day_change, 3),
            "five_day_return_pct": round(perf_week, 3),
            "market_cap": round(market_cap, 2),
            "source": "TRADINGVIEW_SCANNER",
        }

    return metrics


def _yf_extract(raw, ticker):
    if raw is None or raw.empty:
        return None

    try:
        if isinstance(raw.columns, pd.MultiIndex):
            lv0 = {str(v) for v in raw.columns.get_level_values(0)}
            lv1 = {str(v) for v in raw.columns.get_level_values(1)}
            if ticker in lv0:
                df = raw[ticker].copy()
            elif ticker in lv1:
                df = raw.xs(ticker, axis=1, level=1).copy()
            else:
                return None
        else:
            df = raw.copy()

        if df is None or df.empty:
            return None

        df.columns = [str(c).title() for c in df.columns]
        if not {"Close", "Volume"}.issubset(df.columns):
            return None

        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
        df = df.dropna(subset=["Close"])
        return df if not df.empty else None
    except Exception:
        return None


def _daily_enrich(metrics):
    if not metrics:
        return metrics

    ranked = sorted(
        metrics,
        key=lambda s: (
            metrics[s].get("turnover", 0.0),
            metrics[s].get("rvol", 0.0),
        ),
        reverse=True,
    )[:ENRICH_MAX]

    for start in range(0, len(ranked), 60):
        batch = ranked[start:start + 60]
        try:
            raw = yf.download(
                tickers=" ".join(batch),
                interval="1d",
                period="1mo",
                group_by="ticker",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception as exc:
            print(f"UNIVERSE DAILY ENRICH ERROR: {exc}", flush=True)
            continue

        for symbol in batch:
            df = _yf_extract(raw, symbol)
            if df is None or len(df) < 6:
                continue

            close = df["Close"].astype(float)
            volume = df["Volume"].fillna(0.0).astype(float)

            def ret(days):
                if len(close) <= days or close.iloc[-days - 1] <= 0:
                    return None
                return (close.iloc[-1] / close.iloc[-days - 1] - 1.0) * 100.0

            ret3 = ret(3)
            ret5 = ret(5)
            turnover10 = (close * volume).tail(10).mean()
            positive_days = int((close.diff().tail(5) > 0).sum())
            abs_move5 = float(close.pct_change().tail(5).abs().mean() * 100.0)

            row = metrics.get(symbol)
            if row is None:
                continue
            if ret3 is not None:
                row["three_day_return_pct"] = round(float(ret3), 3)
            if ret5 is not None:
                row["five_day_return_pct"] = round(float(ret5), 3)
            row["avg_turnover_10d"] = round(float(turnover10 or 0.0), 2)
            row["positive_days_5d"] = positive_days
            row["avg_abs_move_5d_pct"] = round(abs_move5, 3)

        del raw

    return metrics


def _candidate_score(row):
    turnover = max(
        _num(row.get("avg_turnover_10d"), 0.0),
        _num(row.get("turnover"), 0.0),
    )
    rvol = _num(row.get("rvol"), 0.0)
    day = _num(row.get("day_change_pct"), 0.0)
    ret3 = _num(row.get("three_day_return_pct"), 0.0)
    ret5 = _num(row.get("five_day_return_pct"), 0.0)
    positive_days = int(_num(row.get("positive_days_5d"), 0))

    liquidity = max(0.0, min(30.0, (math.log10(max(turnover, 1.0)) - 6.5) * 13.0))
    rvol_score = max(0.0, min(22.0, (rvol - 0.70) * 14.0))
    day_score = max(-8.0, min(14.0, day * 2.2))
    three_day_score = max(-8.0, min(16.0, ret3 * 1.8))
    five_day_score = max(-10.0, min(18.0, ret5 * 1.2))
    consistency = min(10.0, positive_days * 2.0)

    return round(
        liquidity + rvol_score + day_score + three_day_score + five_day_score + consistency,
        3,
    )


def _classify(metrics):
    core = set(FALLBACK_SYMBOLS)
    eligible = []

    for symbol, row in metrics.items():
        turnover = max(
            _num(row.get("avg_turnover_10d"), 0.0),
            _num(row.get("turnover"), 0.0),
        )
        rvol = _num(row.get("rvol"), 0.0)
        day = _num(row.get("day_change_pct"), 0.0)
        ret3 = _num(row.get("three_day_return_pct"), 0.0)
        ret5 = _num(row.get("five_day_return_pct"), 0.0)
        positive_days = int(_num(row.get("positive_days_5d"), 0))

        liquid = turnover >= MIN_DAILY_TURNOVER
        slow_burn = (
            turnover >= SLOW_BURN_MIN_TURNOVER
            and 1.0 <= ret3 <= 10.0
            and 2.0 <= ret5 <= 18.0
            and positive_days >= 3
            and rvol >= 0.60
            and -2.0 <= day <= 5.0
        )

        fast_candidate = (
            turnover >= MIN_DAILY_TURNOVER
            and (
                rvol >= 1.15
                or day >= 1.5
                or ret3 >= 2.5
                or ret5 >= 4.0
            )
            and day < 9.7
        )

        score = _candidate_score(row)
        row["score"] = score
        row["liquid"] = bool(liquid)
        row["slow_burn"] = bool(slow_burn)
        row["fast_candidate"] = bool(fast_candidate)

        if liquid or slow_burn:
            eligible.append((symbol, score))

    eligible.sort(key=lambda x: x[1], reverse=True)

    promoted = [
        symbol
        for symbol, _ in eligible
        if symbol not in core
    ][:PROMOTED_MAX]

    fast = [
        symbol
        for symbol, _ in eligible
        if metrics.get(symbol, {}).get("fast_candidate")
    ][:FAST_WATCHLIST_SIZE]

    if len(fast) < FAST_WATCHLIST_SIZE:
        for symbol, _ in eligible:
            if symbol not in fast:
                fast.append(symbol)
            if len(fast) >= FAST_WATCHLIST_SIZE:
                break

    return promoted, fast


def refresh_universe(force=False, log=print):
    now = time.time()

    with _LOCK:
        last_refresh = _num(_STATE.get("refreshed_at"), 0.0)
        last_daily = _num(_STATE.get("daily_enriched_at"), 0.0)

    if not force and now - last_refresh < REFRESH_SECONDS:
        return universe_status()

    try:
        rows, columns = _discover_rows()
        metrics = _extract_metrics(rows, columns)
        if not metrics:
            raise RuntimeError("discovery returned no valid BIST stocks")

        do_enrich = force or now - last_daily >= DAILY_ENRICH_SECONDS
        if do_enrich:
            metrics = _daily_enrich(metrics)
            daily_enriched_at = now
        else:
            with _LOCK:
                old = _STATE.get("metrics") or {}
            for symbol, row in metrics.items():
                old_row = old.get(symbol) if isinstance(old, dict) else None
                if isinstance(old_row, dict):
                    for key in (
                        "three_day_return_pct",
                        "five_day_return_pct",
                        "avg_turnover_10d",
                        "positive_days_5d",
                        "avg_abs_move_5d_pct",
                    ):
                        if key in old_row:
                            row[key] = old_row[key]
            daily_enriched_at = last_daily

        promoted, fast = _classify(metrics)
        discovered = sorted(metrics.keys())

        with _LOCK:
            _STATE.update({
                "refreshed_at": now,
                "daily_enriched_at": daily_enriched_at,
                "source": "TRADINGVIEW+YAHOO_DAILY" if do_enrich else "TRADINGVIEW",
                "discovered": discovered,
                "promoted": promoted,
                "fast_watchlist": fast,
                "metrics": metrics,
            })
        _save_state()

        log(
            "UNIVERSE_REFRESH=PASS "
            f"discovered={len(discovered)} "
            f"promoted={len(promoted)} "
            f"fast={len(fast)} "
            f"source={_STATE['source']}"
        )
    except Exception as exc:
        log(f"UNIVERSE_REFRESH=FALLBACK error={exc}")

    return universe_status()


def get_active_universe():
    with _LOCK:
        promoted = list(_STATE.get("promoted") or [])
    return list(dict.fromkeys(list(FALLBACK_SYMBOLS) + promoted))


def get_discovered_universe():
    with _LOCK:
        discovered = list(_STATE.get("discovered") or [])
    return list(dict.fromkeys(discovered or FALLBACK_SYMBOLS))


def get_fast_watchlist(extra_symbols=None):
    with _LOCK:
        fast = list(_STATE.get("fast_watchlist") or [])
    merged = fast + list(extra_symbols or [])
    return list(dict.fromkeys(s for s in merged if s))


def get_symbol_metrics(symbol):
    with _LOCK:
        row = (_STATE.get("metrics") or {}).get(symbol)
        return dict(row) if isinstance(row, dict) else {}


def universe_status():
    with _LOCK:
        return {
            "refreshed_at": _STATE.get("refreshed_at"),
            "daily_enriched_at": _STATE.get("daily_enriched_at"),
            "source": _STATE.get("source"),
            "discovered_count": len(_STATE.get("discovered") or []),
            "active_count": len(get_active_universe()),
            "promoted_count": len(_STATE.get("promoted") or []),
            "fast_watchlist_count": len(_STATE.get("fast_watchlist") or []),
        }


def universe_refresh_loop(log=print):
    while True:
        try:
            refresh_universe(force=False, log=log)
        except Exception as exc:
            log(f"UNIVERSE LOOP ERROR: {exc}")
        time.sleep(min(60, max(15, REFRESH_SECONDS // 10)))


_load_state()
