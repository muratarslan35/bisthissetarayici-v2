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

# ==============================
# 📌 Telegram Ayarları
# ==============================

TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

# Birden fazla ID eklemek için liste halinde yaz
TELEGRAM_CHAT_IDS = [
    661794787,   # Senin ID
    # 123456789, # Başka kişi eklemek istersen buraya ID ekle
]

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in TELEGRAM_CHAT_IDS:
        try:
            requests.post(url, json={
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML"
            })
        except:
            pass

# Sistem açıldığında mesaj
def sistem_bildir():
    telegram_send("🤖 Sistem Render üzerinde başlatıldı ve aktif!")

# ==============================
# 📌 Veri Çekme Döngüsü
# ==============================

def update_loop():
    global LATEST_DATA

    while True:
        try:
            data = fetch_bist_data()

            for his in data:
                msg = ""

                symbol = his["symbol"]
                rsi = his["RSI"]
                last_signal = his["last_signal"]

                # RSI MESAJLARI
                if rsi is not None:
                    if rsi < 20:
                        msg += f"🔻 {symbol} RSI {rsi:.2f} < 20\n"
                    elif rsi > 80:
                        msg += f"🔺 {symbol} RSI {rsi:.2f} > 80\n"

                # AL / SAT
                if last_signal == "AL":
                    msg += f"🟢 {symbol} AL sinyali!\n"
                elif last_signal == "SAT":
                    msg += f"🔴 {symbol} SAT sinyali!\n"

                # 3 tepe kırılımı
                if his["three_peak_break"]:
                    msg += f"⚠️ {symbol} üç tepe kırılımı!\n"

                # 11 ve 15 yeşil mum
                if his["green_mum_11"]:
                    msg += f"🟢 {symbol} 4H - 11:00 yeşil mum.\n"
                if his["green_mum_15"]:
                    msg += f"🟢 {symbol} 4H - 15:00 yeşil mum.\n"

                # Fiyat bilgileri
                msg += f"Fiyat: {his['current_price']} TL\n"
                msg += f"Günlük değişim: {his['daily_change']}\n"
                msg += f"Hacim: {his['volume']}\n"
                msg += f"Trend: {his['trend']}\n"
                msg += f"RSI: {rsi}\n"
                msg += f"Sinyal zamanı: {his['signal_time']}\n"

                if msg.strip():
                    telegram_send(msg)

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

# ==============================
# 📌 ROUTES
# ==============================

@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# ==============================
# 📌 MAIN
# ==============================

if __name__ == "__main__":
    sistem_bildir()
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()

    # Render portu otomatik okur artık — kesin çözüm
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
