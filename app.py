# app.py
import threading
import time
import os
from zoneinfo import ZoneInfo
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request

from fetch_bist import fetch_bist_data
from signal_engine import process_signals
from self_ping import start_self_ping

app = Flask(__name__, static_folder="static")

# GLOBAL
LATEST_DATA = {"status": "init", "data": None, "timestamp": None}
data_lock = threading.Lock()

# TELEGRAM SETTINGS (ENV first, fallback to hard-coded token if present)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
# CHAT_IDS: comma separated env or default your id
CHAT_IDS_ENV = os.getenv("CHAT_IDS")
if CHAT_IDS_ENV:
    CHAT_IDS = [int(x.strip()) for x in CHAT_IDS_ENV.split(",") if x.strip().isdigit()]
else:
    CHAT_IDS = [661794787]

# STATE for notifications (keeps which event was notified for a symbol)
NOTIFIED = {}  # { symbol: set(event_keys) }

# tz
TR_TZ = ZoneInfo("Europe/Istanbul")

def send_telegram(text):
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in CHAT_IDS:
        try:
            r = requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "HTML"}, timeout=6)
            app.logger.info(f"[APP] Telegram -> {cid} {r.status_code} {r.text}")
        except Exception as e:
            app.logger.exception("[APP] Telegram send error: %s", e)

def sistem_bildir():
    send_telegram(f"🤖 Sistem başlatıldı ve aktif! (URL: {os.getenv('SELF_URL','(no SELF_URL)')})")

# Background loop
def update_loop():
    app.logger.info("[APP] Background update_loop starting...")
    # send startup once
    try:
        sistem_bildir()
    except Exception as e:
        app.logger.exception("startup telegram failed: %s", e)

    while True:
        try:
            data = fetch_bist_data()
            # process signals -> returns list of (symbol, event_key, message)
            events = process_signals(data, notified_state=NOTIFIED, tz=TR_TZ)

            # send telegram for new events
            for ev in events:
                symbol, event_key, message = ev
                send_telegram(message)

            with data_lock:
                LATEST_DATA["status"] = "ok"
                LATEST_DATA["data"] = data
                LATEST_DATA["timestamp"] = int(time.time())

        except Exception as e:
            app.logger.exception("update_loop exception: %s", e)
            with data_lock:
                LATEST_DATA["status"] = "error"
                LATEST_DATA["error"] = str(e)
        time.sleep(int(os.getenv("UPDATE_INTERVAL", "60")))

# start background safely: use a flag and start at import time in worker
_started = False
def start_background_once():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=update_loop, daemon=True, name="update_loop").start()
        # start self ping if SELF_URL set
        start_self_ping()
        app.logger.info("[APP] Background thread started.")

# Use before_request to ensure a worker starts background on first request
@app.before_request
def ensure_background():
    start_background_once()

# Routes
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# optional health endpoint
@app.route("/health")
def health():
    with data_lock:
        return jsonify({"status": LATEST_DATA.get("status", "init")})

if __name__ == "__main__":
    # when run locally
    start_background_once()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
