from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Telegram ayarları
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_ID = 661794787


def telegram_send(text):
    for cid in [CHAT_ID]:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload, timeout=5)
        except:
            pass


def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")


def update_loop():
    while True:
        try:
            data = fetch_bist_data()

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

                # RSI
                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {his['symbol']} RSI {rsi:.2f} < 20!\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {his['symbol']} RSI {rsi:.2f} > 80!\n"

                # AL – SAT sinyalleri
                if last_signal == "AL":
                    mesaj += f"🟢 {his['symbol']} AL sinyali!\n"
                elif last_signal == "SAT":
                    mesaj += f"🔴 {his['symbol']} SAT sinyali!\n"

                if support_break:
                    mesaj += f"🟢 {his['symbol']} destek kırıldı!\n"
                if resistance_break:
                    mesaj += f"🔴 {his['symbol']} direnç kırıldı!\n"
                if three_peak:
                    mesaj += f"⚠️ {his['symbol']} üç tepe kırılımı gerçekleşti!\n"

                if green_11:
                    mesaj += f"🟢 {his['symbol']} 11:00 yeşil mum!\n"

                if green_15:
                    mesaj += f"🟢 {his['symbol']} 15:00 yeşil mum!\n"

                mesaj += (
                    f"Fiyat: {price} TL\n"
                    f"Günlük değişim: {daily_change}\n"
                    f"Hacim: {volume}\n"
                    f"Trend: {trend}\n"
                    f"Son sinyal: {last_signal}\n"
                    f"Sinyal zamanı: {signal_time}\n"
                    f"RSI: {rsi}\n"
                )

                if mesaj.strip():
                    telegram_send(mesaj)

            with data_lock:
                LATEST_DATA["status"] = "ok"
                LATEST_DATA["timestamp"] = int(time.time())
                LATEST_DATA["data"] = data

        except Exception as e:
            with data_lock:
                LATEST_DATA["status"] = "error"
                LATEST_DATA["error"] = str(e)

        time.sleep(60)


# -------------------------------
# RENDER'DA OTOMATİK BAŞLATMA!
# -------------------------------
@app.before_first_request
def start_background_processes():
    sistem_bildir()

    t = threading.Thread(target=update_loop, daemon=True)
    t.start()

    start_self_ping()


@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")


@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)
