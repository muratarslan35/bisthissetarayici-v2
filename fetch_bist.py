import yfinance as yf
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
import os

# Fallback static list (eğer otomatik API başarısız olursa kullanılır)
FALLBACK_SYMBOLS = [
    "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","KOZAL.IS","KRDMD.IS","THYAO.IS",
    "ASELS.IS","SISE.IS","BIMAS.IS","VAKBN.IS","PETKM.IS","TCELL.IS","TOASO.IS",
    "EREGL.IS","TUPRS.IS","SASA.IS"
]

def get_bist_symbols():
    """
    Önce isyatirim API'sinden almaya çalışır.
    Başarısız olursa fallback list döner.
    """
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        resp = requests.get(url, timeout=7)
        resp.raise_for_status()
        js = resp.json()
        bist30 = []
        bist100 = []
        for item in js:
            code = item.get("indexCode")
            comps = item.get("components") or []
            if code == "XU030":
                bist30 = [x["symbol"] + ".IS" for x in comps if x.get("symbol")]
            elif code == "XU100":
                bist100 = [x["symbol"] + ".IS" for x in comps if x.get("symbol")]
        if bist30 or bist100:
            # combine unique
            combined = list(dict.fromkeys(bist30 + bist100))
            print("[fetch_bist] got symbols from API:", len(combined))
            return combined
    except Exception as e:
        print("get_bist_symbols fallback:", e)
    print("[fetch_bist] using fallback symbols.")
    return FALLBACK_SYMBOLS


# Teknik hesaplamalar
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)  # default nötr
    return rsi

def detect_three_peaks(series):
    try:
        peaks_idx = (series > series.shift(1)) & (series > series.shift(-1))
        peaks = series[peaks_idx]
        if len(peaks) < 3:
            return False
        last_three = peaks.index[-3:]
        max_peak = series.loc[last_three].max()
        return series.iloc[-1] > max_peak
    except Exception:
        return False

def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []
    for sym in symbols:
        try:
            # Her bir sembolü ayrı çekmek daha güvenli
            df = yf.download(sym, period="7d", interval="15m", auto_adjust=True, progress=False)
            # df bazen MultiIndex veya Series dönebilir; normalize et
            if df is None or df.empty:
                raise ValueError("empty df")
            # Ensure DataFrame has Close column
            if "Close" not in df.columns:
                # if columns are single-level with symbol suffix
                cols = [c for c in df.columns if "Close" in str(c)]
                if cols:
                    df.columns = [c if "Close" in str(c) else c for c in df.columns]
                else:
                    raise ValueError("No Close column")
            # drop rows without close
            if "Close" in df.columns:
                df = df.dropna(subset=["Close"])
            if df.empty:
                raise ValueError("no close data")

            # Compute indicators
            df["RSI"] = calculate_rsi(df["Close"])
            ma20 = df["Close"].rolling(window=20, min_periods=1).mean().iloc[-1]
            ma50 = df["Close"].rolling(window=50, min_periods=1).mean().iloc[-1]
            ma100 = df["Close"].rolling(window=100, min_periods=1).mean().iloc[-1]
            ma200 = df["Close"].rolling(window=200, min_periods=1).mean().iloc[-1]

            current_price = float(df["Close"].iloc[-1])
            last_rsi = float(df["RSI"].iloc[-1])

            # Signals
            last_signal = "Yok"
            if last_rsi < 20:
                last_signal = "AL"
            elif last_rsi > 80:
                last_signal = "SAT"

            trend = "Yukarı" if ma20 > ma50 else "Aşağı"

            # MA break detection (current price crossing above recent MA)
            ma_breaks = {
                "20": current_price > ma20 and df["Close"].iloc[-2] <= df["Close"].rolling(20).mean().iloc[-2] if len(df) > 1 else False,
                "50": current_price > ma50 and df["Close"].iloc[-2] <= df["Close"].rolling(50).mean().iloc[-2] if len(df) > 1 else False,
                "100": current_price > ma100 and df["Close"].iloc[-2] <= df["Close"].rolling(100).mean().iloc[-2] if len(df) > 1 else False,
                "200": current_price > ma200 and df["Close"].iloc[-2] <= df["Close"].rolling(200).mean().iloc[-2] if len(df) > 1 else False,
            }

            # support/resistance naive example: use rolling min/max over last N for demo
            support = float(df["Close"].rolling(window=50, min_periods=1).min().iloc[-1])
            resistance = float(df["Close"].rolling(window=50, min_periods=1).max().iloc[-1])
            support_break = current_price < support * 0.995  # 0.5% below support
            resistance_break = current_price > resistance * 1.005  # 0.5% above resistance

            # three peak
            three_peak = detect_three_peaks(df["Close"])

            # green candles at 11 and 15 (local timezone assumptions: use index hour)
            try:
                idx = df.index
                hours = idx.hour
                green_11 = any((hours == 11) & (df["Close"] > df["Open"]))
                green_15 = any((hours == 15) & (df["Close"] > df["Open"]))
            except Exception:
                green_11 = False
                green_15 = False

            daily_change = round((df["Close"].iloc[-1] - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2) if len(df) > 1 else 0.0
            volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns and not df["Volume"].isna().all() else None

            results.append({
                "symbol": sym.replace(".IS", ""),
                "current_price": current_price,
                "RSI": last_rsi,
                "last_signal": last_signal,
                "trend": trend,
                "daily_change": f"%{daily_change}",
                "volume": volume,
                "green_mum_11": green_11,
                "green_mum_15": green_15,
                "three_peak_break": three_peak,
                "support": support,
                "resistance": resistance,
                "support_break": support_break,
                "resistance_break": resistance_break,
                "ma_breaks": ma_breaks,
                "signal_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

        except Exception as e:
            # keep fetching going; log the symbol that failed
            print(f"[fetch_bist] fetch error for {sym}", e)
            continue

    return results
