from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
import os
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__, static_folder="static")

# ---------- CONFIG ----------
# WARNING: token hard-coded per your request. Prefer env var in production:
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "8588829956:AAEK2-wa75CoHQjPjPFEAUU_LElRBduC-_TU"
# CHAT_IDS can be a CSV in env or defined here
CHAT_IDS_ENV = os.getenv("CHAT_IDS")  # e.g. "661794787,12345678"
if CHAT_IDS_ENV:
    CHAT_IDS = [int(x.strip()) for x in CHAT_IDS_ENV.split(",") if x.strip()]
else:
    CHAT_IDS = [
        661794787,
        # add more IDs manually here if desired
    ]

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))  # seconds
# ----------------------------

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# log helper
def log(*args, **kwargs):
    print("[APP]", *args, **kwargs)

# telegram send with basic logging and timeout
def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        try:
            r = requests.post(url, json={"chat_id": cid, "text": text, "parse_mode":"HTML"}, timeout=10)
            log("Telegram ->", cid, r.status_code, r.text[:200])
        except Exception as e:
            log("Telegram send error ->", cid, e)

# Background update loop: fetch data, compute signals, send telegram when conditions occur
def update_loop():
    global LATEST_DATA
    try:
        log("Background update_loop starting...")
        # send one-time start message
        telegram_send("🤖 Sistem başlatıldı ve aktif! (background loop başladı)")
    except Exception as e:
        log("Start message error:", e)

    while True:
        try:
            data = fetch_bist_data()  # returns list of dicts per symbol
            # For every symbol, evaluate notifications (keeps your algorithms intact)
            for his in data:
                try:
                    rsi = his.get("RSI")
                    last_signal = his.get("last_signal")
                    support_break = his.get("support_break")
                    resistance_break = his.get("resistance_break")
                    green_11 = his.get("green_mum_11")
                    green_15 = his.get("green_mum_15")
                    three_peak = his.get("three_peak_break")
                    ma_breaks = his.get("ma_breaks", {})  # ma20/50/100/200 breaks dict
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

                    # Signals
                    if last_signal == "AL":
                        mesaj += f"🟢 {his['symbol']} AL sinyali!\n"
                    elif last_signal == "SAT":
                        mesaj += f"🔴 {his['symbol']} SAT sinyali!\n"

                    # Support/Resistance
                    if support_break:
                        mesaj += f"🟢 {his['symbol']} destek kırıldı!\n"
                    if resistance_break:
                        mesaj += f"🔴 {his['symbol']} direnç kırıldı!\n"

                    # 3-peak
                    if three_peak:
                        mesaj += f"⚠️ {his['symbol']} üç tepe kırılımı gerçekleşti!\n"

                    # green candles
                    if green_11:
                        mesaj += f"🟢 {his['symbol']} 11:00-15:00 aralığında 4H yeşil mum.\n"
                    if green_15:
                        mesaj += f"🟢 {his['symbol']} 15:00-19:00 aralığında 4H yeşil mum.\n"

                    # MA breaks
                    for ma_label, val in ma_breaks.items():
                        if val is True:
                            mesaj += f"📈 {his['symbol']} {ma_label} kırılımı.\n"

                    # daily info
                    mesaj += f"Fiyat: {price} TL | Değişim: {daily_change} | Hacim: {volume} | Trend: {trend}\n"
                    mesaj += f"Sinyal zamanı: {signal_time} | RSI: {rsi}\n"

                    # send only if mesaj not empty and has notable items (to avoid spam)
                    # We allow sending short info if last_signal exists or MA/support/resistance/three_peak triggered
                    notable = any([
                        last_signal in ("AL","SAT"),
                        support_break, resistance_break, three_peak,
                        any(ma_breaks.values()),
                        (rsi is not None and (rsi < 20 or rsi > 80))
                    ])
                    # You can change behavior: to always send summary, set notable=True
                    if mesaj and notable:
                        telegram_send(mesaj)
                except Exception as e:
                    log("Per-symbol handling error:", his.get("symbol"), e)

            # update shared data for dashboard
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
        except Exception as e:
            log("update_loop error:", e)
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}
        time.sleep(POLL_INTERVAL)

# Start background thread in a safe-once way:
_bg_started = False
_bg_lock = threading.Lock()

@app.before_request
def ensure_background_started():
    # This handler runs on each request; the first request will trigger the background thread.
    global _bg_started
    if not _bg_started:
        with _bg_lock:
            if not _bg_started:
                log("Starting background thread from before_request...")
                threading.Thread(target=update_loop, daemon=True).start()
                # start self ping if env provided
                try:
                    start_self_ping()
                    log("Self-ping started (if SELF_URL set).")
                except Exception as e:
                    log("self-ping start error:", e)
                _bg_started = True

# Routes
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# If someone runs app.py directly (not via gunicorn), support it for local testing:
if __name__ == "__main__":
    # start thread immediately for local runs
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    # use flask dev server in local test only
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
