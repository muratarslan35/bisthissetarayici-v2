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
            r = requests.post(url, json=payload, timeout=6)
            print("[APP] Telegram ->", cid, r.status_code, r.text if r is not None else "")
            # if unauthorized, log once (do not spam)
            if r.status_code == 401:
                print("[APP] Telegram unauthorized. Check TELEGRAM_TOKEN.")
        except Exception as e:
            print("[APP] Telegram Error:", e)

# ------------- CHECK SIGNAL DUPLICATION -------------
def should_send_signal(symbol, sig_key, dedupe_seconds=86400):
    """
    Return True if this (symbol, sig_key) wasn't sent in the last dedupe_seconds.
    We store epoch seconds.
    """
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
        try:
            now_tr = to_tr_timezone(datetime.utcnow())
            # next midnight TR (set 00:00:05 to ensure after midnight)
            next_midnight = (now_tr + timedelta(days=1)).replace(
                hour=0, minute=0, second=5, microsecond=0
            )
            sleep_seconds = max(5, (next_midnight - now_tr).total_seconds())
            time.sleep(sleep_seconds)
            with SENT_LOCK:
                SENT_SIGNALS.clear()
            print("[APP] Daily SENT_SIGNALS cleared at TR midnight.")
        except Exception as e:
            print("[APP] daily_reset_loop error:", e)
            # wait bit before retry
            time.sleep(30)

# ---------------- MAIN UPDATE LOOP ----------------
def update_loop():
    global LATEST_DATA

    print("[APP] update_loop started.")
    # send one-time startup notification (only if token seems valid)
    try:
        telegram_send("🤖 Sistem aktif! Bot başlatıldı.")
    except Exception as e:
        print("[APP] startup telegram error:", e)

    while True:
        try:
            fetch_start = time.time()
            results = fetch_bist_data()  # list of dicts per symbol
            fetch_end = time.time()

            processed = []
            total_signals_sent = 0

            for item in results:
                try:
                    # process_signals MUST return list of tuples (sig_key, message)
                    signals = process_signals(item) or []
                except Exception as e:
                    print("[APP] process_signals error for", item.get("symbol"), e)
                    signals = []

                # ensure signals is iterable of (key,message)
                for x in signals:
                    if not isinstance(x, (list, tuple)) or len(x) < 2:
                        continue
                    sig_key, message = x[0], x[1]
                    # dedupe by symbol + sig_key
                    if should_send_signal(item.get("symbol"), sig_key):
                        try:
                            telegram_send(message)
                            total_signals_sent += 1
                        except Exception as e:
                            print("[APP] telegram send failed for", item.get("symbol"), e)

                processed.append(item)

            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "data": processed,
                    "timestamp": int(time.time()),
                    "last_fetch": to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
                    "last_scan": to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
                }

            print(f"[APP] Loop completed. scanned={len(processed)} signals_sent={total_signals_sent} fetch_time={int(fetch_end-fetch_start)}s")

        except Exception as e:
            print("[APP] Loop ERROR:", e)
            # ensure LATEST_DATA contains the error for dashboard visibility
            with data_lock:
                LATEST_DATA["status"] = "error"
                LATEST_DATA["error"] = str(e)
        # wait interval
        interval = int(os.getenv("FETCH_INTERVAL", "60"))
        time.sleep(interval)

# ---------------- START THREADS ----------------
started = False

@app.before_request
def start_background_once():
    global started
    if not started:
        started = True
        threading.Thread(target=update_loop, daemon=True).start()
        threading.Thread(target=daily_reset_loop, daemon=True).start()
        # start self-ping if configured
        try:
            from self_ping import start_self_ping
            start_self_ping()
            print("[APP] Self-ping started (if SELF_URL set).")
        except Exception as e:
            print("[APP] start_self_ping error:", e)
        print("[APP] Background threads started.")

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return send_from_directory("static", "dashboard.html")

@app.route("/latest-data")
def latest_data():
    with data_lock:
        # always return JSON safe structure
        out = {
            "data": LATEST_DATA.get("data", []),
            "status": LATEST_DATA.get("status"),
            "last_scan": LATEST_DATA.get("last_scan"),
            "last_fetch": LATEST_DATA.get("last_fetch"),
            "timestamp": LATEST_DATA.get("timestamp"),
            "error": LATEST_DATA.get("error", None)
        }
        return jsonify(out)

@app.route("/api")
def api_root():
    with data_lock:
        return jsonify(LATEST_DATA)

@app.route("/health")
def health():
    return make_response(jsonify({"status":"ok"}), 200)

# ---------------- LOCAL DEV ----------------
if __name__ == "__main__":
    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=daily_reset_loop, daemon=True).start()
    try:
        from self_ping import start_self_ping
        start_self_ping()
    except Exception:
        pass
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
