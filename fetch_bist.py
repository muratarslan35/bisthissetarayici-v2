# fetch_bist.py
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone, timedelta
from utils import FALLBACK_SYMBOLS, calculate_rsi, moving_averages, detect_three_peaks, detect_support_resistance_break

# use yfinance with progress=False to avoid noisy output
yf.pdr_override = False  # not using pandas-datareader; just ensure no override warnings

def get_bist_symbols():
    """
    Attempts to fetch BIST30 + BIST100 symbols from isyatirim API,
    falls back to FALLBACK_SYMBOLS from utils if failure.
    """
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        js = r.json()
        symbols = []
        for item in js:
            idx = item.get("indexCode")
            comps = item.get("components", [])
            for c in comps:
                sym = c.get("symbol")
                if sym:
                    symbols.append(sym if sym.endswith(".IS") else sym + ".IS")
        symbols = list(dict.fromkeys(symbols))
        if symbols:
            return symbols
    except Exception as e:
        # fallback
        print("get_bist_symbols fallback:", e)
    # final fallback: use provided list (already in utils)
    return FALLBACK_SYMBOLS.copy()

def fetch_one_symbol(sym):
    """
    Fetch 15m bars for last 7 days for a single symbol.
    Return dict with all flags used by app.
    """
    try:
        df = yf.download(sym, period="7d", interval="15m", auto_adjust=True, progress=False)
    except Exception as e:
        raise

    # handle multiindex or empty frames
    if df.empty:
        raise ValueError("empty df")

    # Normalize when multiindex returns (not expected for single ticker)
    if isinstance(df.columns, pd.MultiIndex):
        # try to extract by (field, sym)
        if ("Close", sym) in df.columns:
            df = pd.DataFrame({
                "Open": df[("Open", sym)],
                "High": df[("High", sym)],
                "Low": df[("Low", sym)],
                "Close": df[("Close", sym)],
                "Volume": df[("Volume", sym)]
            })
        else:
            # unexpected structure
            raise ValueError("multiindex unexpected")

    # Basic cleanup
    df = df.dropna(how="all")
    if df.empty or "Close" not in df.columns:
        raise ValueError("no Close column")

    # Indicators
    df["RSI"] = calculate_rsi(df["Close"])
    rsi_val = float(df["RSI"].iloc[-1])

    ma_vals = moving_averages(df, windows=[20,50,100,200])
    current_price = float(df["Close"].iloc[-1])

    # MA relative state: "above"/"below"
    ma_breaks = {}
    for k, v in ma_vals.items():
        if v is None:
            ma_breaks[f"MA{str(k)}"] = None
        else:
            ma_breaks[f"MA{str(k)}"] = "above" if current_price > v else "below"

    # detect 20x50 cross (golden/death)
    try:
        ma20 = df["Close"].rolling(20, min_periods=1).mean()
        ma50 = df["Close"].rolling(50, min_periods=1).mean()
        if len(ma20) >= 2 and len(ma50) >= 2:
            if ma20.iloc[-2] <= ma50.iloc[-2] and ma20.iloc[-1] > ma50.iloc[-1]:
                ma_breaks["20x50"] = "golden_cross"
            elif ma20.iloc[-2] >= ma50.iloc[-2] and ma20.iloc[-1] < ma50.iloc[-1]:
                ma_breaks["20x50"] = "death_cross"
    except Exception:
        pass

    # support / resistance break
    support_break, resistance_break = detect_support_resistance_break(df, lookback=20)

    # three peaks
    three_peak = detect_three_peaks(df["Close"])

    # 11:00 and 15:00 green candle detection on 15m bars:
    df_local = df.copy()
    df_local["hour"] = df_local.index.tz_localize(None).hour
    green_11 = any((df_local["hour"] == 11) & (df_local["Close"] > df_local["Open"]))
    green_15 = any((df_local["hour"] == 15) & (df_local["Close"] > df_local["Open"]))

    # trend
    trend = "Yukarı" if ma_vals.get(20) and ma_vals.get(50) and ma_vals[20] > ma_vals[50] else "Aşağı"

    # daily change percentage (using earliest available in df)
    try:
        daily_change = round((current_price - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2)
    except Exception:
        daily_change = 0.0

    volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns and not pd.isna(df["Volume"].iloc[-1]) else None

    # basic AL/SAT from RSI thresholds (user had many thresholds across conversation; using 30/70 here as safe)
    last_signal = None
    if rsi_val < 30:
        last_signal = "AL"
    elif rsi_val > 70:
        last_signal = "SAT"

    # Composite daily logic (A-type) - simplified:
    # - Check daily 1D series: if yesterday had a green candle and today turned green2, and h4 or 15m condition -> set composite
    composite_signal = None
    try:
        # fetch 1d bars (less frequently) - lightweight
        df1d = yf.download(sym, period="10d", interval="1d", auto_adjust=True, progress=False)
        if not df1d.empty and "Close" in df1d.columns:
            # yesterday index -2, today -1
            if len(df1d) >= 2:
                yesterday_open = df1d["Open"].iloc[-2]
                yesterday_close = df1d["Close"].iloc[-2]
                today_open = df1d["Open"].iloc[-1]
                today_close = df1d["Close"].iloc[-1]
                # yesterday green?
                yesterday_green = yesterday_close > yesterday_open
                today_green = today_close > today_open
                # Count green count across days: if yesterday green and today green -> double green
                if yesterday_green and today_green:
                    # now check 4H/15m indicator on the 15m df (approx using green_11/15)
                    # if any of green_11 or green_15 true -> composite A
                    if green_11 or green_15:
                        composite_signal = "A"
    except Exception:
        pass

    # signal_time in UTC (aware)
    signal_time = datetime.now(timezone.utc).isoformat()

    out = {
        "symbol": sym.replace(".IS",""),
        "current_price": current_price,
        "yfinance_price": current_price,
        "trend": trend,
        "last_signal": last_signal,
        "signal_time": signal_time,
        "daily_change": f"%{daily_change}",
        "volume": volume,
        "RSI": float(rsi_val),
        "support_break": support_break,
        "resistance_break": resistance_break,
        "green_mum_11": green_11,
        "green_mum_15": green_15,
        "three_peak_break": three_peak,
        "ma_breaks": ma_breaks,
        "ma_values": ma_vals,
        "composite_signal": composite_signal
    }
    return out

def fetch_bist_data():
    syms = get_bist_symbols()
    results = []
    for s in syms:
        try:
            item = fetch_one_symbol(s)
            results.append(item)
        except Exception as e:
            print("[fetch_bist] fetch error for", s, e)
            # small sleep to avoid hammering
            time.sleep(0.05)
            continue
        time.sleep(0.15)  # throttle
    return results
