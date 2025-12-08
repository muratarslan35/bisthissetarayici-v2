import yfinance as yf
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time

def get_bist_symbols():
    try:
        url = "https://api.isyatirim.com.tr/index/indexsectorperformance"
        js = requests.get(url, timeout=5).json()
        bist30 = []
        bist100 = []
        for item in js:
            if item.get("indexCode") == "XU030":
                bist30 = [x["symbol"] + ".IS" for x in item["components"]]
            if item.get("indexCode") == "XU100":
                bist100 = [x["symbol"] + ".IS" for x in item["components"]]
        return list(set(bist30 + bist100))
    except:
        return []

def get_tradingview_price(symbol):
    # Bu fonksiyon, TradingView'den fiyat alır.
    # Gerçek API veya scraping kullanmalısınız.
    # Örnek: aşağıdaki, 'sayfa yapısına göre düzenlenmeli.
    try:
        url = f"https://www.tradingview.com/symbols/{symbol}/"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, 'html.parser')
        price_elem = soup.find('div', {'class': 'tv-symbol-price-quote__value'})
        if price_elem:
            price_text = price_elem.text.replace(',', '').replace('$', '').strip()
            return float(price_text)
        else:
            return None
    except:
        return None

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def fetch_bist_data():
    symbols = get_bist_symbols()
    results = []
    try:
        data = yf.download(tickers=symbols, period="5d", interval="15m")
    except:
        return results
    for symbol in symbols:
        try:
            df = data['Close'][symbol]
            if df.empty:
                continue
            current_price = df.iloc[-1]
            tv_price = get_tradingview_price(symbol)
            if tv_price is None:
                tv_price = current_price
            sapma_pct = ((tv_price - current_price) / current_price) * 100
            high = data['High'][symbol].max()
            low = data['Low'][symbol].min()
            daily_change = high - low
            ma20 = df.rolling(window=20).mean().iloc[-1]
            ma50 = df.rolling(window=50).mean().iloc[-1]
            trend = "Yukarı" if ma20 > ma50 else "Aşağı"
            rsi_series = calculate_rsi(df)
            rsi = rsi_series.iloc[-1] if not rsi_series.empty else None
            results.append({
                "symbol": symbol.replace('.IS',''),
                "current_price": float(current_price),
                "yfinance_price": float(current_price),
                "tv_price": tv_price,
                "sapma_pct": sapma_pct,
                "trend": trend,
                "last_signal": "AL" if rsi and rsi < 30 else "SAT" if rsi and rsi > 70 else "Yok",
                "signal_time": datetime.now().strftime("%H:%M"),
                "daily_change": round((current_price - low) / low * 100, 2),
                "volume": int(data['Volume'][symbol].iloc[-1]),
            })
        except:
            continue
    return results
