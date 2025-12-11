# app.py
import os
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, send_from_directory, request, make_response
try:
    from flask_cors import CORS
except Exception:
    CORS = None

from fetch_bist import fetch_bist_data
from signal_engine import process_signals
from utils import to_tr_timezone

app = Flask(__name__, static_folder="static", static_url_path="/")
if CORS:
    CORS(app)

# --- GLOBALS ---
LATEST_DATA = {
    "status": "init",
    "data": [],
    "last_scan": None,
    "last_fetch": None,
    "timestamp": None
}

data_lock = threading.Lock()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")

CHAT_IDS = os.getenv("CHAT_IDS", None)
if CHAT_IDS:
    try:
        CHAT_IDS = [int(x.strip()) for x in CHAT_IDS.split(",")]
    except:
        CHAT_IDS = [661794787]
else:
    CHAT_IDS = [661794787]

# SENT SIGNALS (duplicate prevent)
SENT_SIGNALS = {}
SENT_LOCK = threading.Lock()

# ---------------- TELEGRAM SEND ----------------
import requests
def telegram_send(text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"text": text, "parse_mode": parse_mode}
    headers = {"Content-Type": "application/json"}

    for cid in CHAT_IDS:
        payload["chat_id"] = cid
        try:
            r = requests.post(url, json=payload, timeout=5)
            print("[APP] Telegram ->", cid, r.status_code)
        except Exception as e:
            print("[APP] Telegram Error:", e)

# ------------- CHECK SIGNAL DUPLICATION -------------
def should_send_signal(symbol, sig_key, dedupe_seconds=86400):
    now = int(time.time())
    with SENT_LOCK:
        sym_map = SENT_SIGNALS.setdefault(symbol, {})
        last = sym_map.get(sig_key)

        if last is None or now - last > dedupe_seconds:
            sym_map[sig_key] = now
            return True
        return False

# ------------- DAILY RESET AT MIDNIGHT TR -------------
def daily_reset_loop():
    while True:
        now_tr = to_tr_timezone(datetime.utcnow())
        next_midnight = (now_tr + timedelta(days=1)).replace(
            hour=0, minute=0, second=5, microsecond=0
        )
        sleep_seconds = max(5, (next_midnight - now_tr).total_seconds())
        time.sleep(sleep_seconds)
        with SENT_LOCK:
            SENT_SIGNALS.clear()
        print("[APP] Daily signal reset done")

# ---------------- MAIN UPDATE LOOP ----------------
def update_loop():
    global LATEST_DATA

    print("[APP] update_loop started.")
    telegram_send("🤖 Sistem aktif! Bot başlatıldı.")

    while True:
        try:
            fetch_start = time.time()
            results = fetch_bist_data()
            fetch_end = time.time()

            processed = []

            for item in results:
                signals = process_signals(item)

                for sig_key, message in signals:
                    if should_send_signal(item["symbol"], sig_key):
                        telegram_send(message)

                processed.append(item)

            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "data": processed,
                    "timestamp": int(time.time()),
                    "last_fetch": to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
                    "last_scan": to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
                }

            print(f"[APP] Loop completed. {len(processed)} symbols scanned.")

        except Exception as e:
            print("[APP] Loop ERROR:", e)
            with data_lock:
                LATEST_DATA["status"] = "error"
                LATEST_DATA["error"] = str(e)

        time.sleep(int(os.getenv("FETCH_INTERVAL", "60")))

# ---------------- START THREADS ----------------
started = False

@app.before_request
def start_background_once():
    global started
    if not started:
        started = True
        threading.Thread(target=update_loop, daemon=True).start()
        threading.Thread(target=daily_reset_loop, daemon=True).start()
        from self_ping import start_self_ping
        start_self_ping()
        print("[APP] Background threads started.")

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return send_from_directory("static", "dashboard.html")

@app.route("/latest-data")
def latest_data():
    with data_lock:
        return jsonify(LATEST_DATA)

@app.route("/api")
def api_root():
    with data_lock:
        return jsonify(LATEST_DATA)

@app.route("/health")
def health():
    return make_response(jsonify({"status": "ok"}), 200)

# ---------------- LOCAL DEV ----------------
if __name__ == "__main__":
    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=daily_reset_loop, daemon=True).start()
    from self_ping import start_self_ping
    start_self_ping()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
