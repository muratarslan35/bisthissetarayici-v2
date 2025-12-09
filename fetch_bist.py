import yfinance as yf
import requests
import pandas as pd
import numpy as np
from datetime import datetime


# --- BIST30 + BIST100 Hisselerini Otomatik Çeker ---
def get_bist_symbols():
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        js = requests.get(url, timeout=5).json()

        bist30, bist100 = [], []

        for item in js:
            if item.get("indexCode") == "XU030":
                bist30 = [x["symbol"] + ".IS" for x in item["components"]]
            if item.get("indexCode") == "XU100":
                bist100 = [x["symbol"] + ".IS" for x in item["components"]]

        return list(set(bist30 + bist100))

    except:
        return []


# --- RSI hesabı ---
def rsi(df, period=14):
    delta = df["Close"].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean()
    avg_loss = pd.Series(loss).rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def fetch_bist_data():

    symbols = get_bist_symbols()
    results = []

    for symbol in symbols:
        try:
            df = yf.download(symbol, period="5d", interval="15m", auto_adjust=True)
            if df.empty:
                continue

            # RSI
            df["RSI"] = rsi(df)
            last_rsi = float(df["RSI"].iloc[-1])
            price = float(df["Close"].iloc[-1])

            # Trend
            ma20 = df["Close"].rolling(20).mean().iloc[-1]
            trend = "Yükseliş" if price > ma20 else "Düşüş"

            # AL / SAT algoritması
            last_signal = "AL" if last_rsi < 30 else "SAT" if last_rsi > 70 else None

            # Yeşil mum tespiti
            df["hour"] = df.index.hour
            green_11 = (df[(df["hour"] == 11) & (df["Close"] > df["Open"])]).shape[0] > 0
            green_15 = (df[(df["hour"] == 15) & (df["Close"] > df["Open"])]).shape[0] > 0

            # Günlük değişim
            daily_change = round((price - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2)

            # Hacim
            volume = int(df["Volume"].iloc[-1])

            # Diğer algoritmaların boş placeholder'ları
            support_break = False
            resistance_break = False
            three_peak_break = False

            results.append({
                "symbol": symbol.replace(".IS", ""),
                "current_price": price,
                "RSI": last_rsi,
                "last_signal": last_signal,
                "trend": trend,
                "daily_change": f"%{daily_change}",
                "volume": volume,
                "green_mum_11": green_11,
                "green_mum_15": green_15,
                "support_break": support_break,
                "resistance_break": resistance_break,
                "three_peak_break": three_peak_break,
                "signal_time": datetime.now().strftime("%H:%M:%S"),
            })

        except Exception:
            continue

    return results
