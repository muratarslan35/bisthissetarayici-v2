from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Telegram
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_ID = 661794787

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except:
        pass

def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı – BIST tarama aktif!")

def update_loop():
    global LATEST_DATA
    while True:
        try:
            data = fetch_bist_data()

            for h in data:
                mesaj = ""

                if h["RSI"] < 20:
                    mesaj += f"🔻 {h['symbol']} RSI < 20!\n"
                if h["RSI"] > 80:
                    mesaj += f"🔺 {h['symbol']} RSI > 80!\n"

                if h["last_signal"] == "AL":
                    mesaj += f"🟢 {h['symbol']} AL sinyali!\n"
                if h["last_signal"] == "SAT":
                    mesaj += f"🔴 {h['symbol']} SAT sinyali!\n"

                if h["green_mum_11"]:
                    mesaj += f"🟢 11:00 yeşil mum!\n"
                if h["green_mum_15"]:
                    mesaj += f"🟢 15:00 yeşil mum!\n"

                if mesaj:
                    mesaj += (
                        f"Fiyat: {h['current_price']} TL\n"
                        f"Değişim: {h['daily_change']}\n"
                        f"Hacim: {h['volume']}\n"
                        f"Trend: {h['trend']}\n"
                        f"Sinyal Zamanı: {h['signal_time']}"
                    )
                    telegram_send(mesaj)

            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}

        except Exception as e:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}

        time.sleep(60)

@app.route("/")
def index():
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
