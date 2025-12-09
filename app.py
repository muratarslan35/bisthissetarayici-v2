import os
import threading
import time
import json
import requests
from flask import Flask, jsonify, send_from_directory
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__, static_folder="static")

# --- GLOBAL / DEFAULT KONFIG ---
# tercih: ortam değişkenlerinden al, yoksa verilen varsayılanları kullan
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN",
    "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")  # öneri: deploy'ta ENV kullan
CHAT_IDS_ENV = os.getenv("CHAT_IDS", "661794787")  # virgülle ayrılmış
CHAT_IDS = [int(x.strip()) for x in CHAT_IDS_ENV.split(",") if x.strip().isdigit()]

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# basit logger
def log(*args, **kwargs):
    print("[APP]", *args, **kwargs, flush=True)

# Telegram gönderimi (durumu logla)
def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            r = requests.post(url, json=payload, timeout=8)
            log("Telegram ->", cid, r.status_code, r.text[:200])
        except Exception as e:
            log("Telegram error ->", cid, str(e))

# Background döngüsü: fetch ve bildirimler
def update_loop():
    global LATEST_DATA
    log("Background update_loop starting...")
    # başlangıç bildirimi
    try:
        telegram_send("🤖 Sistem başlatıldı ve tarama başlatıldı.")
    except Exception as e:
        log("Telegram başlangıç hatası:", e)

    while True:
        try:
            data = fetch_bist_data()  # list of dicts
            # Algoritma: her hisse için kontrol ve telegram mesajı oluşturma
            for item in data:
                mesaj = ""
                symbol = item.get("symbol")
                # RSI
                rsi = item.get("RSI")
                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {symbol} RSI {rsi:.2f} < 20\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {symbol} RSI {rsi:.2f} > 80\n"
                # Sinyal (AL/SAT)
                last_signal = item.get("last_signal")
                if last_signal == "AL":
                    mesaj += f"🟢 {symbol} AL sinyali\n"
                elif last_signal == "SAT":
                    mesaj += f"🔴 {symbol} SAT sinyali\n"
                # MA kırılımları (MA20/50/100/200)
                ma_breaks = item.get("ma_breaks", {})
                for mname, mb in ma_breaks.items():
                    if mb == "price_above":
                        mesaj += f"⬆️ {symbol} fiyat {mname} üzerinde (kırıldı)\n"
                    elif mb == "price_below":
                        mesaj += f"⬇️ {symbol} fiyat {mname} altında\n"
                    elif mb == "golden_cross":
                        mesaj += f"✨ {symbol} Golden Cross: {mname}\n"
                    elif mb == "death_cross":
                        mesaj += f"💀 {symbol} Death Cross: {mname}\n"

                # Support / Resistance / 3 tepe
                if item.get("support_break"):
                    mesaj += f"🟢 {symbol} destek kırıldı\n"
                if item.get("resistance_break"):
                    mesaj += f"🔴 {symbol} direnç kırıldı\n"
                if item.get("three_peak_break"):
                    mesaj += f"⚠️ {symbol} üç tepe kırılımı!\n"

                # Saat 11 / 15 yeşil mum
                if item.get("green_mum_11"):
                    mesaj += f"🟢 {symbol} 4H saat 11'de yeşil mum oluştu\n"
                if item.get("green_mum_15"):
                    mesaj += f"🟢 {symbol} 4H saat 15'te yeşil mum oluştu\n"

                # Genel bilgi
                mesaj += f"Fiyat: {item.get('current_price')} TL\n"
                mesaj += f"Günlük değişim: {item.get('daily_change')}\n"
                mesaj += f"Hacim: {item.get('volume')}\n"
                mesaj += f"Trend: {item.get('trend')}\n"
                mesaj += f"Son sinyal: {last_signal}\n"
                mesaj += f"Sinyal zamanı: {item.get('signal_time')}\n"

                # Eğer mesaj varsa gönder
                if mesaj.strip():
                    telegram_send(mesaj)

            # LATEST_DATA güncelle
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}

        except Exception as e:
            log("update_loop exception:", e)
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}
        # bekle
        time.sleep(int(os.getenv("UPDATE_INTERVAL", "60")))

# Background başlatma (gunicorn ile uyumlu, ilk istek geldiğinde başlat)
_started = False
@app.before_request
def start_background():
    global _started
    if not _started:
        _started = True
        log("Starting background thread from before_request...")
        threading.Thread(target=update_loop, daemon=True).start()
        # self ping (eğer SELF_URL tanımlıysa)
        start_self_ping()
        log("Self-ping thread started (if SELF_URL set).")

# ROUTES
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# Local run için (geliştirme)
if __name__ == "__main__":
    # local test: start update loop directly
    try:
        threading.Thread(target=update_loop, daemon=True).start()
        start_self_ping()
    except Exception as e:
        log("Local start error:", e)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
