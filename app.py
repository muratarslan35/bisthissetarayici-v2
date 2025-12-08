from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
import os

from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

# GLOBAL VERİ
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# ===== TELEGRAM =====
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

# Birden fazla chat_id eklemek için LİSTE halinde tutuyoruz
CHAT_IDS = [
    661794787,   # Murat
    # 123456789, # Arkadaş 1 (koyabilirsin)
    # 987654321, # Arkadaş 2 (koyabilirsin)
]

def telegram_send(text):
    for cid in CHAT_IDS:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload, timeout=5)
        except:
            pass

# Sistem açılış bildirimi
def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve çalışıyor!")

# ===== ANA LOOP =====
def update_loop():
    global LATEST_DATA

    while True:
        try:
            data = fetch_bist_data()

            # Her hisse için kontrol
            for his in data:
                mesaj = ""

                rsi = his.get("RSI")
                symbol = his["symbol"]

                # RSI
                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {symbol} RSI {rsi:.2f} < 20!\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {symbol} RSI {rsi:.2f} > 80!\n"

                # Sinyal
                if his.get("last_signal") == "AL":
                    mesaj += f"🟢 {symbol} AL sinyali!\n"
                elif his.get("last_signal") == "SAT":
                    mesaj += f"🔴 {symbol} SAT sinyali!\n"

                # 15 / 11 mumları
                if his.get("green_mum_11"):
                    mesaj += f"🟢 {symbol} 4H saat 11'de yeşil mum!\n"
                if his.get("green_mum_15"):
                    mesaj += f"🟢 {symbol} 4H saat 15'te yeşil mum!\n"

                # 3 tepe
                if his.get("three_peak_break"):
                    mesaj += f"⚠️ {symbol} üç tepe kırılımı\n"

                # Her zaman gönderilen bilgiler
                mesaj += (
                    f"Fiyat: {his.get('current_price')} TL\n"
                    f"Değişim: {his.get('daily_change')}\n"
                    f"Hacim: {his.get('volume')}\n"
                    f"Trend: {his.get('trend')}\n"
                    f"Sinyal zamanı: {his.get('signal_time')}\n"
                )

                if mesaj:
                    telegram_send(mesaj)

            # ----- EN ÖNEMLİ KISIM (senin dosyanda YOKTU!) -----
            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "data": data
                }
            # ----------------------------------------------------

        except Exception as e:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}

        time.sleep(60)

# ===== ROUTES =====
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# ===== MAIN =====
if __name__ == "__main__":
    sistem_bildir()
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()

    # Render otomatik PORT belirlediği için elle 10000 YAZMA !!!
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
