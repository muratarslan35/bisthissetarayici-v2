import yfinance as yf
import json
import time
import os

# Doğru Yahoo Finance sembolleri
symbols = {
    "BIST30": "XU030.IS",
    "BIST100": "XU100.IS"
}

OUTPUT_FILE = "bist_data.json"

def fetch_data():
    try:
        data = yf.download(
            tickers=list(symbols.values()),
            period="1d",
            interval="1m",
            auto_adjust=False
        )

        latest = {}
        for key, sym in symbols.items():
            try:
                price = float(data["Close"][sym].dropna()[-1])
                latest[key] = price
            except:
                latest[key] = None

        with open(OUTPUT_FILE, "w") as f:
            json.dump(latest, f)

        print("BIST verileri güncellendi:", latest)

    except Exception as e:
        print("Hata oluştu:", e)

if __name__ == "__main__":
    print("BIST fetcher başlatıldı...")
    while True:
        fetch_data()
        time.sleep(60)
