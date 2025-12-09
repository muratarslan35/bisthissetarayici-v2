from flask import Flask, jsonify, send_from_directory, request
import threading
import time
import requests
import os
from fetch_bist import fetch_bist_data, get_bist_symbols
from self_ping import start_self_ping

app = Flask(__name__)

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# ----- TELEGRAM: environment override if present -----
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")

# CHAT_IDS: can set env var CHAT_IDS="661794787,12345,67890"
env_chat = os.getenv("CHAT_IDS")
if env_chat:
    try:
        CHAT_IDS = [int(x.strip()) for x in env_chat.split(",") if x.strip()]
    except:
        CHAT_IDS = [661794787]
else:
    CHAT_IDS = [
        661794787,
        # add more IDs here manually if you like
    ]


def telegram_send(text):
    """Send a message to all CHAT_IDS and log response for debugging."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            r = requests.post(url, json=payload, timeout=7)
            app.logger.info(f"[APP] Telegram -> {cid} {r.status_code} {r.text}")
            # If 401 unauthorized, warn once
            if r.status_code == 401:
                app.logger.error("[APP] Telegram token unauthorized (401). Check TELEGRAM_TOKEN.")
        except Exception as e:
            app.logger.exception(f"[APP] Telegram send error to {cid}: {e}")


# -----------------------------
# Main update loop
# -----------------------------
def update_loop():
    """Background loop: fetch data, analyze, update LATEST_DATA and send telegram messages."""
    app.logger.info("[APP] Background update_loop starting...")
    # send initial start message
    telegram_send("🤖 Sistem başlatıldı ve tarama aktif!")

    while True:
        try:
            symbols = get_bist_symbols()
            app.logger.info(f"[APP] Symbols to scan: {len(symbols)}")
            data = fetch_bist_data(symbols)

            alerts_sent = 0
            for his in data:
                # Build message according to your existing rules (all preserved)
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
                ma_breaks = his.get("ma_breaks", {})

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
                    mesaj += f"⚠️ {his['symbol']} üç tepe kırılımı gerçekleşti!\n"

                if green_11:
                    mesaj += f"🟢 {his['symbol']} 4H saat 11'de yeşil mum oluştu.\n"
                if green_15:
                    mesaj += f"🟢 {his['symbol']} 4H saat 15'te yeşil mum oluştu.\n"

                # MA kırılımları
                for ma_name, ma_hit in ma_breaks.items():
                    if ma_hit:
                        mesaj += f"🔷 {his['symbol']} {ma_name} kırılımı!\n"

                mesaj += f"Fiyat: {price} TL | Günlük değişim: {daily_change} | Hacim: {volume} | Trend: {trend}\n"
                mesaj += f"Son sinyal: {last_signal} | Zaman: {his.get('signal_time','-')} | RSI: {rsi}\n"

                # Eğer mesaj içeriği varsa gönder
                if mesaj.strip():
                    telegram_send(mesaj)
                    alerts_sent += 1

            with data_lock:
                global LATEST_DATA
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data, "alerts_sent": alerts_sent}
            app.logger.info(f"[APP] Cycle complete, alerts_sent={alerts_sent}")

        except Exception as e:
            app.logger.exception(f"[APP] Error in update_loop: {e}")
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}
        # bekleme süresi (env üzerinden kontrol et)
        sleep_s = int(os.getenv("SCAN_INTERVAL", "60"))
        time.sleep(sleep_s)


# -----------------------------
# Start background jobs once per worker (safe approach)
# -----------------------------
started = False


@app.before_request
def start_background_once():
    global started
    if not started:
        started = True
        app.logger.info("[APP] Starting background thread from before_request...")
        t = threading.Thread(target=update_loop, daemon=True)
        t.start()
        start_self_ping()
        app.logger.info("[APP] Self-ping started (if SELF_URL set).")


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")


@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)


# Health check endpoint
@app.route("/health")
def health():
    return jsonify({"ok": True, "time": int(time.time())})


if __name__ == "__main__":
    # local debug run (not used under gunicorn)
    telegram_send("🤖 Sistem (local) başlatıldı.")
    t = threading.Thread(target=update_loop, daemon=True)
    t.start()
    start_self_ping()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
