# app.py
import os
import sys
import threading
import time
import requests
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory, request
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping
from utils import to_tr_timezone

app = Flask(__name__)

# --- GLOBALS ---
LATEST_DATA = {"status": "init", "data": None, "timestamp": None}
data_lock = threading.Lock()

# TELEGRAM (kullanıcı istediği gibi token burada)
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
# Buraya elle ekleyebileceğin ID'leri koy:
CHAT_IDS = [
    661794787,
    # 12345678, 87654321
]

# Bildirimleri tekrarlamamak için saklanan set/dict
# format: sent_signals[symbol] = set(of signal keys)
sent_signals = {}
# günlük tekil (1D) bildirimi kontrolü için tarih
last_daily_reset = None

# Helper: telegram gönder (loglayıp hataları gösterir)
def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        try:
            payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
            r = requests.post(url, json=payload, timeout=8)
            # log response for debugging
            app.logger.info(f"[TELEGRAM] to {cid} {r.status_code} {r.text}")
            if r.status_code == 401:
                app.logger.error("[TELEGRAM] Unauthorized token or bot blocked by chat_id.")
        except Exception as e:
            app.logger.exception("[TELEGRAM] send failed:")

# Helper - biçimlendirici: MA durumunu okunur hâle getir
def fmt_ma_breaks(ma_breaks):
    parts = []
    for k, v in ma_breaks.items():
        if v is None:
            parts.append(f"{k}: N/A")
        else:
            # v expected "above"/"below" or "golden_cross"/"death_cross"
            if v in ("above", "price_above"):
                parts.append(f"{k}: ÜSTTE")
            elif v in ("below", "price_below"):
                parts.append(f"{k}: ALTI")
            elif v == "golden_cross":
                parts.append(f"{k}: GOLDEN CROSS")
            elif v == "death_cross":
                parts.append(f"{k}: DEATH CROSS")
            else:
                parts.append(f"{k}: {v}")
    return " | ".join(parts)

# Sinyal ana işleme: gelen veri içindeki koşulları değerlendirir, mesaj hazırlar
def process_and_notify(data_list):
    global sent_signals, last_daily_reset

    # günlük reset (TR günü) - sent_daily set sıfırlama
    now_tr = to_tr_timezone(datetime.now(timezone.utc))
    today_date = now_tr.date()
    if last_daily_reset != today_date:
        # reset günlük tekil bildiriler (günlük-only sinyaller için)
        sent_signals = {}
        last_daily_reset = today_date
        app.logger.info("[APP] Daily sent_signals reset.")

    for item in data_list:
        symbol = item.get("symbol")
        if not symbol:
            continue

        # set up record for symbol
        if symbol not in sent_signals:
            sent_signals[symbol] = set()

        rsi = item.get("RSI")
        last_signal = item.get("last_signal")
        support_break = item.get("support_break")
        resistance_break = item.get("resistance_break")
        three_peak = item.get("three_peak_break")
        green_11 = item.get("green_mum_11")
        green_15 = item.get("green_mum_15")
        ma_breaks = item.get("ma_breaks", {})  # dict MA20..MA200 / above/below/cross
        ma_values = item.get("ma_values", {})
        trend = item.get("trend")
        price = item.get("current_price")
        daily_change = item.get("daily_change")
        volume = item.get("volume")
        signal_time_raw = item.get("signal_time")  # expected in UTC string or aware dt

        # Normalize time -> TR
        try:
            if isinstance(signal_time_raw, str):
                # try parse ISO-like first
                dt = datetime.fromisoformat(signal_time_raw)
            elif isinstance(signal_time_raw, datetime):
                dt = signal_time_raw
            else:
                dt = datetime.now(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        dt_tr = to_tr_timezone(dt.astimezone(timezone.utc))
        dt_str = dt_tr.strftime("%Y-%m-%d %H:%M:%S (TR)")

        # build message parts according to user's full algorithm set
        messages = []

        # RSI extremes (>=80 or <=20) - use thresholds from user's spec (20/80)
        if rsi is not None:
            try:
                rsi_val = float(rsi)
                if rsi_val < 20 and "RSI_<20" not in sent_signals[symbol]:
                    messages.append(f"🔻 {symbol} RSI {rsi_val:.2f} < 20 (AL Uyarısı)")
                    sent_signals[symbol].add("RSI_<20")
                elif rsi_val > 80 and "RSI_>80" not in sent_signals[symbol]:
                    messages.append(f"🔺 {symbol} RSI {rsi_val:.2f} > 80 (SAT Uyarısı)")
                    sent_signals[symbol].add("RSI_>80")
            except:
                pass

        # AL / SAT sinyali (from algorithm)
        if last_signal and last_signal in ("AL","SAT"):
            key = f"SIGNAL_{last_signal}"
            if key not in sent_signals[symbol]:
                messages.append(f"{'🟢' if last_signal=='AL' else '🔴'} {symbol} - {last_signal} sinyali (algoritma).")
                sent_signals[symbol].add(key)

        # support / resistance breaks
        if support_break and "SUPPORT_BREAK" not in sent_signals[symbol]:
            messages.append(f"🟢 {symbol} destek kırıldı.")
            sent_signals[symbol].add("SUPPORT_BREAK")
        if resistance_break and "RESISTANCE_BREAK" not in sent_signals[symbol]:
            messages.append(f"🔴 {symbol} direnç kırıldı.")
            sent_signals[symbol].add("RESISTANCE_BREAK")

        # three peak
        if three_peak and "THREE_PEAK" not in sent_signals[symbol]:
            messages.append(f"⚠️ {symbol} üç tepe kırılımı gerçekleşti.")
            sent_signals[symbol].add("THREE_PEAK")

        # 11 and 15 green candles
        if green_11 and "GREEN_11" not in sent_signals[symbol]:
            messages.append(f"🟢 {symbol} 11:00'de yeşil mum oluştu.")
            sent_signals[symbol].add("GREEN_11")
        if green_15 and "GREEN_15" not in sent_signals[symbol]:
            messages.append(f"🟢 {symbol} 15:00'te yeşil mum oluştu.")
            sent_signals[symbol].add("GREEN_15")

        # MA breaks / crosses and MA values summary (only notify if cross or changed and not sent already)
        # We'll notify for crosses (20x50 golden/death) and indicate MA positions
        if isinstance(ma_breaks, dict):
            # Cross
            cross_val = ma_breaks.get("20x50")
            if cross_val and f"MA20x50_{cross_val}" not in sent_signals[symbol]:
                messages.append(f"📈 {symbol} MA20x50: {cross_val.replace('_',' ').upper()}.")
                sent_signals[symbol].add(f"MA20x50_{cross_val}")

        # Additionally we include MA position summary in the message but don't use as one-off gating
        ma_summary = fmt_ma_breaks(ma_breaks)

        # Daily composite signal (A type): combine conditions as user asked (1D + 4H + 15m logic)
        # The fetch side should supply flags for 'daily_green_count' and 'h4_green_count' etc if available.
        # We'll check simple combination: if today has daily green previous and now second green + 4H green etc.
        # The fetch algorithm must set item['composite_signal'] = "A" when conditions met; if present, notify once.
        comp = item.get("composite_signal")
        if comp and f"COMPOSITE_{comp}" not in sent_signals[symbol]:
            messages.append(f"🔥 {symbol} KOMPOZİT SİNYAL {comp} tetiklendi.")
            sent_signals[symbol].add(f"COMPOSITE_{comp}")

        # If there are messages to send -> build final text including MA summary, price, trend, RSI, time
        if messages:
            header = f"{' / '.join(messages)}\n"
            body = f"Fiyat: {price} TL | Trend: {trend} | RSI: {rsi}\nGünlük değişim: {daily_change} | Hacim: {volume}\nMA: {ma_summary}\nSinyal zamanı: {dt_str}"
            final = header + body
            # send once per symbol per message (but there may be multiple message pieces)
            telegram_send(final)

    # end for

# Background update loop (run in daemon thread)
def update_loop():
    global LATEST_DATA
    app.logger.info("[APP] Background update_loop starting...")
    # send a startup notification once
    try:
        telegram_send("🤖 Sistem başlatıldı ve tarama başlıyor (Render).")
    except Exception:
        app.logger.exception("[APP] Startup telegram send failed.")

    while True:
        try:
            data = fetch_bist_data()  # list of dicts
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
            # process signals & notify
            try:
                process_and_notify(data)
            except Exception:
                app.logger.exception("[APP] process_and_notify error")
        except Exception:
            app.logger.exception("[APP] fetch_bist_data error")
            with data_lock:
                LATEST_DATA = {"status": "error", "timestamp": int(time.time()), "error": "fetch error"}
        time.sleep(60)  # 60s döngü

# Ensure background starts only once per worker (use before_request with flag)
_background_started = False
@app.before_request
def ensure_background_started():
    global _background_started
    if not _background_started:
        _background_started = True
        app.logger.info("[APP] Starting background thread from before_request...")
        t = threading.Thread(target=update_loop, daemon=True)
        t.start()
        # start self ping thread (if SELF_URL set in env)
        start_self_ping()
        app.logger.info("[APP] Self-ping started (if SELF_URL set).")

# Routes
@app.route("/")
def dashboard():
    # serve static dashboard if present
    if os.path.exists("static/dashboard.html"):
        return send_from_directory("static", "dashboard.html")
    return "<h3>BIST Tarayıcı</h3><p>Dashboard dosyası (static/dashboard.html) bulunamadı.</p>"

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# Allow manual trigger (for debugging)
@app.route("/trigger")
def trigger_now():
    # immediate fetch & notify (rate-limit not enforced here)
    try:
        data = fetch_bist_data()
        with data_lock:
            LATEST_DATA.update({"status":"ok","timestamp":int(time.time()), "data":data})
        process_and_notify(data)
        return jsonify({"result":"ok","fetched":len(data)})
    except Exception as e:
        return jsonify({"result":"error","err":str(e)}), 500

# Standard gunicorn entry: app
# Note: do NOT put update_loop under if __name__ == "__main__" because gunicorn will not call it.
