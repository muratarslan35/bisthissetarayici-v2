# fetch_bist.py
import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# BIST sembolleri
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
                    if c.get("symbol"):
                        bist30.append(c["symbol"] + ".IS")

            if item.get("indexCode") == "XU100":
                for c in item.get("components", []):
                    if c.get("symbol"):
                        bist100.append(c["symbol"] + ".IS")

        merged = list(dict.fromkeys(bist30 + bist100))
        if merged:
            return merged

    except:
        pass

    return [
        "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","THYAO.IS","ASELS.IS","KRDMD.IS","PETKM.IS",
        "EREGL.IS","TOASO.IS","TUPRS.IS","KOCAS.IS","BIMAS.IS","VAKBN.IS","SISE.IS","KOZAL.IS"
    ]

# RSI
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period, min_periods=1).mean()
    avg_loss = loss.rolling(period, min_periods=1).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)

# Hareketli ortalamalar
def moving_averages(df, windows=[20,50,100,200]):
    ma = {}
    for w in windows:
        ma[w] = df["Close"].rolling(w, min_periods=1).mean().iloc[-1]
    return ma

# Destek / direnç
def detect_support_resistance_break(df, lookback=20):
    recent_low = df["Low"].rolling(lookback).min().iloc[-2]
    recent_high = df["High"].rolling(lookback).max().iloc[-2]

    current = df["Close"].iloc[-1]

    return current < recent_low, current > recent_high

# 3 tepe
def detect_three_peaks(close_series):
    peaks = (close_series > close_series.shift(1)) & (close_series > close_series.shift(-1))
    idx = close_series[peaks].index
    if len(idx) < 3:
        return False

    last3 = close_series.loc[idx[-3:]].max()
    return close_series.iloc[-1] > last3

# Ana fetch
def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []

    for sym in symbols:
        try:
            df = yf.download(sym, period="7d", interval="15m", auto_adjust=True, progress=False)

            df = df.dropna()
            df["RSI"] = calculate_rsi(df["Close"])
            rsi = float(df["RSI"].iloc[-1])

            mas = moving_averages(df)
            current = float(df["Close"].iloc[-1])

            # MA kırılım isimleri düzeltildi
            ma_breaks = {}
            for w, mv in mas.items():
                ma_breaks[f"MA{w}"] = "Üstünde" if current > mv else "Altında"

            # Destek / direnç
            support, resistance = detect_support_resistance_break(df)

            # 3 tepe
            three_peak = detect_three_peaks(df["Close"])

            # 11 - 15 mumları
            df["hour"] = df.index.hour
            green_11 = any((df["hour"] == 11) & (df["Close"] > df["Open"]))
            green_15 = any((df["hour"] == 15) & (df["Close"] > df["Open"]))

            # Trend
            trend = "Yukarı" if mas[20] > mas[50] else "Aşağı"

            daily_change = round((current - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2)

            volume = int(df["Volume"].iloc[-1])

            # RSI sinyal
            sig = "Yok"
            if rsi < 20:
                sig = "Aşırı Düşük RSI"
            elif rsi > 80:
                sig = "Aşırı Yüksek RSI"

            results.append({
                "symbol": sym.replace(".IS",""),
                "current_price": current,
                "trend": trend,
                "RSI": rsi,
                "daily_change": f"%{daily_change}",
                "volume": volume,
                "support_break": support,
                "resistance_break": resistance,
                "three_peak_break": three_peak,
                "green_mum_11": green_11,
                "green_mum_15": green_15,
                "ma_values": mas,
                "ma_breaks": ma_breaks,
                "last_signal": sig,
                "signal_time": datetime.utcnow().isoformat()
            })

        except Exception as e:
            print("Fetch error:", sym, e)
            continue

        time.sleep(0.2)

    return results
