import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

TR_TZ = ZoneInfo("Europe/Istanbul")

BATCH_SIZE = max(5, int(os.getenv("YF_BATCH_SIZE", "35")))
BATCH_PAUSE_SECONDS = max(0.0, float(os.getenv("YF_BATCH_PAUSE_SECONDS", "0.4")))
INTRADAY_TTL = max(120, int(os.getenv("YF_INTRADAY_TTL_SECONDS", "300")))
DAILY_TTL = max(900, int(os.getenv("YF_DAILY_TTL_SECONDS", "21600")))
MAX_BACKOFF = max(300, int(os.getenv("YF_MAX_BACKOFF_SECONDS", "900")))

_CACHE = {
    "15m": {"data": {}, "ts": 0.0, "failures": 0, "next_allowed": 0.0},
    "1d": {"data": {}, "ts": 0.0, "failures": 0, "next_allowed": 0.0},
}


def _chunks(values, size):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _yf_symbol(symbol):
    clean = str(symbol).replace(".IS", "").upper().strip()
    return f"{clean}.IS"


def _normalize_df(df):
    if df is None or df.empty:
        return None

    out = df.copy()
    out.columns = [str(c).title() for c in out.columns]

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(out.columns)):
        return None

    idx = pd.to_datetime(out.index, errors="coerce", utc=True)
    out.index = idx
    out = out[~out.index.isna()]
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out.dropna(subset=["Open", "High", "Low", "Close"])

    for c in ("Open", "High", "Low", "Close", "Volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out["Volume"] = out["Volume"].fillna(0.0)
    return out if not out.empty else None


def _extract_ticker(raw, ticker):
    if raw is None or raw.empty:
        return None

    if not isinstance(raw.columns, pd.MultiIndex):
        return _normalize_df(raw)

    lv0 = {str(v) for v in raw.columns.get_level_values(0)}
    lv1 = {str(v) for v in raw.columns.get_level_values(1)}

    try:
        if ticker in lv0:
            return _normalize_df(raw[ticker])
        if ticker in lv1:
            return _normalize_df(raw.xs(ticker, axis=1, level=1))
    except Exception:
        return None

    return None


def _download_batch(batch, interval, period):
    tickers = [_yf_symbol(s) for s in batch]
    raw = yf.download(
        tickers=" ".join(tickers),
        interval=interval,
        period=period,
        group_by="ticker",
        progress=False,
        auto_adjust=False,
        threads=False,
    )

    result = {}
    for original, ticker in zip(batch, tickers):
        df = _extract_ticker(raw, ticker)
        if df is not None and not df.empty:
            result[original] = df
    return result


def _ttl(interval):
    return INTRADAY_TTL if interval == "15m" else DAILY_TTL


def _period(interval):
    # 15m: enough history for EMA200 + intraday/session statistics.
    # 1d: enough warm-up for EMA200 and 20/60/120 day structures.
    return "10d" if interval == "15m" else "2y"


def _download_universe(symbols, interval):
    state = _CACHE[interval]
    now = time.time()
    symbols = list(dict.fromkeys(symbols))

    cache_fresh = bool(state["data"]) and now - state["ts"] < _ttl(interval)
    missing = [symbol for symbol in symbols if symbol not in state["data"]]

    if cache_fresh and not missing:
        return state["data"]

    if now < state["next_allowed"]:
        return state["data"]

    # When dynamic-universe discovery promotes a new stock, do not wait for the
    # broad cache TTL to expire. Fetch only the newly requested symbols and
    # merge them into the current cache. Normal TTL expiry still refreshes the
    # whole requested universe.
    fetch_symbols = missing if cache_fresh and missing else symbols
    fresh = {}
    had_error = False

    for batch in _chunks(fetch_symbols, BATCH_SIZE):
        try:
            fresh.update(_download_batch(batch, interval, _period(interval)))
        except Exception as exc:
            had_error = True
            print(f"YF BATCH ERROR [{interval}] {batch[0]}..: {exc}", flush=True)

        if BATCH_PAUSE_SECONDS:
            time.sleep(BATCH_PAUSE_SECONDS)

    if fresh:
        if cache_fresh and missing:
            state["data"].update(fresh)
        else:
            state["data"] = fresh
            state["ts"] = now

    if had_error:
        state["failures"] += 1
        backoff = min(MAX_BACKOFF, 15 * (2 ** min(state["failures"], 6)))
        state["next_allowed"] = now + backoff
    else:
        state["failures"] = 0
        state["next_allowed"] = 0.0

    return state["data"]


def _ema(series, span):
    return pd.Series(series, index=series.index).astype(float).ewm(
        span=span, adjust=False
    ).mean()


def _rsi(series, period=14):
    s = pd.Series(series, index=series.index).astype(float)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def _resample_bist(df, rule):
    if df is None or df.empty:
        return None

    local = df.copy()
    local.index = local.index.tz_convert(TR_TZ)

    try:
        local = local.between_time("10:00", "18:10")
    except Exception:
        pass

    out = (
        local.resample(
            rule,
            origin="start_day",
            offset="10h",
            label="left",
            closed="left",
        )
        .agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        })
        .dropna(subset=["Open", "High", "Low", "Close"])
    )

    return out if not out.empty else None


def _build_tf(intraday, daily):
    if intraday is None or intraday.empty or daily is None or daily.empty:
        return None

    d15 = intraday.copy()
    d15["ema20"] = _ema(d15["Close"], 20)
    d15["ema50"] = _ema(d15["Close"], 50)
    d15["ema200"] = _ema(d15["Close"], 200)
    d15["rsi"] = _rsi(d15["Close"])
    d15["volume_ok"] = d15["Volume"] > d15["Volume"].rolling(20).mean() * 1.5

    tf = {
        "15m": {
            "ema20": float(d15["ema20"].iloc[-1]),
            "ema50": float(d15["ema50"].iloc[-1]),
            "ema200": float(d15["ema200"].iloc[-1]),
            # V3 does not synthesize fake "live EMA" values.
            "ema20_live": float(d15["ema20"].iloc[-1]),
            "ema50_live": float(d15["ema50"].iloc[-1]),
            "ema200_live": float(d15["ema200"].iloc[-1]),
            "rsi": float(d15["rsi"].iloc[-1]),
            "volume_ok": bool(d15["volume_ok"].iloc[-1]),
            "df": d15,
        }
    }

    for name, rule in (("1h", "1h"), ("4h", "4h")):
        frame = _resample_bist(d15, rule)
        if frame is None or len(frame) < 5:
            continue
        frame["ema20"] = _ema(frame["Close"], 20)
        frame["ema50"] = _ema(frame["Close"], 50)
        frame["rsi"] = _rsi(frame["Close"])
        tf[name] = {
            "ema20": float(frame["ema20"].iloc[-1]),
            "ema50": float(frame["ema50"].iloc[-1]),
            "rsi": float(frame["rsi"].iloc[-1]),
            "df": frame,
        }

    d1 = daily.copy()
    d1["ema20"] = _ema(d1["Close"], 20)
    d1["ema50"] = _ema(d1["Close"], 50)
    d1["ema200"] = _ema(d1["Close"], 200)
    d1["rsi"] = _rsi(d1["Close"])
    tf["1d"] = {
        "ema20": float(d1["ema20"].iloc[-1]),
        "ema50": float(d1["ema50"].iloc[-1]),
        "ema200": float(d1["ema200"].iloc[-1]),
        "rsi": float(d1["rsi"].iloc[-1]),
        "df": d1,
    }

    return tf


def fetch_market_snapshot(symbols):
    """
    Low-request market snapshot.

    External traffic is centralized here:
      - Yahoo multi-symbol batch download
      - TTL cache
      - exponential backoff on failures
    No per-indicator or per-strategy network calls are made.
    """
    symbols = list(dict.fromkeys(symbols))
    intraday_map = _download_universe(symbols, "15m")
    daily_map = _download_universe(symbols, "1d")
    fetched_at = datetime.now(timezone.utc)

    results = []

    for symbol in symbols:
        intraday = intraday_map.get(symbol)
        daily = daily_map.get(symbol)

        if intraday is None or intraday.empty or daily is None or daily.empty:
            continue

        tf = _build_tf(intraday, daily)
        if not tf:
            continue

        try:
            price = float(intraday["Close"].iloc[-1])
        except Exception:
            continue

        bar_time = intraday.index[-1]
        try:
            age_minutes = max(
                0.0,
                (pd.Timestamp(fetched_at) - pd.Timestamp(bar_time)).total_seconds() / 60.0,
            )
        except Exception:
            age_minutes = 999.0

        confidence = 100
        if age_minutes > 50:
            confidence -= 35
        elif age_minutes > 35:
            confidence -= 20
        if len(intraday) < 200:
            confidence -= 15
        if len(daily) < 220:
            confidence -= 20
        confidence = max(0, min(100, confidence))

        results.append({
            "symbol": symbol,
            "current_price": price,
            "tf": tf,
            "fetched_at": fetched_at,
            "source_bar_time": bar_time,
            "data_age_minutes": round(age_minutes, 1),
            "data_confidence": confidence,
            "data_source": "YAHOO_BATCH",
        })

    return results


def cache_status():
    now = time.time()
    out = {}
    for interval, state in _CACHE.items():
        out[interval] = {
            "symbols": len(state["data"]),
            "age_seconds": round(max(0.0, now - state["ts"]), 1) if state["ts"] else None,
            "failures": state["failures"],
            "backoff_seconds": round(max(0.0, state["next_allowed"] - now), 1),
        }
    return out
