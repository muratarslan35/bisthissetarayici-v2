import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# --- Helper: BIST30 & BIST100 sembollerini çek ---
def get_bist_symbols():
    # 1) Öncelikle API den dene (istasyon değişebilir)
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        js = r.json()
        bist30, bist100 = [], []
        for item in js:
            if item.get("indexCode") == "XU030":
                # components listesi varsa
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
        print("[get_bist_symbols] fallback:", e)

    # 2) fallback: sabit listeden al (en büyükler). İstersen burayı genişlet.
    fallback = [
        "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","THYAO.IS","ASELS.IS","KRDMD.IS","PETKM.IS",
        "EREGL.IS","TOASO.IS","TUPRS.IS","KOCAS.IS","BIMAS.IS","VAKBN.IS","SISE.IS","KOZAL.IS"
    ]
    # append .IS değilse ekle
    return [s if s.endswith(".IS") else s + ".IS" for s in fallback]

# --- RSI hesaplama ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # fillna için
    return rsi.fillna(50)

# --- Basit MA'lar ---
def moving_averages(df, windows=[20,50,100,200]):
    mas = {}
    for w in windows:
        if "Close" in df.columns:
            mas[w] = df["Close"].rolling(window=w, min_periods=1).mean().iloc[-1]
        else:
            mas[w] = None
    return mas

# --- three peaks tespiti (basit) ---
def detect_three_peaks(close_series):
    if close_series.empty or len(close_series) < 5:
        return False
    peaks = (close_series > close_series.shift(1)) & (close_series > close_series.shift(-1))
    peak_idx = close_series[peaks].index
    if len(peak_idx) < 3:
        return False
    last_three = peak_idx[-3:]
    max_peak = close_series.loc[last_three].max()
    current_price = close_series.iloc[-1]
    return current_price > max_peak

# --- destek/direnç kırılım (basit tanım: son N bar'ın en yüksek/düşüğünü kırma) ---
def detect_support_resistance_break(df, lookback=20):
    if "Low" not in df.columns or "High" not in df.columns:
        return False, False
    recent_low = df["Low"].rolling(window=lookback, min_periods=1).min().iloc[-2] if len(df) > 1 else df["Low"].iloc[-1]
    recent_high = df["High"].rolling(window=lookback, min_periods=1).max().iloc[-2] if len(df) > 1 else df["High"].iloc[-1]
    current = df["Close"].iloc[-1]
    support_break = current < recent_low
    resistance_break = current > recent_high
    return support_break, resistance_break

# --- Ana fetch fonksiyonu: her sembol için tüm algoritmaları döner ---
def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []
    for sym in symbols:
        try:
            # yfinance ile tek tek çek (çoklu indirme hatalara yol açabiliyor)
            df = yf.download(sym, period="7d", interval="15m", auto_adjust=True, progress=False)
            # df çoklu ticker yapısı dönebilir; tek ticker için düz
            if isinstance(df.columns, pd.MultiIndex):
                # multiindex varsa Close sütununa eriş
                if ("Close", sym) in df.columns:
                    df_t = pd.DataFrame({
                        "Open": df[("Open", sym)],
                        "High": df[("High", sym)],
                        "Low": df[("Low", sym)],
                        "Close": df[("Close", sym)],
                        "Volume": df[("Volume", sym)]
                    })
                else:
                    # beklenmeyen yapı
                    print("[fetch_bist] unexpected multiindex for", sym)
                    continue
            else:
                # normal yapı
                if "Close" not in df.columns:
                    print("[fetch_bist] no Close for", sym)
                    continue
                df_t = df[["Open","High","Low","Close","Volume"]].copy()

            # dropna güvenli (varsa)
            df_t = df_t.dropna(how="all")
            if df_t.empty or "Close" not in df_t.columns:
                print("[fetch_bist] empty or no Close after cleanup for", sym)
                continue

            # RSI ve MA'lar
            df_t["RSI"] = calculate_rsi(df_t["Close"])
            rsi_val = float(df_t["RSI"].iloc[-1])

            mas = moving_averages(df_t, windows=[20,50,100,200])
            current_price = float(df_t["Close"].iloc[-1])
            # MA kırılım: fiyatın MA üzerinde/altında olması
            ma_breaks = {}
            for w, mv in mas.items():
                if mv is None:
                    ma_breaks[f"MA{w}"] = None
                else:
                    ma_breaks[f"MA{w}"] = "price_above" if current_price > mv else "price_below"

            # golden/death cross örneği (MA20 x MA50)
            try:
                ma20_series = df_t["Close"].rolling(20,min_periods=1).mean()
                ma50_series = df_t["Close"].rolling(50,min_periods=1).mean()
                if ma20_series.iloc[-2] <= ma50_series.iloc[-2] and ma20_series.iloc[-1] > ma50_series.iloc[-1]:
                    ma_breaks["20x50"] = "golden_cross"
                elif ma20_series.iloc[-2] >= ma50_series.iloc[-2] and ma20_series.iloc[-1] < ma50_series.iloc[-1]:
                    ma_breaks["20x50"] = "death_cross"
            except Exception:
                pass

            # support/resistance
            support_break, resistance_break = detect_support_resistance_break(df_t, lookback=20)

            # 3 tepe
            three_peak = detect_three_peaks(df_t["Close"])

            # 11 ve 15 kontrolü (günlük bazda 4H değilse 15m veride saat kontrolü)
            df_t_index = df_t.copy()
            df_t_index["hour"] = df_t_index.index.hour
            green_11 = not df_t_index[(df_t_index["hour"] == 11) & (df_t_index["Close"] <= df_t_index["Open"])].any().any()
            green_11 = any((df_t_index["hour"] == 11) & (df_t_index["Close"] > df_t_index["Open"]))
            green_15 = any((df_t_index["hour"] == 15) & (df_t_index["Close"] > df_t_index["Open"]))

            # trend MA20>MA50 gibi
            trend = "Yukarı" if mas.get(20, 0) and mas.get(50, 0) and mas[20] > mas[50] else "Aşağı"

            daily_change = round((current_price - df_t["Close"].iloc[0]) / df_t["Close"].iloc[0] * 100, 2) if len(df_t) > 0 else 0
            volume = int(df_t["Volume"].iloc[-1]) if "Volume" in df_t.columns and not pd.isna(df_t["Volume"].iloc[-1]) else None

            # basit sinyal: RSI
            last_signal = "Yok"
            if rsi_val < 30:
                last_signal = "AL"
            elif rsi_val > 70:
                last_signal = "SAT"

            results.append({
                "symbol": sym.replace(".IS",""),
                "current_price": current_price,
                "yfinance_price": current_price,
                "trend": trend,
                "last_signal": last_signal,
                "signal_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
            })
        except Exception as e:
            print("[fetch_bist] fetch error for", sym, e)
            continue
        # hafif bekle (yfinance rate limit için)
        time.sleep(0.2)
    return results
