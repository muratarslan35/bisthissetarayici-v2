# fetch_bist.py
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone
from utils import (
    FALLBACK_SYMBOLS,
    calculate_rsi,
    moving_averages,
    detect_three_peaks,
    detect_support_resistance_break,
    to_tr_timezone,
)

yf.pdr_override = False

# Helper to download safely and return df or None
def yf_download_safe(ticker, period, interval):
    try:
        df = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            # sometimes yfinance returns multicol for many tickers; handle if single ticker requested
            # try to pull simple Close column
            if ("Close", ticker) in df.columns:
                df = pd.DataFrame({
                    "Open": df[("Open", ticker)],
                    "High": df[("High", ticker)],
                    "Low": df[("Low", ticker)],
                    "Close": df[("Close", ticker)],
                    "Volume": df[("Volume", ticker)]
                })
            else:
                return None
        if df is None or df.empty or "Close" not in df.columns:
            return None
        return df.dropna(how="all")
    except Exception:
        return None

def get_bist_symbols():
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        js = r.json()
        syms = []
        for item in js:
            code = item.get("indexCode", "")
            if code in ("XU030", "XU100"):
                comps = item.get("components", []) or []
                for c in comps:
                    s = c.get("symbol")
                    if s:
                        syms.append(s if s.endswith(".IS") else s + ".IS")
        syms = list(dict.fromkeys(syms))
        if syms:
            print("[fetch_bist] got symbols from API:", len(syms))
            return syms
    except Exception as e:
        print("[fetch_bist] get_bist_symbols fallback:", e)
    return FALLBACK_SYMBOLS.copy()

def fetch_timeframe_indicators(df, tf_label):
    """
    Given a dataframe for a timeframe (pandas DataFrame),
    compute RSI, MA20/50/100/200 (last values), green candle flags etc.
    Returns a dict.
    """
    out = {}
    if df is None or df.empty or "Close" not in df.columns:
        return out
    # RSI
    try:
        out["rsi"] = float(calculate_rsi(df["Close"]).iloc[-1])
    except Exception:
        out["rsi"] = None
    # Moving averages last values
    mas = {}
    for w in (20,50,100,200):
        try:
            mas[w] = float(df["Close"].rolling(window=w, min_periods=1).mean().iloc[-1])
        except Exception:
            mas[w] = None
    out["ma_values"] = mas
    # MA directions: compare last close to MA
    ma_dirs = {}
    cur = float(df["Close"].iloc[-1])
    for w, mv in mas.items():
        if mv is None:
            ma_dirs[w] = None
        else:
            ma_dirs[w] = "above" if cur > mv else "below"
    out["ma_dirs"] = ma_dirs
    # last closes for trend calculations
    out["last_close"] = float(df["Close"].iloc[-1])
    # simple volume check
    try:
        out["volume"] = int(df["Volume"].iloc[-1]) if "Volume" in df.columns else None
        out["volume_avg_5"] = int(df["Volume"].iloc[-6:-1].mean()) if "Volume" in df.columns and len(df) > 6 else out.get("volume")
    except Exception:
        out["volume"] = None
        out["volume_avg_5"] = None
    # green candle for most recent bar
    try:
        out["last_open"] = float(df["Open"].iloc[-1])
        out["last_green"] = out["last_close"] > out["last_open"]
    except Exception:
        out["last_open"] = None
        out["last_green"] = None
    # support/resistance on this timeframe
    try:
        s_break, r_break = detect_support_resistance_break(df, lookback=20)
        out["support_break"] = s_break
        out["resistance_break"] = r_break
    except Exception:
        out["support_break"] = False
        out["resistance_break"] = False

    return out

def fetch_one_symbol(sym):
    """
    Fetch multiple timeframes for a symbol and compute indicators.
    Returns a dict containing keys for 15m,1h,4h,1d plus previous behaviour preserved.
    """
    # primary short timeframe for frequent scanning (15m)
    df_15 = yf_download_safe(sym, period="7d", interval="15m")
    if df_15 is None:
        raise ValueError("no 15m data")

    # 1h, 4h, 1d downloads (may be lighter calls; cautious on rate limits)
    df_1h = yf_download_safe(sym, period="14d", interval="60m")   # 1h last 14 days
    df_4h = yf_download_safe(sym, period="60d", interval="240m")  # 4h last 60 days
    df_1d = yf_download_safe(sym, period="120d", interval="1d")   # 1d last 120 days

    # indicators for each timeframe
    tf_15 = fetch_timeframe_indicators(df_15, "15m")
    tf_1h  = fetch_timeframe_indicators(df_1h, "1h") if df_1h is not None else {}
    tf_4h  = fetch_timeframe_indicators(df_4h, "4h") if df_4h is not None else {}
    tf_1d  = fetch_timeframe_indicators(df_1d, "1d") if df_1d is not None else {}

    # derive combined / legacy fields (for backwards compatibility)
    current_price = tf_15.get("last_close", None)
    rsi_15 = tf_15.get("rsi", None)
    # daily change using df_15 first available vs last
    try:
        daily_change = round((current_price - df_15["Close"].iloc[0]) / df_15["Close"].iloc[0] * 100, 2)
    except Exception:
        daily_change = 0.0
    volume = tf_15.get("volume", None)

    # simple AL/SAT from 15m RSI (preserve legacy thresholds)
    last_signal = None
    if rsi_15 is not None:
        if rsi_15 < 30:
            last_signal = "AL"
        elif rsi_15 > 70:
            last_signal = "SAT"

    # three peak on 15m
    three_peak = detect_three_peaks(df_15["Close"]) if "Close" in df_15.columns else False

    # support/resistance overall as combination (prefer higher timeframe support)
    # compute sr per timeframe using detect_support_resistance_break already done in tf* dicts
    sr = {
        "15m": {"support": None, "resistance": None, "break": tf_15.get("support_break") or tf_15.get("resistance_break")},
        "1h":  {"support": None, "resistance": None, "break": tf_1h.get("support_break") or tf_1h.get("resistance_break")},
        "4h":  {"support": None, "resistance": None, "break": tf_4h.get("support_break") or tf_4h.get("resistance_break")},
        "1D":  {"support": None, "resistance": None, "break": tf_1d.get("support_break") or tf_1d.get("resistance_break")},
    }
    # For user display, compute nearest support/resistance numeric levels using simple method:
    # use rolling min/max of low/high over lookbacks
    try:
        sr["15m"]["support"] = float(df_15["Low"].rolling(window=20, min_periods=1).min().iloc[-1])
        sr["15m"]["resistance"] = float(df_15["High"].rolling(window=20, min_periods=1).max().iloc[-1])
    except Exception:
        sr["15m"]["support"] = None; sr["15m"]["resistance"] = None
    if df_1h is not None:
        try:
            sr["1h"]["support"] = float(df_1h["Low"].rolling(window=20, min_periods=1).min().iloc[-1])
            sr["1h"]["resistance"] = float(df_1h["High"].rolling(window=20, min_periods=1).max().iloc[-1])
        except Exception:
            sr["1h"]["support"] = None; sr["1h"]["resistance"] = None
    if df_4h is not None:
        try:
            sr["4h"]["support"] = float(df_4h["Low"].rolling(window=20, min_periods=1).min().iloc[-1])
            sr["4h"]["resistance"] = float(df_4h["High"].rolling(window=20, min_periods=1).max().iloc[-1])
        except Exception:
            sr["4h"]["support"] = None; sr["4h"]["resistance"] = None
    if df_1d is not None:
        try:
            sr["1D"]["support"] = float(df_1d["Low"].rolling(window=20, min_periods=1).min().iloc[-1])
            sr["1D"]["resistance"] = float(df_1d["High"].rolling(window=20, min_periods=1).max().iloc[-1])
        except Exception:
            sr["1D"]["support"] = None; sr["1D"]["resistance"] = None

    # Combined (legacy) composite check (kept)
    composite_signal = None
    try:
        # if yesterday and today green on daily and 15m has green_11/15 proxies -> composite
        if tf_1d.get("last_green") and tf_1d.get("last_green") and (tf_15.get("last_green") or tf_1h.get("last_green")):
            composite_signal = "A"
    except Exception:
        composite_signal = None

    # Build result dict
    out = {
        "symbol": sym.replace(".IS", ""),
        "current_price": current_price,
        "rsi_15": rsi_15,
        "RSI": rsi_15,  # legacy
        "last_signal": last_signal,
        "daily_change": f"%{daily_change}",
        "volume": volume,
        "three_peak_break": three_peak,
        "trend": "Yukarı" if tf_15.get("ma_dirs", {}).get(20) == "above" else "Aşağı",
        # timeframe blocks
        "tf": {
            "15m": tf_15,
            "1h": tf_1h,
            "4h": tf_4h,
            "1d": tf_1d
        },
        "support_resistance": sr,
        "support_break": tf_15.get("support_break", False),
        "resistance_break": tf_15.get("resistance_break", False),
        "ma_breaks": tf_15.get("ma_dirs", {}),   # legacy: per MA whether price above/below on 15m
        "ma_values": {
            "15m": tf_15.get("ma_values", {}),
            "1h": tf_1h.get("ma_values", {}),
            "4h": tf_4h.get("ma_values", {}),
            "1d": tf_1d.get("ma_values", {})
        },
        "composite_signal": composite_signal,
    }

    # Super-combined (Süper kombine): OPTION 2 logic (all conditions must be met)
    # Conditions described by user:
    # 1) 15m: recent MA20 upward break (we require that price crossed above MA20 in last 2 bars) AND short-term MA20 slope positive
    #    and volume spike (volume now > avg last 5 bars or > volume 5 bars ago)
    # 2) 1h: price above MA50 and MA20 slope positive (MA20[i] > MA20[i-3])
    # 3) 4h: MA20 > MA50 and MA20 slope upwards and MA20 recently "yukarı kırdı"
    # 4) daily: today's candle green and price above daily MA20
    # 5) RSI between 45-65 and no three_peak and (if resistance_break -> bonus)
    super_ok = False
    try:
        # 15m checks
        tf15 = tf_15
        tf1h = tf_1h
        tf4h = tf_4h
        tf1d = tf_1d
        cond1 = False
        cond2 = False
        cond3 = False
        cond4 = False
        cond5 = False

        # 1) 15m: price crossed above MA20 in last 2 bars
        if df_15 is not None and "Close" in df_15.columns:
            ma20_15 = df_15["Close"].rolling(20, min_periods=1).mean()
            if len(ma20_15) >= 3:
                prev2 = df_15["Close"].iloc[-3]
                prev1 = df_15["Close"].iloc[-2]
                curr = df_15["Close"].iloc[-1]
                # cross-up if prev1 <= ma20_prev1 and curr > ma20_curr OR curr > ma20_prev1 etc
                if (prev1 <= ma20_15.iloc[-2] and curr > ma20_15.iloc[-1]) or (prev2 <= ma20_15.iloc[-3] and prev1 > ma20_15.iloc[-2] and curr > ma20_15.iloc[-1]):
                    cond1 = True
            # slope positive?
            try:
                ma20_slope = ma20_15.iloc[-1] - ma20_15.iloc[-3] if len(ma20_15) >= 3 else 0
                cond1 = cond1 and (ma20_slope > 0)
            except Exception:
                cond1 = cond1
            # volume spike
            try:
                v_now = int(df_15["Volume"].iloc[-1])
                v_avg = int(df_15["Volume"].iloc[-6:-1].mean()) if len(df_15) > 6 else v_now
                if v_now > v_avg or (len(df_15) > 5 and v_now > int(df_15["Volume"].iloc[-6])):
                    cond1 = cond1 and True
            except Exception:
                pass

        # 2) 1h: price above MA50 and MA20 slope positive
        if df_1h is not None and "Close" in df_1h.columns:
            ma50_1h = df_1h["Close"].rolling(50, min_periods=1).mean()
            ma20_1h = df_1h["Close"].rolling(20, min_periods=1).mean()
            if len(ma50_1h) >= 1 and len(ma20_1h) >= 4:
                price_1h = df_1h["Close"].iloc[-1]
                if price_1h > ma50_1h.iloc[-1]:
                    cond2 = True
                # ma20 slope over 3 bars:
                if ma20_1h.iloc[-1] > ma20_1h.iloc[-4]:
                    cond2 = cond2 and True
                else:
                    cond2 = False if cond2 else False

        # 3) 4h: MA20 > MA50 and ma20 slope up and price cross-up recently
        if df_4h is not None and "Close" in df_4h.columns:
            ma20_4h = df_4h["Close"].rolling(20, min_periods=1).mean()
            ma50_4h = df_4h["Close"].rolling(50, min_periods=1).mean()
            if len(ma20_4h) >= 2 and len(ma50_4h) >= 2:
                if ma20_4h.iloc[-1] > ma50_4h.iloc[-1]:
                    # slope
                    if ma20_4h.iloc[-1] > ma20_4h.iloc[-3] if len(ma20_4h) >= 3 else True:
                        cond3 = True

        # 4) daily: today's candle green and price above daily ma20
        if df_1d is not None and "Close" in df_1d.columns:
            if len(df_1d) >= 1:
                today_close = df_1d["Close"].iloc[-1]
                today_open = df_1d["Open"].iloc[-1]
                ma20_1d = df_1d["Close"].rolling(20, min_periods=1).mean()
                if today_close > today_open and today_close > (ma20_1d.iloc[-1] if len(ma20_1d) >= 1 else -1):
                    cond4 = True

        # 5) RSI between 45-65 & no three peaks
        rsi_val = tf15.get("rsi", None)
        if rsi_val is not None:
            if 45 <= rsi_val <= 65 and not three_peak:
                cond5 = True

        super_ok = all([cond1, cond2, cond3, cond4, cond5])

    except Exception:
        super_ok = False

    out["super_combined_ok"] = super_ok
    # extra: if super_ok and resistance_break on higher timeframe, mark bonus
    try:
        out["super_bonus"] = super_ok and (tf_4h.get("resistance_break") or tf_1d.get("resistance_break"))
    except Exception:
        out["super_bonus"] = False

    return out

def fetch_bist_data():
    syms = get_bist_symbols()
    results = []
    for s in syms:
        try:
            rec = fetch_one_symbol(s)
            if rec:
                results.append(rec)
        except Exception as e:
            print("[fetch_bist] fetch error for", s, e)
            time.sleep(0.05)
            continue
        time.sleep(0.12)
    return results
