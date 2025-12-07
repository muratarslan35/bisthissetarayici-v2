from flask import Flask, jsonify, send_from_directory 
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Bildirim fonksiyonları
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_IDS = [661794787]  # Çoklu kişiler ekleyebilirsiniz

def telegram_send(text):
    for cid in CHAT_IDS:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload)
        except:
            pass

def check_and_notify(his):
    symbol = his.get("symbol")
    rsi = his.get("RSI")
    last_signal = his.get("last_signal")
    support_break = his.get("support_break")
    resistance_break = his.get("resistance_break")
    green_11 = his.get("green_mum_11")
    green_15 = his.get("green_mum_15")
    three_peak = his.get("three_peak_break")
    price = his.get("current_price")
    daily_change = his.get("daily_change")
    volume = his.get("volume")
    trend = his.get("trend")
    signal_time = his.get("signal_time")
    message = ""

    # Sapma
    if his.get("sapma_pct") is not None:
        message += f"🔍 {symbol} Sapma: {his['sapma_pct']:.2f}% (Yfinance & TradingView)\n"

    # RSI sınırları
    if rsi is not None:
        if rsi < 20:
            message += f"🔻 {symbol} RSI {rsi:.2f} < 20!\n"
        elif rsi > 80:
            message += f"🔺 {symbol} RSI {rsi:.2f} > 80!\n"

    # Destek/direnç kırılımı
    if support_break:
        message += f"🟢 {symbol} destek kırıldı!\n"
    if resistance_break:
        message += f"🔴 {symbol} direnç kırıldı!\n"

    # 3 tepe kırılımı
    if three_peak:
        message += f"⚠️ {symbol} üç tepe kırılımı gerçekleşti!\n"

    # Sinyal
    if last_signal == "AL":
        message += f"🟢 {symbol} AL sinyali!\n"
    elif last_signal == "SAT":
        message += f"🔴 {symbol} SAT sinyali!\n"

    # Mumlar ve saat 11-15'teki yeşil mumlar
    if green_11:
        message += f"🟢 {symbol} 4H saat 11'de yeşil mum oluştu.\n"
    if green_15:
        message += f"🟢 {symbol} 4H saat 15'te yeşil mum oluştu.\n"

    # Günlük ve saatlik veriler
    message += f"Fiyat: {price} TL\n"
    message += f"Günlük değişim: {daily_change}\n"
    message += f"Hacim: {volume}\n"
    message += f"Trend: {trend}\n"
    message += f"Son sinyal: {last_signal}\n"
    message += f"Sinyal zamanı: {signal_time}\n"
    message += f"RSI: {rsi}\n"

    if message:
        telegram_send(message)

def update_loop():
    while True:
        try:
            data = fetch_bist_data()
            for his in data:
                # RSI ve sapma kontrolü
                rsi = his.get("RSI")
                if rsi is not None:
                    if rsi < 20:
                        telegram_send(f"🔻 {his['symbol']} RSI {rsi:.2f} 20'nin altında!\n")
                    elif rsi > 80:
                        telegram_send(f"🔺 {his['symbol']} RSI {rsi:.2f} 80'in üzerinde!\n")
                # Sapma
                sapma = his.get("sapma_pct")
                if sapma is not None and abs(sapma) > 5:
                    telegram_send(f"🔎 {his['symbol']} Sapma: {sapma:.2f}% (Yfinance & TradingView)\n")
                # Sinyal ve diğer uyarılar
                check_and_notify(his)

            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
        except:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": "Hata oluştu"}
        time.sleep(60)

@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

@app.before_first_request
def sistem_baslangici():
    telegram_send("🤖 Sistem aktif ve çalışıyor!")

if __name__ == "__main__":
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
