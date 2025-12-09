import yfinance as yf
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
import os
import math

# --- BIST30 + BIST100 Hisselerini Otomatik Çeken Fonksiyon ---
def get_bist_symbols():
    """
    Tries to fetch BIST30 and BIST100 components from i̇ş yatrım (public API used before).
    Returns list of symbols formatted for yfinance (e.g. 'GARAN.IS').
    """
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        js = resp.json()
        bist30 = []
        bist100 = []
        for item in js:
            code = item.get("indexCode", "")
            if code == "XU030":
                # item["components"] expected list of dicts with "symbol"
                bist30 = [x["symbol"] + ".IS" for x in item.get("components", []) if x.get("symbol")]
            if code == "XU100":
                bist100 = [x["symbol"] + ".IS" for x in item.get("components", []) if x.get("symbol")]
        symbols = list(dict.fromkeys(bist30 + bist100))  # preserve order, unique
        if not symbols:
            raise Exception("Empty components from isyatirim API")
        return symbols
    except Exception as e:
        # fallback: small hardcoded list or read from a cached file if needed
        # return a small safe default to avoid total failure
        print("get_bist_symbols fallback:", e)
        fallback = [
            # some common symbols as fallback
            "GARAN.IS","AKBNK.IS","YKBNK.IS","ASELS.IS","THYAO.IS"
        ]
        return fallback

# RSI calculation
def rsi(df_close, period=14):
    delta = df_close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series

def detect_three_peaks(series):
    # simple local peak detection, requires at least 3 local peaks and current price > max of last 3 peaks
    if series.empty or len(series) < 5:
        return False
    peaks = (series > series.shift(1)) & (series > series.shift(-1))
    peak_idx = series[peaks].index
    if len(peak_idx) < 3:
        return False
    last_three = peak_idx[-3:]
    max_peak = series.loc[last_three].max()
    current_price = series.iloc[-1]
    return current_price > max_peak

def detect_ma_breaks(df, price):
    """
    Check crossing of price over MA20/50/100/200 comparing previous close to previous MA and current.
    Returns dict {'MA-20': True/False, ...}
    """
    ma_breaks = {}
    mas = {"MA-20": 20, "MA-50": 50, "MA-100": 100, "MA-200": 200}
    for label, window in mas.items():
        if len(df["Close"]) < window+1:
            ma_breaks[label] = False
            continue
        ma = df["Close"].rolling(window=window).mean()
        prev_close = df["Close"].iloc[-2]
        prev_ma = ma.iloc[-2]
        curr_close = price
        curr_ma = ma.iloc[-1]
        # Break if previously below MA and now above MA (cross up) or vice versa
        crossed_up = (prev_close <= prev_ma) and (curr_close > curr_ma)
        crossed_down = (prev_close >= prev_ma) and (curr_close < curr_ma)
        ma_breaks[label] = crossed_up or crossed_down
    return ma_breaks

def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []
    for symbol in symbols:
        try:
            # Fetch last 10 days at 15m resolution to compute indicators robustly
            df = yf.download(symbol, period="10d", interval="15m", auto_adjust=True, progress=False)
            if df is None or df.empty:
                continue

            # Ensure datetime index timezone naive (simplify)
            df = df.sort_index()
            close = df["Close"]

            # RSI
            rsi_series = rsi(close, period=14)
            last_rsi = None
            if not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]):
                last_rsi = float(rsi_series.iloc[-1])

            current_price = float(close.iloc[-1])
            # MA trend
            ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
            ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
            ma100 = close.rolling(100).mean().iloc[-1] if len(close) >= 100 else None
            ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

            trend = "Yukarı" if (ma20 and ma50 and ma20 > ma50) else "Aşağı"

            # 11 and 15 hour green candles (we inspect hourly windows inside 4H blocks like original)
            # We approximate by checking any 15m candles between target hour and +4h for green overall
            now = datetime.now()
            def is_green_window(target_hour):
                start = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
                end = start + timedelta(hours=4)
                df_window = df[(df.index >= start) & (df.index < end)]
                if df_window.empty:
                    return False
                open_price = df_window["Open"].iloc[0]
                close_price = df_window["Close"].iloc[-1]
                return close_price > open_price

            green_11 = is_green_window(11)
            green_15 = is_green_window(15)

            # detect 3-peak
            three_peak = detect_three_peaks(close)

            # detect MA breaks
            ma_breaks = detect_ma_breaks(df, current_price)

            # support/resistance break placeholders (user can extend)
            support_break = False
            resistance_break = False
            # simple daily change & volume
            daily_change = round(((current_price - df["Close"].iloc[0]) / df["Close"].iloc[0]) * 100, 2) if df["Close"].iloc[0] != 0 else 0
            volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns and not pd.isna(df["Volume"].iloc[-1]) else None

            # last_signal using RSI thresholds as before (preserve original logic)
            last_signal = "Yok"
            if last_rsi is not None:
                if last_rsi < 20:
                    last_signal = "AL"
                elif last_rsi > 80:
                    last_signal = "SAT"

            results.append({
                "symbol": symbol.replace(".IS",""),
                "current_price": current_price,
                "yfinance_price": current_price,
                "tv_price": None,
                "sapma_pct": None,
                "trend": trend,
                "last_signal": last_signal,
                "signal_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "daily_change": f"%{daily_change}",
                "volume": volume,
                "RSI": last_rsi,
                "support": None,
                "resistance": None,
                "support_break": support_break,
                "resistance_break": resistance_break,
                "green_mum_11": green_11,
                "green_mum_15": green_15,
                "three_peak_break": three_peak,
                "ma_breaks": ma_breaks  # MA-20/50/100/200 break flags
            })
        except Exception as e:
            # skip symbol on error but continue
            print("fetch error for", symbol, e)
            continue

    return results
