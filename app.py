from flask import Flask, jsonify, send_from_directory
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# ------------------------ TELEGRAM AYARLARI ------------------------
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_ID = 661794787

def telegram_send(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Telegram gönderim hatası:", e)

# ----------------------- SİSTEM BAŞLANGIÇ MESAJI -----------------------
def sistem_bildir():
    telegram_send("🤖 Sistem başarıyla başlatıldı!\nTarama aktif 🚀")


# ----------------------- ANA TARAYICI DÖNGÜSÜ ------------------------
def update_loop():
    while True:
        try:
            data = fetch_bist_data()
            print("Tarama çalıştı, toplam:", len(data))

            # Hem dashboard'a hem telegram'a algoritmalı bildirim
            for h in data:
                mesaj = ""
                s = h["symbol"]

                # --- RSI ---
                if h["RSI"] is not None:
                    if h["RSI"] < 20:
                        mesaj += f"🔻 {s} RSI < 20 ({h['RSI']:.2f})\n"
                    if h["RSI"] > 80:
                        mesaj += f"🔺 {s} RSI > 80 ({h['RSI']:.2f})\n"

                # --- AL / SAT algoritması ---
                if h["last_signal"] == "AL":
                    mesaj += f"🟢 {s} AL sinyali\n"
                if h["last_signal"] == "SAT":
                    mesaj += f"🔴 {s} SAT sinyali\n"

                # --- Mum algoritmaları ---
                if h["green_mum_11"]:
                    mesaj += f"🟢 {s} 11:00 yeşil mum\n"
                if h["green_mum_15"]:
                    mesaj += f"🟢 {s} 15:00 yeşil mum\n"

                # --- Destek / direnç / üç tepe ---
                if h["support_break"]:
                    mesaj += f"🟢 {s} destek kırıldı\n"
                if h["resistance_break"]:
                    mesaj += f"🔴 {s} direnç kırıldı\n"
                if h["three_peak_break"]:
                    mesaj += f"⚠️ {s} üç tepe kırılımı\n"

                # --- Ek bilgiler ---
                mesaj += f"Fiyat: {h['current_price']} TL\n"
                mesaj += f"Günlük: {h['daily_change']}\nHacim: {h['volume']}\n"
                mesaj += f"Trend: {h['trend']}\n"
                mesaj += f"RSI: {h['RSI']}\n"
                mesaj += f"Sinyal zamanı: {h['signal_time']}\n"

                # Bildirim gönder
                if mesaj.strip() != "":
                    telegram_send(mesaj)

            # Dashboard güncellemesi
            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "data": data
                }

        except Exception as e:
            print("update_loop hatası:", e)
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}

        time.sleep(60)  # 1 dk tarama


# ----------------------- ROUTES -----------------------
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)


# ----------------------- ÇALIŞTIRICI -----------------------
if __name__ == "__main__":
    sistem_bildir()  # Telegram bildirimi kesin gönderilir
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
