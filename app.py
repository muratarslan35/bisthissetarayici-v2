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

# TELEGRAM
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

CHAT_IDS = [
    661794787,
    # buraya başka kullanıcı ID ekleyebilirsin
]

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in CHAT_IDS:
        try:
            requests.post(url, json={"chat_id": cid, "text": text}, timeout=5)
        except:
            pass


# ========================
#     BACKGROUND-LOOP
# ========================
def update_loop():
    global LATEST_DATA

    telegram_send("🤖 Sistem başlatıldı (Render aktif)!")

    while True:
        try:
            data = fetch_bist_data()

            # TELEGRAM SİNYAL
            for his in data:
                rsi = his.get("RSI")
                last_signal = his.get("last_signal")

                mesaj = ""

                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🟢 {his['symbol']} RSI < 20 (AL)\n"
                    elif rsi > 80:
                        mesaj += f"🔴 {his['symbol']} RSI > 80 (SAT)\n"

                if last_signal == "AL":
                    mesaj += f"🟩 AL sinyali: {his['symbol']}\n"
                elif last_signal == "SAT":
                    mesaj += f"🟥 SAT sinyali: {his['symbol']}\n"

                if mesaj:
                    telegram_send(mesaj)

            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "data": data
                }

        except Exception as e:
            with data_lock:
                LATEST_DATA = {"status": "error", "err": str(e)}

        time.sleep(60)



# ==========================
#    THREAD AUTO START
# ==========================
# Flask 3 ile "before_first_request" yok, bu yüzden burada başlatıyoruz

def start_background_threads_once():
    """Gunicorn worker açıldığı anda çağırılacak."""
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()


# Bu fonksiyon Flask import edildiğinde 1 kere çalışır
start_background_threads_once()



# ==========================
#       ROUTES
# ==========================
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)
