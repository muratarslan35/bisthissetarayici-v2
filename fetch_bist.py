# fetch_bist.py
import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from utils import FALLBACK_SYMBOLS, calculate_rsi, moving_averages, detect_three_peaks, detect_support_resistance_break, to_tr_timezone

# Try to get BIST30/BIST100 list from API, else fallback
def get_bist_symbols():
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        js = r.json()
        bist30, bist100 = [], []
        for item in js:
            if item.get("indexCode") == "XU030":
                for c in item.get("components", []):
                    sym = c.get("symbol")
                    if sym:
                        bist30.append(sym + ".IS")
            if item.get("indexCode") == "XU100":
                for c in item.get("components", []):
                    sym = c.get("symbol")
                    if sym:
                        bist100.append(sym + ".IS")
        combined = list(dict.fromkeys(bist30 + bist100))
        if combined:
            return combined
    except Exception as e:
        print("get_bist_symbols fallback:", e)
    # fallback provided list
    return FALLBACK_SYMBOLS.copy()

def safe_extract_single_ticker(df, sym):
    # yfinance sometimes returns multi-index; normalize
    if isinstance(df.columns, pd.MultiIndex):
        # try to extract (Close, sym) etc.
        try:
            df_t = pd.DataFrame({
                "Open": df[("Open", sym)],
                "High": df[("High", sym)],
                "Low": df[("Low", sym)],
                "Close": df[("Close", sym)],
                "Volume": df[("Volume", sym)]
            })
            return df_t
        except Exception:
            return None
    else:
        # normal columns
        if "Close" not in df.columns:
            return None
        cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
        df_t = df[cols].copy()
        return df_t

def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []
    for sym in symbols:
        try:
            df = yf.download(sym, period="7d", interval="15m", auto_adjust=True, progress=False)
            df_t = safe_extract_single_ticker(df, sym)
            if df_t is None or df_t.empty:
                print("[fetch_bist] fetch error for", sym, "empty or no Close")
                continue

            # clean
            df_t = df_t.dropna(how="all")
            if df_t.empty or "Close" not in df_t.columns:
                print("[fetch_bist] empty or no Close after cleanup for", sym)
                continue

            # calculations
            df_t["RSI"] = calculate_rsi(df_t["Close"])
            rsi_val = float(df_t["RSI"].iloc[-1])

            mas = moving_averages(df_t, windows=[20,50,100,200])
            current_price = float(df_t["Close"].iloc[-1])

            ma_breaks = {}
            for w, mv in mas.items():
                if mv is None:
                    ma_breaks[f"MA{w}"] = None
                else:
                    ma_breaks[f"MA{w}"] = "price_above" if current_price > mv else "price_below"

            # golden/death cross
            try:
                ma20 = df_t["Close"].rolling(20,min_periods=1).mean()
                ma50 = df_t["Close"].rolling(50,min_periods=1).mean()
                if len(ma20) > 1 and len(ma50) > 1:
                    if ma20.iloc[-2] <= ma50.iloc[-2] and ma20.iloc[-1] > ma50.iloc[-1]:
                        ma_breaks["20x50"] = "golden_cross"
                    elif ma20.iloc[-2] >= ma50.iloc[-2] and ma20.iloc[-1] < ma50.iloc[-1]:
                        ma_breaks["20x50"] = "death_cross"
            except Exception:
                pass

            support_break, resistance_break = detect_support_resistance_break(df_t, lookback=20)
            three_peak = detect_three_peaks(df_t["Close"])

            # hours detection on 15m bars -> mark if any green in hour=11 or hour=15
            df_t["hour"] = df_t.index.tz_localize(None).hour
            green_11 = any((df_t["hour"] == 11) & (df_t["Close"] > df_t["Open"]))
            green_15 = any((df_t["hour"] == 15) & (df_t["Close"] > df_t["Open"]))

            trend = "Yukarı" if mas.get(20) and mas.get(50) and mas[20] > mas[50] else "Aşağı"
            daily_change = round((current_price - df_t["Close"].iloc[0]) / df_t["Close"].iloc[0] * 100, 2) if len(df_t)>0 else 0
            volume = int(df_t["Volume"].iloc[-1]) if "Volume" in df_t.columns and not pd.isna(df_t["Volume"].iloc[-1]) else None

            last_signal = "Yok"
            if rsi_val < 20:
                last_signal = "AL"
            elif rsi_val > 80:
                last_signal = "SAT"

            results.append({
                "symbol": sym.replace(".IS",""),
                "current_price": current_price,
                "yfinance_price": current_price,
                "trend": trend,
                "last_signal": last_signal,
                "signal_time": to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
                "daily_change": f"%{daily_change}",
                "volume": volume,
                "RSI": rsi_val,
                "support_break": bool(support_break),
                "resistance_break": bool(resistance_break),
                "green_mum_11": bool(green_11),
                "green_mum_15": bool(green_15),
                "three_peak_break": bool(three_peak),
                "ma_breaks": ma_breaks,
                "ma_values": mas
            })
        except Exception as e:
            print("[fetch_bist] fetch error for", sym, e)
            continue
        time.sleep(0.15)
    return results
