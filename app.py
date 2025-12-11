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
LATEST_DATA = {"status": "init", "data": [], "last_scan": None, "last_fetch": None}
data_lock = threading.Lock()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")
# default CHAT_IDS - sen bunlari render env olarak da koyabilirsin
CHAT_IDS = os.getenv("CHAT_IDS", None)
if CHAT_IDS:
    try:
        CHAT_IDS = [int(x.strip()) for x in CHAT_IDS.split(",")]
    except:
        CHAT_IDS = [661794787]
else:
    CHAT_IDS = [661794787]  # fallback

SENT_SIGNALS = {}  # { "SYMBOL": {"signal_key": timestamp_sent, ... }, ... }
SENT_LOCK = threading.Lock()

# Telegram helper
import requests
def telegram_send(text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"text": text, "parse_mode": parse_mode}
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        payload["chat_id"] = cid
        try:
            r = requests.post(url, json=payload, timeout=6)
            # log
            print("[APP] Telegram ->", cid, r.status_code, r.text)
            # if unauthorized, stop attempting repeatedly
            if r.status_code == 401:
                print("[APP] Telegram unauthorized. Check TELEGRAM_TOKEN.")
        except Exception as e:
            print("[APP] Telegram send error:", e)

# Utility: whether we should send this specific signal (dedupe)
def should_send_signal(symbol, sig_key, dedupe_seconds=60*60*24):
    # sig_key: e.g. "RSI_AL", "MA20_above", "DAILY_COMBINED"
    now = int(time.time())
    with SENT_LOCK:
        sym_map = SENT_SIGNALS.setdefault(symbol, {})
        last = sym_map.get(sig_key)
        if last is None:
            sym_map[sig_key] = now
            return True
        # if older than dedupe_seconds, allow resend
        if now - last > dedupe_seconds:
            sym_map[sig_key] = now
            return True
        return False

# Reset daily signals at midnight TR
def daily_reset_loop():
    while True:
        now_tr = to_tr_timezone(datetime.utcnow())
        # compute seconds until next midnight TR
        next_midnight = (now_tr + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        sleep_seconds = (next_midnight - now_tr).total_seconds()
        time.sleep(max(5, sleep_seconds))
        with SENT_LOCK:
            SENT_SIGNALS.clear()
            print("[APP] Daily SENT_SIGNALS cleared at TR midnight.")

# Background update loop
def update_loop():
    global LATEST_DATA
    print("[APP] Background update_loop starting...")
    # initial notify
    telegram_send("🤖 Sistem başlatıldı ve aktif! (Bot başlatıldı)")
    while True:
        try:
            start_fetch = time.time()
            results = fetch_bist_data()  # list of dicts per symbol
            fetch_done = time.time()
            processed = []
            # process signals (this will evaluate combined daily logic etc.)
            for item in results:
                # process_signals returns list of (sig_key, message) to send OR empty
                signals = process_signals(item)
                # for each unique signal, check dedupe
                for sig_key, message in signals:
                    if should_send_signal(item["symbol"], sig_key):
                        telegram_send(message)
                processed.append(item)
            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "last_scan": to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
                    "last_fetch": int(fetch_done),
                    "data": processed
                }
            print(f"[APP] Update loop finished. symbols={len(processed)} scan_time={int(time.time()-start_fetch)}s")
        except Exception as e:
            print("[APP] update error:", e)
            with data_lock:
                LATEST_DATA["status"] = "error"
                LATEST_DATA["error"] = str(e)
        # interval: 60s (configurable via env)
        interval = int(os.getenv("FETCH_INTERVAL", "60"))
        time.sleep(interval)

# Hook to start background threads once (works for gunicorn too)
started = False
@app.before_request
def start_background_once():
    global started
    if not started:
        started = True
        t = threading.Thread(target=update_loop, daemon=True)
        t.start()
        t2 = threading.Thread(target=daily_reset_loop, daemon=True)
        t2.start()
        # start self ping (optional)
        from self_ping import start_self_ping
        start_self_ping()
        print("[APP] Background threads started from before_request.")

# --- routes ---
@app.route("/")
def index():
    # serve static dashboard
    return send_from_directory("static", "dashboard.html")

@app.route("/latest-data")
def latest_data():
    with data_lock:
        return jsonify(LATEST_DATA)

@app.route("/api")
def api_root():
    # legacy API; return same
    with data_lock:
        return jsonify(LATEST_DATA)

@app.route("/health")
def health():
    return make_response(jsonify({"status":"ok"}), 200)

if __name__ == "__main__":
    # local run fallback
    print("[APP] Starting dev server...")
    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=daily_reset_loop, daemon=True).start()
    from self_ping import start_self_ping
    start_self_ping()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
