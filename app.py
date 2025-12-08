from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

# Global veri değişkeni
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Telegram bilgileri
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

# Birden fazla chat_id ekleyebilmen için liste
CHAT_IDS = [
    661794787,  # Sen
    # 123456789, # Eklemek istediğin diğer ID'leri buraya yazacaksın
]

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in CHAT_IDS:
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload)
        except:
            pass


def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")


def update_loop():
    global LATEST_DATA  # *** EN ÖNEMLİ KISIM EKLENDİ ***
    while True:
        try:
            data = fetch_bist_data()

            # Tüm hisseleri dolaş ve sinyal varsa telegram gönder
            for his in data:
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

                mesaj = ""

                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {his['symbol']} RSI {rsi:.2f} < 20!\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {his['symbol']} RSI {rsi:.2f} > 80!\n"

                if last_signal == "AL":
                    mesaj += f"🟢 {his['symbol']} AL sinyali!\n"
                elif last_signal == "SAT":
                    mesaj += f"🔴 {his['symbol']} SAT sinyali!\n"

                if support_break:
                    mesaj += f"🟢 {his['symbol']} destek kırıldı!\n"
                if resistance_break:
                    mesaj += f"🔴 {his['symbol']} direnç kırıldı!\n"

                if three_peak:
                    mesaj += f"⚠️ {his['symbol']} üç tepe kırılımı!\n"

                if green_11:
                    mesaj += f"🟢 {his['symbol']} 4H saat 11'de yeşil mum.\n"
                if green_15:
                    mesaj += f"🟢 {his['symbol']} 4H saat 15'te yeşil mum.\n"

                mesaj += f"Fiyat: {price} TL\n"
                mesaj += f"Günlük değişim: {daily_change}\n"
                mesaj += f"Hacim: {volume}\n"
                mesaj += f"Trend: {trend}\n"
                mesaj += f"Sinyal zamanı: {signal_time}\n"
                mesaj += f"RSI: {rsi}\n"

                if mesaj.strip():
                    telegram_send(mesaj)

            # API verisini güncelle
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
