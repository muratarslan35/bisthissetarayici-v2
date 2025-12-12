# fetch_bist.py
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone, timedelta
from utils import (
    FALLBACK_SYMBOLS,
    calculate_rsi,
    moving_averages,
    detect_three_peaks
)

yf.pdr_override = False  # yfinance safe mode

# ---------------------------------------------------------
# DESTEK – DİRENÇ HESABI (MODÜL DAHİL)
# ---------------------------------------------------------
def calc_support_resistance(df, lookback=20):
    """
    Basic swing-high / swing-low support & resistance.
    Returns (nearest_support, nearest_resistance)
    """
    if len(df) < lookback + 3:
        return None, None

    highs = df["High"].tail(lookback).values
    lows = df["Low"].tail(lookback).values

    nearest_res = np.max(highs)
    nearest_sup = np.min(lows)

    return float(nearest_sup), float(nearest_res)


# ---------------------------------------------------------
def detect_support_resistance_break(df, lookback=20):
    """
    Detect if last candle broke above/below recent support/resistance.
    """
    sup, res = calc_support_resistance(df, lookback)
    if sup is None:
        return False, False

    last_close = df["Close"].iloc[-1]

    support_break = last_close < sup
    resistance_break = last_close > res

    return support_break, resistance_break


# ---------------------------------------------------------
# TIMEFRAME GETTER
# ---------------------------------------------------------
def get_tf_prices(sym, interval, period="60d"):
    try:
        df = yf.download(sym, interval=interval, period=period, auto_adjust=True, progress=False)
        if not df.empty:
            df = df.dropna()
        return df
    except:
        return pd.DataFrame()


# ---------------------------------------------------------
def get_bist_symbols():
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=6)
        js = r.json()
        symbols = []
        for item in js:
            comps = item.get("components", [])
            for c in comps:
                sym = c.get("symbol")
                if sym:
                    symbols.append(sym if sym.endswith(".IS") else sym + ".IS")

        symbols = list(dict.fromkeys(symbols))
        if symbols:
            return symbols
    except Exception as e:
        print("get_bist_symbols fallback:", e)

    return FALLBACK_SYMBOLS.copy()


# ---------------------------------------------------------
def fetch_one_symbol(sym):
    # 15m ana veri
    df = yf.download(sym, period="7d", interval="15m", auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError("empty df")

    df = df.dropna()

    # RSI
    df["RSI"] = calculate_rsi(df["Close"])
    rsi_val = float(df["RSI"].iloc[-1])

    # MA
    ma_vals = moving_averages(df, [20, 50, 100, 200])
    price = float(df["Close"].iloc[-1])

    # MA durumları Türkçe
    ma_breaks = {}
    for k, v in ma_vals.items():
        if v is None:
            ma_breaks[f"MA{k}"] = None
        else:
            if price > v:
                ma_breaks[f"MA{k}"] = "yukarı kırdı"
            else:
                ma_breaks[f"MA{k}"] = "aşağı kırdı"

    # Golden/Death cross
    try:
        ma20 = df["Close"].rolling(20).mean()
        ma50 = df["Close"].rolling(50).mean()
        if ma20.iloc[-2] <= ma50.iloc[-2] and ma20.iloc[-1] > ma50.iloc[-1]:
            ma_breaks["20x50"] = "golden_cross"
        elif ma20.iloc[-2] >= ma50.iloc[-2] and ma20.iloc[-1] < ma50.iloc[-1]:
            ma_breaks["20x50"] = "death_cross"
    except:
        pass

    # 3 tepeler
    three_peak = detect_three_peaks(df["Close"])

    # 11:00 – 15:00 yeşil mum
    df_local = df.copy()
    df_local["hour"] = df_local.index.tz_localize(None).hour
    green_11 = any((df_local["hour"] == 11) & (df_local["Close"] > df_local["Open"]))
    green_15 = any((df_local["hour"] == 15) & (df_local["Close"] > df_local["Open"]))

    # Hacim
    volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns else None

    # Günlük değişim
    try:
        daily_change = round((price - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2)
    except:
        daily_change = 0.0

    # Temel AL/SAT (RSI)
    last_signal = "AL" if rsi_val < 30 else "SAT" if rsi_val > 70 else None

    # Support – Resistance BREAK
    support_break, resistance_break = detect_support_resistance_break(df, 20)

    # ---------------------------------------------------------
    # Çoklu Timeframe Destek – Direnç (15m - 1H - 4H - 1D)
    # ---------------------------------------------------------
    tf_data = {
        "15m": get_tf_prices(sym, "15m", "7d"),
        "1h": get_tf_prices(sym, "1h", "30d"),
        "4h": get_tf_prices(sym, "4h", "60d"),
        "1d": get_tf_prices(sym, "1d", "1y"),
    }

    tf_sr = {}
    for tf, d in tf_data.items():
        if d.empty:
            tf_sr[tf] = {"support": None, "resistance": None}
        else:
            s, r = calc_support_resistance(d, 20)
            tf_sr[tf] = {"support": s, "resistance": r}

    # Composite A sinyali korundu
    composite_signal = None
    try:
        df1d = tf_data["1d"]
        if not df1d.empty and len(df1d) >= 2:
            y_open, y_close = df1d["Open"].iloc[-2], df1d["Close"].iloc[-2]
            t_open, t_close = df1d["Open"].iloc[-1], df1d["Close"].iloc[-1]
            if y_close > y_open and t_close > t_open:
                if green_11 or green_15:
                    composite_signal = "A"
    except:
        pass

    # Son veri çıktı
    out = {
        "symbol": sym.replace(".IS", ""),
        "current_price": price,
        "yfinance_price": price,
        "RSI": rsi_val,
        "volume": volume,
        "daily_change": f"%{daily_change}",
        "last_signal": last_signal,
        "signal_time": datetime.now(timezone.utc).isoformat(),

        "support_break": support_break,
        "resistance_break": resistance_break,

        "green_mum_11": green_11,
        "green_mum_15": green_15,
        "three_peak_break": three_peak,

        "ma_breaks": ma_breaks,
        "ma_values": ma_vals,

        "multi_tf_sr": tf_sr,     # 15m-1H-4H-1D destek/direnç

        "composite_signal": composite_signal
    }

    return out


# ---------------------------------------------------------
def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []

    for s in symbols:
        try:
            d = fetch_one_symbol(s)
            results.append(d)
        except Exception as e:
            print("[fetch_bist] error", s, e)
        time.sleep(0.12)

    return results
