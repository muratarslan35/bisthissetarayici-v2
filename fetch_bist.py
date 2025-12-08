import yfinance as yf
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time

symbols = ["BIST30", "BIST100"]

def get_tradingview_price(symbol):
    try:
        url = f"https://www.tradingview.com/symbols/{symbol}/"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        p = soup.find("div", {"class": "tv-symbol-price-quote__value"})
        if p:
            val = p.text.replace(",", "").replace("$", "").strip()
            return float(val)
    except:
        return None
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
        sapma_pct = ((tv_price - current_price) / current_price) * 100

        high = data["High"][sym].max()
        low = data["Low"][sym].min()

        daily_change = high - low

        ma20 = df.rolling(20).mean().iloc[-1]
        ma50 = df.rolling(50).mean().iloc[-1]
        trend = "Yukarı" if ma20 > ma50 else "Aşağı"

        rsi_series = calculate_rsi(df)
        rsi = rsi_series.iloc[-1]

        now = datetime.now()

        # Saat 11 ve 15 mumları
        def green_at_hour(h):
            st = datetime.combine(now.date(), time(h, 0))
            et = st + timedelta(hours=4)
            d = df[(df.index >= st) & (df.index < et)]
            if len(d) < 2:
                return False
            return d.iloc[-1] > d.iloc[0]

        green_11 = green_at_hour(11)
        green_15 = green_at_hour(15)

        # 3 tepe kırılımı
        def three_peaks(close):
            pk = (close > close.shift(1)) & (close > close.shift(-1))
            p = close[pk].index
            if len(p) < 3:
                return False
            last3 = p[-3:]
            return close.iloc[-1] > close.loc[last3].max()

        three_peak = three_peaks(df)

        last_signal = "Yok"
        if rsi < 20:
            last_signal = "AL"
        elif rsi > 80:
            last_signal = "SAT"

        result.append({
            "symbol": sym,
            "current_price": current_price,
            "tv_price": tv_price,
            "sapma_pct": sapma_pct,
            "trend": trend,
            "last_signal": last_signal,
            "signal_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "daily_change": daily_change,
            "volume": data["Volume"][sym].iloc[-1],
            "RSI": rsi,
            "support_break": False,
            "resistance_break": False,
            "green_mum_11": green_11,
            "green_mum_15": green_15,
            "three_peak_break": three_peak
        })

    return result
