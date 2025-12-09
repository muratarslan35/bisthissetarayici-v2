from flask import Flask, jsonify, send_from_directory, request
import threading
import time
import requests
import os
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping
from zoneinfo import ZoneInfo
from datetime import datetime

app = Flask(__name__)

# ---- CONFIG ----
# (Token burada durur; istersen Render ortam değişkeni ekleyip burada os.getenv ile al)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")

# Bildirim alacak ID'leri (manuell ekleyebilirsin)
CHAT_IDS = [
    661794787,
    # örnek: 123456789, 987654321
]

LATEST_DATA = {"status": "init", "data": None, "timestamp": None}
data_lock = threading.Lock()

# timezone Turkey
TZ = ZoneInfo("Europe/Istanbul")

def tz_now():
    return datetime.now(TZ)

def log(*args, **kwargs):
    print("[APP]", *args, **kwargs)

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            r = requests.post(url, json=payload, timeout=8)
            log("Telegram ->", cid, r.status_code, r.text)
        except Exception as e:
            log("Telegram error ->", cid, e)

# Update loop that fetches data and sends signals
def update_loop():
    global LATEST_DATA
    log("Background update_loop starting...", tz_now().isoformat())
    telegram_send(f"🤖 Sistem başlatıldı ve tarama aktif. ({tz_now().strftime('%Y-%m-%d %H:%M:%S')})")

    while True:
        try:
            data = fetch_bist_data()  # list of dicts
            # send per-symbol messages if conditions met
            for his in data:
                # prepare message parts
                simbol = his.get("symbol")
                rsi = his.get("RSI")
                last_signal = his.get("last_signal")
                support_break = his.get("support_break")
                resistance_break = his.get("resistance_break")
                three_peak = his.get("three_peak_break")
                green_11 = his.get("green_mum_11")
                green_15 = his.get("green_mum_15")
                ma_breaks = his.get("ma_breaks", {})
                ma_values = his.get("ma_values", {})

                msg_lines = []
                if rsi is not None:
                    if rsi < 20:
                        msg_lines.append(f"🔻 {simbol} RSI {rsi:.2f} < 20!")
                    elif rsi > 80:
                        msg_lines.append(f"🔺 {simbol} RSI {rsi:.2f} > 80!")

                if last_signal and last_signal != "Yok":
                    if last_signal == "AL":
                        msg_lines.append(f"🟢 {simbol} AL sinyali!")
                    elif last_signal == "SAT":
                        msg_lines.append(f"🔴 {simbol} SAT sinyali!")

                if support_break:
                    msg_lines.append(f"🟢 {simbol} destek kırıldı!")
                if resistance_break:
                    msg_lines.append(f"🔴 {simbol} direnç kırıldı!")
                if three_peak:
                    msg_lines.append(f"⚠️ {simbol} üç tepe kırılımı gerçekleşti!")

                if green_11:
                    msg_lines.append(f"🟢 {simbol} 11:00'de yeşil mum oluştu.")
                if green_15:
                    msg_lines.append(f"🟢 {simbol} 15:00'te yeşil mum oluştu.")

                # MA kırılımları
                for k, v in ma_breaks.items():
                    if v and v != None:
                        msg_lines.append(f"📈 {simbol} {k}: {v}")

                # ek bilgiler
                msg_lines.append(f"Fiyat: {his.get('current_price')} TL | Trend: {his.get('trend')} | RSI: {rsi}")
                msg_lines.append(f"Günlük değişim: {his.get('daily_change')} | Hacim: {his.get('volume')}")
                msg_lines.append(f"Sinyal zamanı (TR): {his.get('signal_time')}")

                if msg_lines:
                    telegram_send("\n".join(msg_lines))

            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}

        except Exception as e:
            log("update_loop exception:", str(e))
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}
        # döngü aralığı: 60s (gerektirirse değiştir)
        time.sleep(int(os.getenv("SCAN_INTERVAL", "60")))

# Başlatma: Render/Gunicorn ile çalışırken background thread'i 1 kere başlat
started = False
def start_background_once():
    global started
    if not started:
        started = True
        t = threading.Thread(target=update_loop, daemon=True)
        t.start()
        start_self_ping()  # self_ping içinde SELF_URL kontrolü var
        log("Background thread başlatıldı.", tz_now().isoformat())

# Eğer Flask sürümünde before_first_request yoksa uyarlama
if hasattr(app, "before_first_request"):
    @app.before_first_request
    def _start():
        log("Starting background thread from before_first_request...")
        start_background_once()
else:
    # fallback: first request geldiğinde başlat (idempotent)
    @app.before_request
    def _start_request():
        if request.path.startswith("/static") or request.path.startswith("/favicon"):
            return
        if not started:
            log("Starting background thread from before_request...")
            start_background_once()

# ROUTES
@app.route("/")
def dashboard():
    # statik dashboard dosyası /static/dashboard.html
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# Admin endpoint: add chat id (protected minimally by a secret key if wanted)
@app.route("/admin/add_chat/<int:chat_id>")
def add_chat(chat_id):
    secret = os.getenv("ADMIN_SECRET", "")
    if secret and request.args.get("s") != secret:
        return "unauthorized", 401
    if chat_id not in CHAT_IDS:
        CHAT_IDS.append(chat_id)
    return jsonify({"ok": True, "chat_ids": CHAT_IDS})

if __name__ == "__main__":
    # development server (local testing)
    start_background_once()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
