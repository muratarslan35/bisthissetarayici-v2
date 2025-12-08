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
CHAT_ID_LIST = [661794787]  # Çoklu ID destekli

# ----------------------------------------------------
# Telegram bildirim fonksiyonu
# ----------------------------------------------------
def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in CHAT_ID_LIST:
        try:
            requests.post(url, json={
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML"
            })
        except:
            pass

# ----------------------------------------------------
# Sistem açılış bildirimi
# ----------------------------------------------------
def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")

# ----------------------------------------------------
# Sürekli tarama döngüsü
# ----------------------------------------------------
def update_loop():
    global LATEST_DATA

    while True:
        try:
            data = fetch_bist_data()

            # Tüm hisselerdeki algoritmaları kontrol et
            for his in data:
                mesaj = ""

                rsi = his.get("RSI")
                last_signal = his.get("last_signal")
                support = his.get("support_break")
                resistance = his.get("resistance_break")
                green11 = his.get("green_mum_11")
                green15 = his.get("green_mum_15")
                three_peak = his.get("three_peak_break")
                price = his.get("current_price")
                daily = his.get("daily_change")
                volume = his.get("volume")
                trend = his.get("trend")
                sigtime = his.get("signal_time")

                # RSI sinyali
                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {his['symbol']} RSI <20 ({rsi:.2f})\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {his['symbol']} RSI >80 ({rsi:.2f})\n"

                # AL / SAT
                if last_signal == "AL":
                    mesaj += f"🟢 {his['symbol']} AL sinyali!\n"
                if last_signal == "SAT":
                    mesaj += f"🔴 {his['symbol']} SAT sinyali!\n"

                # Destek / direnç kırılımı
                if support:
                    mesaj += f"🟢 Destek kırıldı: {his['symbol']}\n"
                if resistance:
                    mesaj += f"🔴 Direnç kırıldı: {his['symbol']}\n"

                # 3 tepe kırılımı
                if three_peak:
                    mesaj += f"⚠️ Üç tepe kırıldı: {his['symbol']}\n"

                # Mum takibi
                if green11:
                    mesaj += f"🟢 4H 11:00 yeşil mum → {his['symbol']}\n"
                if green15:
                    mesaj += f"🟢 4H 15:00 yeşil mum → {his['symbol']}\n"

                # Genel bilgiler
                mesaj += f"Fiyat: {price}\n"
                mesaj += f"Günlük değişim: {daily}\n"
                mesaj += f"Hacim: {volume}\n"
                mesaj += f"Trend: {trend}\n"
                mesaj += f"Sinyal: {last_signal}\n"
                mesaj += f"Zaman: {sigtime}\n"
                mesaj += f"RSI: {rsi}\n"

                # Bildirim gönder
                if mesaj.strip():
                    telegram_send(mesaj)

            # Dashboard verisini güncelle
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

# ----------------------------------------------------
# Arka plan işlemlerini başlat
# ----------------------------------------------------
def start_background_jobs():
    sistem_bildir()
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()

start_background_jobs()

# ----------------------------------------------------
# ROUTES
# ----------------------------------------------------
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# ----------------------------------------------------
# Gunicorn tarafından otomatik çalışır
# ----------------------------------------------------
if __name__ != "__main__":
    start_background_jobs()
