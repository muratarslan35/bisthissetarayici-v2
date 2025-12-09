import os
import threading
import time
import requests
from flask import Flask, jsonify, send_from_directory
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

# Global state
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# TELEGRAM - istersen token + chat ids'i env değişkeni yap (güvenlik için öneri)
# Eğer TOKEN/CHAT_IDS environment'da varsa onları kullan, yoksa buraya yazılı olan kullanılacak.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")

# CHAT_IDS: manuel ekleme kolaylığı için liste burada
CHAT_IDS = os.getenv("CHAT_IDS")  # e.g. "661794787,12345678"
if CHAT_IDS:
    try:
        CHAT_IDS = [int(x.strip()) for x in CHAT_IDS.split(",") if x.strip()]
    except:
        CHAT_IDS = [661794787]
else:
    CHAT_IDS = [
        661794787,
        # buraya eklemek istediğin ID'leri virgülle ayırarak ENV üzerinden ekleyebilirsin
        # veya doğrudan listeye yazabilirsin: 12345, 67890
    ]

TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def telegram_send(text, parse_mode="HTML"):
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        payload = {"chat_id": cid, "text": text, "parse_mode": parse_mode}
        try:
            r = requests.post(TG_URL, json=payload, timeout=7)
            # Log response for debugging
            print(f"[APP] Telegram -> {cid} {r.status_code} {r.text}")
        except Exception as e:
            print("[APP] Telegram send error for", cid, e)


# Background update loop
def update_loop():
    global LATEST_DATA
    print("[APP] Background update_loop starting...")
    # initial notification
    try:
        telegram_send("🤖 Sistem başlatıldı ve taramalar aktif. (Render)")
    except Exception as e:
        print("[APP] initial telegram error:", e)

    while True:
        try:
            data = fetch_bist_data()  # fetch_bist returns list of dicts
            # send notifications for interesting items
            for item in data:
                try:
                    mesaj = ""
                    symbol = item.get("symbol")
                    rsi = item.get("RSI")
                    last_signal = item.get("last_signal")
                    support_break = item.get("support_break")
                    resistance_break = item.get("resistance_break")
                    three_peak = item.get("three_peak_break")
                    green_11 = item.get("green_mum_11")
                    green_15 = item.get("green_mum_15")
                    price = item.get("current_price")
                    trend = item.get("trend")
                    daily_change = item.get("daily_change")
                    volume = item.get("volume")
                    ma_breaks = item.get("ma_breaks", {})  # dict of MA break flags

                    # compose message
                    if rsi is not None:
                        if rsi < 20:
                            mesaj += f"🔻 {symbol} RSI {rsi:.2f} < 20!\n"
                        elif rsi > 80:
                            mesaj += f"🔺 {symbol} RSI {rsi:.2f} > 80!\n"

                    if last_signal == "AL":
                        mesaj += f"🟢 {symbol} AL sinyali!\n"
                    elif last_signal == "SAT":
                        mesaj += f"🔴 {symbol} SAT sinyali!\n"

                    if support_break:
                        mesaj += f"🟢 {symbol} destek kırıldı!\n"
                    if resistance_break:
                        mesaj += f"🔴 {symbol} direnç kırıldı!\n"
                    if three_peak:
                        mesaj += f"⚠️ {symbol} üç tepe kırılımı gerçekleşti!\n"
                    if green_11:
                        mesaj += f"🟢 {symbol} 11:00-15:00 arası yeşil mum.\n"
                    if green_15:
                        mesaj += f"🟢 {symbol} 15:00 saatinde yeşil mum.\n"

                    # MA kırılımları
                    for ma_name, flag in ma_breaks.items():
                        if flag:
                            mesaj += f"📈 {symbol} MA-{ma_name} kırılımı tespit edildi!\n"

                    mesaj += f"Fiyat: {price} TL\nGünlük değişim: {daily_change}\nHacim: {volume}\nTrend: {trend}\nRSI: {rsi}\n"

                    if mesaj:
                        telegram_send(mesaj)
                except Exception as e:
                    print("[APP] notify error for item", item.get("symbol"), e)

            # update global data
            with data_lock:
                # explicit global update
                global LATEST_DATA
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
        except Exception as e:
            print("[APP] update loop error:", e)
            with data_lock:
                global LATEST_DATA
                LATEST_DATA = {"status": "error", "error": str(e)}
        # wait
        time.sleep(int(os.getenv("FETCH_INTERVAL", 60)))  # default 60s


# Start background thread exactly once (compatible with gunicorn)
_started = False
_start_lock = threading.Lock()

def start_background_if_needed():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        print("[APP] Starting background thread...")
        t = threading.Thread(target=update_loop, daemon=True)
        t.start()
        # Start self ping if configured
        try:
            start_self_ping()
            print("[APP] Self-ping started (if SELF_URL set).")
        except Exception as e:
            print("[APP] Self-ping error:", e)

# Try to register startup either via before_first_request or before_request (compat)
try:
    if hasattr(app, "before_first_request"):
        @app.before_first_request
        def _start():
            print("[APP] before_first_request triggered.")
            start_background_if_needed()
    else:
        # fallback
        @app.before_request
        def _start_fallback():
            start_background_if_needed()
except Exception:
    # last-resort: attempt to start immediately (works in many environments, but may run per worker)
    start_background_if_needed()


# Routes
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)


# When running locally (not via gunicorn) you can use this:
if __name__ == "__main__":
    start_background_if_needed()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
