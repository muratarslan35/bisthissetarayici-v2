import yfinance as yf
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
import os
import traceback

# --- BIST30 + BIST100 Hisselerini Otomatik Çeken Fonksiyon ---
def get_bist_symbols():
    """
    Önce isyatirim API'sini dene; başarısızsa fallback olarak küçük bir güvenli liste döndür.
    """
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            js = r.json()
            bist30 = []
            bist100 = []
            for item in js:
                idx = item.get("indexCode")
                comps = item.get("components") or []
                if idx == "XU030":
                    bist30 = [x["symbol"] + ".IS" for x in comps]
                if idx == "XU100":
                    bist100 = [x["symbol"] + ".IS" for x in comps]
            merged = list(dict.fromkeys((bist30 or []) + (bist100 or [])))
            if merged:
                return merged
    except Exception as e:
        # Loglama çağıran taraf yapacak; burada sadece fallback ver
        pass

    # Alternatif: küçük güvenli fallback (popülerler) — uygulama çalışırken bunu genişletebilirsin
    fallback = ["GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","THYAO.IS","ASELS.IS","KCHOL.IS","KRDMD.IS"]
    return fallback


# ---- Teknik analiz hesaplamaları ----
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)  # NaN olanlar için nötr değer
    return rsi


def detect_three_peaks(df_close):
    try:
        peaks_idx = (df_close > df_close.shift(1)) & (df_close > df_close.shift(-1))
        peaks_times = df_close[peaks_idx].index
        if len(peaks_times) < 3:
            return False
        last_three = peaks_times[-3:]
        max_peak = df_close.loc[last_three].max()
        current_price = df_close.iloc[-1]
        return current_price > max_peak
    except Exception:
        return False


def detect_ma_breaks(df_close):
    """
    Returns dict like {"MA20": True/False, "MA50": ..., "MA100":..., "MA200":...}
    Detect if price crosses above last MA (simple check).
    """
    res = {}
    for window in [20, 50, 100, 200]:
        if len(df_close) >= window:
            ma = df_close.rolling(window=window).mean().iloc[-1]
            prev_ma = df_close.rolling(window=window).mean().iloc[-2] if len(df_close) >= window+1 else ma
            current = df_close.iloc[-1]
            prev_close = df_close.iloc[-2] if len(df_close) >= 2 else df_close.iloc[-1]
            # Cross above: previous close <= prev_ma and current > ma
            crossed = (prev_close <= prev_ma) and (current > ma)
            res[f"MA-{window}"] = bool(crossed)
        else:
            res[f"MA-{window}"] = False
    return res


def fetch_bist_data(symbols):
    """
    symbols: list of strings like "GARAN.IS"
    returns list of dicts with all metrics
    """
    results = []
    for symbol in symbols:
        try:
            # download 10 days of 15m bars to have enough history for indicators
            df = yf.download(symbol, period="10d", interval="15m", auto_adjust=True, progress=False)
            if df is None or df.empty:
                continue

            # ensure datetime index timezone-naive
            df = df.sort_index()
            df = df.dropna(subset=["Close"])  # close olmayan satırları çıkar

            df['RSI'] = calc_rsi(df['Close'])
            rsi_val = float(df['RSI'].iloc[-1]) if not df['RSI'].empty else None

            current_price = float(df['Close'].iloc[-1])
            ma_breaks = detect_ma_breaks(df['Close'])
            last_signal = None
            if rsi_val is not None:
                if rsi_val < 20: last_signal = "AL"
                elif rsi_val > 80: last_signal = "SAT"

            # green candles at hours 11 and 15 (using 1h resolution if available; using 15m here)
            try:
                hours = df.copy()
                hours['hour'] = hours.index.hour
                green_11 = not hours[hours['hour'] == 11].empty and (hours[hours['hour'] == 11]['Close'].iloc[-1] > hours[hours['hour'] == 11]['Open'].iloc[0])
                green_15 = not hours[hours['hour'] == 15].empty and (hours[hours['hour'] == 15]['Close'].iloc[-1] > hours[hours['hour'] == 15]['Open'].iloc[0])
            except Exception:
                green_11 = False
                green_15 = False

            three_peak = detect_three_peaks(df['Close'])

            # simplistic support/resistance detection placeholders (you can replace with your algo)
            support = float(df['Low'].rolling(window=20, min_periods=1).min().iloc[-1])
            resistance = float(df['High'].rolling(window=20, min_periods=1).max().iloc[-1])
            support_break = current_price < support * 0.995  # 0.5% below support
            resistance_break = current_price > resistance * 1.005  # 0.5% above resistance

            daily_change = round(((current_price - float(df['Close'].iloc[0])) / float(df['Close'].iloc[0])) * 100, 2)
            volume = int(df['Volume'].iloc[-1]) if 'Volume' in df.columns else None

            results.append({
                "symbol": symbol.replace(".IS", ""),
                "current_price": current_price,
                "yfinance_price": current_price,
                "trend": "Yukarı" if df['Close'].rolling(window=20).mean().iloc[-1] < current_price else "Aşağı",
                "last_signal": last_signal or "Yok",
                "signal_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "daily_change": f"%{daily_change}",
                "volume": volume,
                "RSI": rsi_val,
                "support": support,
                "resistance": resistance,
                "support_break": bool(support_break),
                "resistance_break": bool(resistance_break),
                "green_mum_11": bool(green_11),
                "green_mum_15": bool(green_15),
                "three_peak_break": bool(three_peak),
                "ma_breaks": ma_breaks
            })

        except Exception as e:
            # return a helpful error for logs but continue
            # calling code will log exceptions
            # continue to next symbol
            # optionally append an error entry if you want
            # print stack to logs
            try:
                import traceback
                traceback.print_exc()
            except:
                pass
            continue

    return results
