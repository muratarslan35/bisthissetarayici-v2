from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# --- TELEGRAM AYARLARI ---
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_IDS = [661794787]   # Liste halinde

def telegram_send(text):
    for cid in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "HTML"})
        except:
            pass


# 🟢 Sistem açılış bildirimi
def sistem_baslat_bildirimi():
    telegram_send("🤖 Sistem Render üzerinde başlatıldı!")


# 🟢 ANA LOOP – BİST TARAMA + TELEGRAM BİLDİRİM
def update_loop():
    while True:
        try:
            data = fetch_bist_data()

            for his in data:
                mesaj = ""

                rsi = his["RSI"]
                symbol = his["symbol"]

                # --- SENİN TÜM ALGORİTMALARIN KALDI ---
                if rsi < 20:
                    mesaj += f"🔻 {symbol} RSI < 20\n"
                elif rsi > 80:
                    mesaj += f"🔺 {symbol} RSI > 80\n"

                if his["last_signal"] == "AL":
                    mesaj += f"🟢 {symbol} AL sinyali!\n"

                if his["last_signal"] == "SAT":
                    mesaj += f"🔴 {symbol} SAT sinyali!\n"

                if his["green_mum_11"]:
                    mesaj += f"🟢 {symbol} 11:00 yeşil mum.\n"

                if his["green_mum_15"]:
                    mesaj += f"🟢 {symbol} 15:00 yeşil mum.\n"

                # fiyat — hacim — trend
                mesaj += (
                    f"Fiyat: {his['current_price']} TL\n"
                    f"Günlük değişim: {his['daily_change']}\n"
                    f"Hacim: {his['volume']}\n"
                    f"Trend: {his['trend']}\n"
                    f"Sinyal: {his['last_signal']}\n"
                    f"RSI: {rsi}\n"
                )

                # BİLDİRİM GÖNDER
                if mesaj.strip():
                    telegram_send(mesaj)

            # dashboard datası
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}

        except Exception as e:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}

        time.sleep(60)


# 🟢 THREADLER OTOMATİK BAŞLATILIYOR
def start_background_tasks():
    sistem_baslat_bildirimi()

    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=start_self_ping, daemon=True).start()


# Render worker boot oldu → thread başlat
start_background_tasks()


# --- ROUTES ---
@app.route("/")
def index():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
