import yfinance as yf
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time

symbols = ["BIST30", "BIST100"]

def get_tradingview_price(symbol):
    try:
        url = f"https://www.tradingview.com/symbols/{symbol}/"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.find("div", {"class": "tv-symbol-price-quote__value"})
        if el:
            return float(el.text.replace(",", "").strip())
    except:
        pass
    return None

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def fetch_bist_data():
    result = []
    data = yf.download(tickers=symbols, period="1d", interval="1m")

    for sym in symbols:
        df = data["Close"][sym]
        if df.empty:
            continue

        current_price = df.iloc[-1]
        tv_price = get_tradingview_price(sym) or current_price
        high = data["High"][sym].max()
        low = data["Low"][sym].min()

        ma20 = df.rolling(20).mean().iloc[-1]
        ma50 = df.rolling(50).mean().iloc[-1]
        trend = "Yukarı" if ma20 > ma50 else "Aşağı"

        rsi_series = calculate_rsi(df)
        rsi = rsi_series.iloc[-1] if not rsi_series.empty else None

        # 3 tepe
        def detect_three_peaks(close):
            peaks = (close > close.shift(1)) & (close > close.shift(-1))
            p = close[peaks].index
            if len(p) < 3:
                return False
            last3 = close[p[-3:]]
            if close.iloc[-1] > last3.max():
                return True
            return False

        three_peak = detect_three_peaks(df)

        # 11 ve 15 yeşil mum
        now = datetime.now()

        def hour_green(h):
            st = datetime.combine(now.date(), time(h, 0))
            en = st + timedelta(hours=4)
            part = df[(df.index >= st) & (df.index < en)]
            if len(part) < 2:
                return False
            return part.iloc[-1] > part.iloc[0]

        green_11 = hour_green(11)
        green_15 = hour_green(15)

        last_signal = "AL" if (rsi and rsi < 20) else "SAT" if (rsi and rsi > 80) else "Yok"

        result.append({
            "symbol": sym,
            "current_price": current_price,
            "tv_price": tv_price,
            "daily_change": high - low,
            "trend": trend,
            "volume": data["Volume"][sym].iloc[-1],
            "RSI": rsi,
            "three_peak_break": three_peak,
            "green_mum_11": green_11,
            "green_mum_15": green_15,
            "last_signal": last_signal,
            "signal_time": now.strftime("%Y-%m-%d %H:%M:%S")
        })

    return result
