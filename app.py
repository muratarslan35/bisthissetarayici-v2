from flask import Flask, jsonify, 
send_from_directory
import threading
import time
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping
import requests

app = Flask(__name__)
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Buraya kendi ID'leriniz ekleyin
CHAT_IDS = [661794787]  # örnek ID, ekleyebilirsiniz

TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

def telegram_send(text):
    for cid in CHAT_IDS:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": cid, "text": text, "parse_mode": "HTML"})
        except:
            pass

# Sistem başlangıç bildirimi
def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")

# Ana döngü
def update_loop():
    while True:
        try:
            data = fetch_bist_data()
            for his in data:
                mesaj = ""
                rsi = his.get("RSI")
                last_signal = his.get("last_signal")
                support_break = his.get("support_break")
                resistance_break = his.get("resistance_break")
                green_11 = his.get("green_mum_11")
                green_15 = his.get("green_mum_15")
                three_peak = his.get("three_peak_break")
                price = his.get("current_price")
                daily = his.get("daily_change")
                volume = his.get("volume")
                trend = his.get("trend")
                sigtime = his.get("signal_time")

                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {his['symbol']} RSI <20 ({rsi:.2f})\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {his['symbol']} RSI >80 ({rsi:.2f})\n"

                if last_signal == "AL":
                    mesaj += f"🟢 {his['symbol']} AL sinyali!\n"
                if last_signal == "SAT":
                    mesaj += f"🔴 {his['symbol']} SAT sinyali!\n"

                if support_break:
                    mesaj += f"🟢 Destek kırıldı: {his['symbol']}\n"
                if resistance_break:
                    mesaj += f"🔴 Direnç kırıldı: {his['symbol']}\n"
                if three_peak:
                    mesaj += f"⚠️ Üç tepe kırıldı: {his['symbol']}\n"
                if green_11:
                    mesaj += f"🟢 4H 11:00 yeşil mum → {his['symbol']}\n"
                if green_15:
                    mesaj += f"🟢 4H 15:00 yeşil mum → {his['symbol']}\n"

                mesaj += f"Fiyat: {price}\n"
                mesaj += f"Günlük değişim: {daily}\n"
                mesaj += f"Hacim: {volume}\n"
                mesaj += f"Trend: {trend}\n"
                mesaj += f"Sinyal: {last_signal}\n"
                mesaj += f"Zaman: {sigtime}\n"
                mesaj += f"RSI: {rsi}\n"

                if mesaj:
                    telegram_send(mesaj)

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

if __name__ == "__main__":
    sistem_bildir()
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
