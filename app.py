from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)
LATEST_DATA = {"status": "init", "timestamp": None, "data": None}
data_lock = threading.Lock()

# Telegram bilgileri
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_IDS = [661794787]  # istersen liste ekleyebilirsin


def telegram_send(text):
    for cid in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=5)
        except:
            pass


def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")


# --- TARAMA DÖNGÜSÜ ---
def update_loop():
    global LATEST_DATA

    while True:
        try:
            data = fetch_bist_data()

            for his in data:
                mesaj = ""
                symbol = his["symbol"]

                # Tüm algoritmalar
                if his["RSI"] < 20:
                    mesaj += f"🔻 <b>{symbol}</b> RSI {his['RSI']:.2f} < 20!\n"
                if his["RSI"] > 80:
                    mesaj += f"🔺 <b>{symbol}</b> RSI {his['RSI']:.2f} > 80!\n"

                if his["last_signal"] == "AL":
                    mesaj += f"🟢 <b>{symbol}</b> AL sinyali!\n"
                if his["last_signal"] == "SAT":
                    mesaj += f"🔴 <b>{symbol}</b> SAT sinyali!\n"

                if his["support_break"]:
                    mesaj += f"🟢 <b>{symbol}</b> destek kırıldı!\n"
                if his["resistance_break"]:
                    mesaj += f"🔴 <b>{symbol}</b> direnç kırıldı!\n"

                if his["three_peak_break"]:
                    mesaj += f"⚠️ <b>{symbol}</b> üç tepe kırılımı!\n"

                if his["green_mum_11"]:
                    mesaj += f"🟢 11:00 yeşil mum oluştu ({symbol})\n"
                if his["green_mum_15"]:
                    mesaj += f"🟢 15:00 yeşil mum oluştu ({symbol})\n"

                # Fiyat ve özet bilgiler
                mesaj += (
                    f"Fiyat: {his['current_price']} TL\n"
                    f"Günlük Değişim: {his['daily_change']}\n"
                    f"Hacim: {his['volume']}\n"
                    f"Trend: {his['trend']}\n"
                    f"RSI: {his['RSI']:.2f}\n"
                    f"Sinyal Zamanı: {his['signal_time']}\n"
                )

                if mesaj.strip():
                    telegram_send(mesaj)

            # Dashboard güncelle
            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "data": data,
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
