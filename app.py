from flask import Flask, jsonify, send_from_directory, request
import threading
import time
import requests
import os
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

# -----------------------
# Global state & lock
# -----------------------
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# -----------------------
# TELEGRAM (env ile uyumlu; fallback var)
# -----------------------
# Güvenlik notu: istersen TOKEN ve CHAT_IDS'i Render env var'larına taşı:
# TELEGRAM_TOKEN, CHAT_IDS (virgülle ayrılmış)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU")
# Default tek chat id; istersen Render env'e "661794787,12345,67890" şeklinde ekle
CHAT_IDS_ENV = os.getenv("CHAT_IDS", "")
if CHAT_IDS_ENV:
    try:
        CHAT_IDS = [int(x.strip()) for x in CHAT_IDS_ENV.split(",") if x.strip()]
    except:
        CHAT_IDS = [661794787]
else:
    CHAT_IDS = [661794787]

# Kullanıcıya daha fazla debug görmesi için log prefix
def log(*args, **kwargs):
    print("[APP]", *args, **kwargs)

def telegram_send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    for cid in CHAT_IDS:
        try:
            r = requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "HTML"}, timeout=8)
            # Log et
            log("Telegram ->", cid, r.status_code, r.text)
        except Exception as e:
            log("Telegram send error ->", cid, str(e))

# -----------------------
# Arka plan döngüsü
# -----------------------
def update_loop():
    global LATEST_DATA
    log("Background update_loop starting...")
    # Başlangıç bildirimi
    try:
        telegram_send("🤖 Sistem başlatıldı ve tarama aktif!")
    except Exception as e:
        log("Telegram initial send failed:", e)

    while True:
        try:
            data = fetch_bist_data()  # senin fetch_bist.py içindeki algoritmalar
            if not isinstance(data, list):
                log("fetch_bist_data returned non-list:", type(data))
                data = []

            # Her bir enstrüman için sinyal kontrolü (senin algoritmalar burada ürettiği alanları kullanır)
            for his in data:
                try:
                    # Örnek: RSI & sinyal bildirimi (senin fetch fonksiyonu 'last_signal' ve 'RSI' vermeli)
                    mesaj = ""
                    rsi = his.get("RSI")
                    last_signal = his.get("last_signal")
                    support_break = his.get("support_break")
                    resistance_break = his.get("resistance_break")
                    three_peak = his.get("three_peak_break")
                    green_11 = his.get("green_mum_11")
                    green_15 = his.get("green_mum_15")
                    ma_breaks = his.get("ma_breaks")  # eğer fetch fonksiyonunda eklersen ma-break bilgisi
                    price = his.get("current_price")
                    trend = his.get("trend")
                    signal_time = his.get("signal_time", time.strftime("%Y-%m-%d %H:%M:%S"))

                    if rsi is not None:
                        if isinstance(rsi, float) or isinstance(rsi, int):
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

                    # MA kırılımları (eğer fetch dosyan MA-20/50/100/200 kırılımı veriyorsa burada kullan)
                    if ma_breaks:
                        for ma_name, broke in ma_breaks.items():
                            if broke:
                                mesaj += f"🔷 {his['symbol']} {ma_name} kırıldı!\n"

                    # Genel bilgi
                    mesaj += f"Fiyat: {price}\n"
                    mesaj += f"Trend: {trend}\n"
                    mesaj += f"Sinyal zamanı: {signal_time}\n"

                    if mesaj:
                        telegram_send(mesaj)
                except Exception as e:
                    log("Error composing message for", his.get("symbol"), e)

            # Güncel veriyi kaydet (thread-safe)
            with data_lock:
                # global bildirimi en üstte var
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
        except Exception as e:
            log("Update loop exception:", str(e))
            with data_lock:
                LATEST_DATA = {"status": "error", "err": str(e)}
        # Döngü aralığı: istersen env ile değiştirilebilir
        time.sleep(int(os.getenv("FETCH_INTERVAL", 60)))

# -----------------------
# Background starter (gunicorn ile uyumlu)
# -----------------------
_background_started = False
def start_background_if_needed():
    global _background_started
    if _background_started:
        return
    _background_started = True

    # Start update loop thread
    t = threading.Thread(target=update_loop, daemon=True)
    t.start()
    log("Background update_loop thread started.")

    # Start self-ping (start_self_ping fonksiyonu içindeki SELF_URL kontrol ediyor)
    try:
        start_self_ping()
        log("Self-ping started (if SELF_URL set).")
    except Exception as e:
        log("Self-ping start error:", e)

# Flask hook: before_first_request varsa kullan, yoksa before_request fallback
if hasattr(app, "before_first_request"):
    @app.before_first_request
    def _start_jobs_before_first():
        log("Starting background thread from before_first_request...")
        start_background_if_needed()
else:
    @app.before_request
    def _start_jobs_before_request():
        # fallback (sadece ilk request'te başlat)
        if request.path == "/" or request.path == "/api":
            log("Starting background thread from before_request...")
            start_background_if_needed()

# -----------------------
# Routes
# -----------------------
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# Healthcheck endpoint (render / loadbalancer için faydalı)
@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": int(time.time())})

# Eğer doğrudan python app.py ile çalıştırılıyorsa (local geliştirme)
if __name__ == "__main__":
    log("Starting app via __main__ (dev mode)...")
    start_background_if_needed()
    # Local'da port olarak env PORT'a bak, yoksa 10000
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
