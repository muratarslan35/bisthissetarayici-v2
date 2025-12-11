# fetch_bist.py
import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from utils import calculate_rsi, moving_averages, detect_three_peaks, detect_support_resistance_break, FALLBACK_SYMBOLS

yf.pdr_override = False  # avoid pandas_datareader override

# Try to fetch BIST30/100 symbols from API; if fails use FALLBACK_SYMBOLS
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
    # fallback
    return FALLBACK_SYMBOLS

def fetch_single(symbol):
    try:
        df = yf.download(symbol, period="7d", interval="15m", auto_adjust=True, progress=False)
        # handle multiindex
        if isinstance(df.columns, pd.MultiIndex):
            # extract columns for this symbol if present
            if ("Close", symbol) in df.columns:
                df = pd.DataFrame({
                    "Open": df[("Open", symbol)],
                    "High": df[("High", symbol)],
                    "Low": df[("Low", symbol)],
                    "Close": df[("Close", symbol)],
                    "Volume": df[("Volume", symbol)]
                })
            else:
                print("[fetch_bist] unexpected multiindex for", symbol)
                return None
        # ensure columns
        if "Close" not in df.columns or df.empty:
            print("[fetch_bist] empty or no Close for", symbol)
            return None
        # clean
        df = df.dropna(how="all")
        if df.empty:
            return None
        # compute indicators
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
        # golden/death simple
        try:
            ma20 = df["Close"].rolling(20,min_periods=1).mean()
            ma50 = df["Close"].rolling(50,min_periods=1).mean()
            if len(ma20) > 1 and len(ma50) > 1:
                if ma20.iloc[-2] <= ma50.iloc[-2] and ma20.iloc[-1] > ma50.iloc[-1]:
                    ma_breaks["20x50"] = "golden_cross"
                elif ma20.iloc[-2] >= ma50.iloc[-2] and ma20.iloc[-1] < ma50.iloc[-1]:
                    ma_breaks["20x50"] = "death_cross"
        except Exception:
            pass
        support_break, resistance_break = detect_support_resistance_break(df, lookback=20)
        three_peak = detect_three_peaks(df["Close"])
        df_idx = df.copy()
        df_idx["hour"] = df_idx.index.tz_localize(None).hour
        green_11 = any((df_idx["hour"] == 11) & (df_idx["Close"] > df_idx["Open"]))
        green_15 = any((df_idx["hour"] == 15) & (df_idx["Close"] > df_idx["Open"]))
        daily_change = round((current_price - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2) if len(df) > 0 else 0
        volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns and not pd.isna(df["Volume"].iloc[-1]) else None
        last_signal = "Yok"
        if rsi_val < 30:
            last_signal = "AL"
        elif rsi_val > 70:
            last_signal = "SAT"
        return {
            "symbol": symbol.replace(".IS",""),
            "current_price": current_price,
            "yfinance_price": current_price,
            "trend": "Yukarı" if mas.get(20,0) and mas.get(50,0) and mas[20] > mas[50] else "Aşağı",
            "last_signal": last_signal,
            "signal_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "daily_change": f"%{daily_change}",
            "volume": volume,
            "RSI": rsi_val,
            "support_break": support_break,
            "resistance_break": resistance_break,
            "green_mum_11": green_11,
            "green_mum_15": green_15,
            "three_peak_break": three_peak,
            "ma_breaks": ma_breaks,
            "ma_values": mas
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
        time.sleep(0.15)  # rate-limit safety
    return results
