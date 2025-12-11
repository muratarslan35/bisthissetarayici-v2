import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ============================================================
#  FALLBACK LISTESİ
# ============================================================
FALLBACK_SYMBOLS = [
    "ASELS","THYAO","TUPRS","PETKM","KRDMD","KRDMA","KRDMB","GARAN","YKBNK","AKBNK",
    "ISCTR","SISE","EREGL","BIMAS","SAHOL","TOASO","HEKTS","SASA","FROTO","KOZAL",
    "KOZAA","PENTA","PGSUS","ALARK","ARCLK","ENJSA","AKSA","KCHOL","VESTL","KONTR",
    "TTRAK","AEFES","ODAS","ASELS","CLEBI","KORDS","TAVHL","TKFEN","ULKER","AGHOL",
    "ISMEN","OTKAR","SELEC","SOKM","BAGFS","GESAN","QUAGR","YUNSA","YEOTK","AYDEM",
    # Kullanıcı eklemeleri
    "TEHOL","PEKGY","TUKAS","TERA"
]

# ============================================================
#  INDICATOR FONKSİYONLARI
# ============================================================

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def moving_average(series, window):
    return series.rolling(window).mean()

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def volume_ma(volume, window=20):
    return volume.rolling(window).mean()

# ============================================================
#  Günlük Sinyal Kontrolü (1D)
# ============================================================
def check_daily_alignment(df):
    """
    3 koşullu A tipi TEMİZ sinyal:
    1) Günlük RSI > 50
    2) Günlük EMA20 üzerinde kapanış
    3) Günlük hacim MA20 üzerinde olmalı
    """
    if len(df) < 30:
        return False  # yeterli veri yok

    df["EMA20"] = ema(df["close"], 20)
    df["RSI"] = rsi(df["close"])
    df["VMA20"] = volume_ma(df["volume"], 20)

    last = df.iloc[-1]

    cond1 = last["RSI"] > 50
    cond2 = last["close"] > last["EMA20"]
    cond3 = last["volume"] > last["VMA20"]

    return cond1 and cond2 and cond3


# ============================================================
#  Ana 5D Sinyal Motoru
# ============================================================
def process_signals(df_5m, df_daily):
    """
    df_5m → 5 dakikalık veri
    df_daily → günlük veri

    Tüm algoritmalar korunmuştur.
    Günlük sinyal filtrelemesi (A tipi 3 uyum) entegredir.
    """

    results = {
        "buy_signals": [],
        "sell_signals": [],
        "daily_alignment": False,
    }

    # ------------------------------------------------------------
    # Günlük senkron kontrolü
    # ------------------------------------------------------------
    results["daily_alignment"] = check_daily_alignment(df_daily)

    # ------------------------------------------------------------
    # 5 dakikalık göstergeler
    # ------------------------------------------------------------
    df = df_5m.copy()

    df["EMA20"] = ema(df["close"], 20)
    df["EMA50"] = ema(df["close"], 50)
    df["RSI"] = rsi(df["close"], 14)
    df["VMA20"] = volume_ma(df["volume"], 20)
    df["MA9"] = moving_average(df["close"], 9)
    df["MA21"] = moving_average(df["close"], 21)

    # Golden cross
    df["golden_cross"] = (df["EMA20"] > df["EMA50"]) & (df["EMA20"].shift() <= df["EMA50"].shift())

    # Bear cross
    df["bear_cross"] = (df["EMA20"] < df["EMA50"]) & (df["EMA20"].shift() >= df["EMA50"].shift())

    # Breakout
    df["breakout"] = (df["close"] > df["close"].rolling(20).max().shift())

    last = df.iloc[-1]

    # ============================================================
    #  BUY / SELL SİNYALLERİ
    # ============================================================

    # ---------------------- BUY --------------------------------
    buy_conditions = [
        last["RSI"] > 55,
        last["EMA20"] > last["EMA50"],
        last["volume"] > last["VMA20"],
        last["close"] > last["MA9"],
        last["close"] > last["MA21"],
        results["daily_alignment"] == True,    # Günlük filtre ON
    ]

    if all(buy_conditions):
        results["buy_signals"].append({
            "type": "BUY",
            "reason": "RSI>55 + EMA20>EMA50 + Volume>VMA20 + MA uyum + Günlük A Tipi Uyum",
            "price": float(last["close"])
        })

    # ---------------------- SELL --------------------------------
    sell_conditions = [
        last["RSI"] < 45,
        last["EMA20"] < last["EMA50"],
        last["close"] < last["MA9"],
        last["close"] < last["MA21"],
    ]

    if all(sell_conditions):
        results["sell_signals"].append({
            "type": "SELL",
            "reason": "RSI<45 + EMA20<EMA50 + MA kırılım",
            "price": float(last["close"])
        })

    # Golden Cross Sinyali
    if last["golden_cross"]:
        results["buy_signals"].append({"type": "BUY", "reason": "GOLDEN CROSS", "price": float(last["close"])})

    # Bearish Cross Sinyali
    if last["bear_cross"]:
        results["sell_signals"].append({"type": "SELL", "reason": "BEAR CROSS", "price": float(last["close"])})

    # Breakout
    if last["breakout"] and results["daily_alignment"]:
        results["buy_signals"].append({"type": "BUY", "reason": "BREAKOUT + Günlük Uyum", "price": float(last["close"])})

    return results
