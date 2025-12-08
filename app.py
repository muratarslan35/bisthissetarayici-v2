from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
import os
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# --- Telegram ---
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

# Birden fazla CHAT_ID ekleyebilirsin
CHAT_IDS = [
    661794787,   # Murat
    # 123456789, # örnek kişi
    # 987654321, # başka kişi
]

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in CHAT_IDS:
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload, timeout=5)
        except:
            pass

def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")

# --- Veri Döngüsü ---
def update_loop():
    global LATEST_DATA

    while True:
        try:
            data = fetch_bist_data()

            # Her hisse için bildirim kontrolü
            for his in data:
                msg = ""

                symbol = his["symbol"]
                rsi = his["RSI"]
                last_signal = his["last_signal"]

                if rsi is not None:
                    if rsi < 20:
                        msg += f"🔻 {symbol} RSI {rsi:.2f} < 20!\n"
                    elif rsi > 80:
                        msg += f"🔺 {symbol} RSI {rsi:.2f} > 80!\n"

                if last_signal == "AL":
                    msg += f"🟢 {symbol} AL sinyali!\n"
                elif last_signal == "SAT":
                    msg += f"🔴 {symbol} SAT sinyali!\n"

                if his["support_break"]:
                    msg += f"🟢 {symbol} destek kırıldı!\n"
                if his["resistance_break"]:
                    msg += f"🔴 {symbol} direnç kırıldı!\n"

                if his["three_peak_break"]:
                    msg += f"⚠️ {symbol} üç tepe kırılımı gerçekleşti!\n"

                if his["green_mum_11"]:
                    msg += f"🟢 {symbol} 4H saat 11 yeşil mum!\n"
                if his["green_mum_15"]:
                    msg += f"🟢 {symbol} 4H saat 15 yeşil mum!\n"

                # Ek bilgiler
                msg += (
                    f"Fiyat: {his['current_price']} TL\n"
                    f"Günlük değişim: {his['daily_change']}\n"
                    f"Hacim: {his['volume']}\n"
                    f"Trend: {his['trend']}\n"
                    f"Sinyal zamanı: {his['signal_time']}\n"
                    f"RSI: {rsi}\n"
                )

                if msg.strip():
                    telegram_send(msg)

            # GLOBAL GÜNCELLEME (önceki hatanın nedeni buydu)
            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "data": data
                }

        except Exception as e:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}

        time.sleep(60)

@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

if __name__ == "__main__":
    sistem_bildir()
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
