import os
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from utils import calculate_rsi, moving_averages, detect_three_peaks, detect_support_resistance_break, FALLBACK_SYMBOLS

yf.pdr_override = False  # avoid pandas_datareader override

def get_bist_symbols():
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        js = r.json()
        out = []
        for item in js:
            code = item.get("indexCode", "")
            comps = item.get("components", []) or []
            if code in ("XU030", "XU100"):
                for c in comps:
                    sym = c.get("symbol")
                    if sym:
                        out.append(sym + ".IS")
        out = list(dict.fromkeys(out))
        if out:
            print("[fetch_bist] got symbols from API:", len(out))
            return out
    except Exception as e:
        print("[fetch_bist] get_bist_symbols fallback:", e)
    return FALLBACK_SYMBOLS

def fetch_single(symbol):
    try:
        df = yf.download(symbol, period="7d", interval="15m", auto_adjust=True, progress=False)
        if df.empty or "Close" not in df.columns:
            print("[fetch_bist] empty data for", symbol)
            return None

        df = df.dropna(how="all")
        df["RSI"] = calculate_rsi(df["Close"])
        rsi_val = float(df["RSI"].iloc[-1])
        mas = moving_averages(df, windows=[20,50,100,200])
        current_price = float(df["Close"].iloc[-1])
        ma_breaks = {}
        for w, mv in mas.items():
            if mv is None:
                ma_breaks[f"MA{w}"] = None
            else:
                ma_breaks[f"MA{w}"] = "price_above" if current_price > mv else "price_below"

        ma20 = df["Close"].rolling(20,min_periods=1).mean()
        ma50 = df["Close"].rolling(50,min_periods=1).mean()
        if len(ma20) > 1 and len(ma50) > 1:
            if ma20.iloc[-2] <= ma50.iloc[-2] and ma20.iloc[-1] > ma50.iloc[-1]:
                ma_breaks["20x50"] = "golden_cross"
            elif ma20.iloc[-2] >= ma50.iloc[-2] and ma20.iloc[-1] < ma50.iloc[-1]:
                ma_breaks["20x50"] = "death_cross"

        support_break, resistance_break = detect_support_resistance_break(df, lookback=20)
        three_peak = detect_three_peaks(df["Close"])
        df_idx = df.copy()
        df_idx["hour"] = df_idx.index.tz_localize(None).hour
        green_11 = any((df_idx["hour"] == 11) & (df_idx["Close"] > df_idx["Open"]))
        green_15 = any((df_idx["hour"] == 15) & (df_idx["Close"] > df_idx["Open"]))
        daily_change = round((current_price - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2) if len(df) > 0 else 0
        volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns and not pd.isna(df["Volume"].iloc[-1]) else None

        # Uyumlu key’ler
        return {
            "symbol": symbol.replace(".IS",""),
            "current_price": current_price,
            "RSI": rsi_val,
            "volume": volume,
            "daily_change": daily_change,
            "ma_breaks": ma_breaks,
            "support_break": support_break,
            "resistance_break": resistance_break,
            "three_peak": three_peak,
            "green_1100": green_11,
            "green_1500": green_15
        }

    except Exception as e:
        print("[fetch_bist] fetch error for", symbol, e)
        return None

def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []
    for sym in symbols:
        rec = fetch_single(sym)
        if rec:
            results.append(rec)
        time.sleep(0.15)
    return results
