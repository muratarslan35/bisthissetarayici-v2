import yfinance as yf
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time

symbols = ["BIST30", "BIST100"]  # Güncel sembol isimleri

def get_tradingview_price(symbol):
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
    result = []
    data = yf.download(tickers=symbols, period="1d", interval="1m")
    for sym in symbols:
        df = data['Close'][sym]
        if df.empty:
            continue

        current_price = df.iloc[-1]
        tv_price = get_tradingview_price(sym)
        if tv_price is None:
            tv_price = current_price
        sapma_pct = ((tv_price - current_price) / current_price) * 100

        high = data['High'][sym].max()
        low = data['Low'][sym].min()
        daily_change = high - low

        ma20 = df.rolling(window=20).mean().iloc[-1]
        ma50 = df.rolling(window=50).mean().iloc[-1]
        trend = "Yukarı" if ma20 > ma50 else "Aşağı"

        rsi_series = calculate_rsi(df)
        rsi = rsi_series.iloc[-1] if not rsi_series.empty else None

        # Günlük mumlar ve saat 11, 15
        now = datetime.now()
        day_start = datetime.combine(now.date(), time(0,0))
        today_mum = df[df.index >= day_start]
        green_mum_count = len(today_mum[today_mum > today_mum.shift(1)])
        def is_green_at_hour(target_hour):
            start_time = datetime.combine(now.date(), time(target_hour, 0))
            end_time = start_time + timedelta(hours=4)
            df_hour = data['Close'][sym][(data['Close'][sym].index >= start_time) & (data['Close'][sym].index < end_time)]
            if df_hour.empty:
                return False
            open_price = df_hour.iloc[0]
            close_price = df_hour.iloc[-1]
            return close_price > open_price
        green_11 = is_green_at_hour(11)
        green_15 = is_green_at_hour(15)
        # 3 tepe
        def detect_three_peaks(df_close):
            peaks_idx = (df_close > df_close.shift(1)) & (df_close > df_close.shift(-1))
            peaks_times = df_close[peaks_idx].index
            if len(peaks_times) < 3:
                return False
            last_three = peaks_times[-3:]
            max_peak = df_close.loc[last_three].max()
            current_price = df_close.iloc[-1]
            if current_price > max_peak:
                return True
            return False
        three_peak = detect_three_peaks(df)
        # Sinyal
        last_signal = "Yok"
        if rsi is not None:
            if rsi < 20:
                last_signal = "AL"
            elif rsi > 80:
                last_signal = "SAT"
        result.append({
            "symbol": sym,
            "current_price": current_price,
            "yfinance_price": current_price,
            "tv_price": tv_price,
            "sapma_pct": sapma_pct,
            "trend": trend,
            "last_signal": last_signal,
            "signal_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "daily_change": daily_change,
            "volume": data['Volume'][sym].iloc[-1] if 'Volume' in data else None,
            "RSI": rsi,
            "support": None,
            "resistance": None,
            "support_break": False,
            "resistance_break": False,
            "green_mum_11": green_11,
            "green_mum_15": green_15,
            "three_peak_break": three_peak
        })
    return result
