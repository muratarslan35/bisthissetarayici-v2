from flask import Flask, jsonify, send_from_directory
import threading
import time
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping
import requests

app = Flask(__name__)
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Telegram ayarları - token ve chat ID
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"
CHAT_ID = "661794787"  # Chat ID tek ise, string ya da int fark etmez

def telegram_send(text):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        )
        # Logla, mesaj gönderilip gönderilmediğini görebilirsin
        print(f"Telegram gönderildi, durum: {response.status_code}")
        if response.status_code != 200:
            print(f"Hata: {response.text}")
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")

def sistem_bildir():
    print("Sistem başlatılıyor ve bildirim gönderiliyor...")
    telegram_send("🤖 Sistem aktif ve çalışıyor!")

def update_loop():
    global LATEST_DATA
    while True:
        print("Güncelleme başlıyor...")
        try:
            data = fetch_bist_data()
            print("Veri çekildi, güncelleniyor...")
            for his in data:
                mesaj = ""
                rsi = his.get("RSI")
                last_signal = his.get("last_signal")
                support_break = his.get("support_break")
                resistance_break = his.get("resistance_break")
                green_11 = his.get("green_mum_11")
                green_15 = his.get("green_mum_15")
                three_peak = his.get("three_peak_break")
                price = his.get("current_price")
                daily = his.get("daily_change")
                volume = his.get("volume")
                trend = his.get("trend")
                sigtime = his.get("signal_time")

                if rsi is not None:
                    if rsi < 20:
                        mesaj += f"🔻 {his['symbol']} RSI <20 ({rsi:.2f})\n"
                    elif rsi > 80:
                        mesaj += f"🔺 {his['symbol']} RSI >80 ({rsi:.2f})\n"

                if last_signal == "AL":
                    mesaj += f"🟢 {his['symbol']} AL sinyali!\n"
                if last_signal == "SAT":
                    mesaj += f"🔴 {his['symbol']} SAT sinyali!\n"

                if support_break:
                    mesaj += f"🟢 Destek kırıldı: {his['symbol']}\n"
                if resistance_break:
                    mesaj += f"🔴 Direnç kırıldı: {his['symbol']}\n"
                if three_peak:
                    mesaj += f"⚠️ Üç tepe kırıldı: {his['symbol']}\n"
                if green_11:
                    mesaj += f"🟢 4H 11:00 yeşil mum → {his['symbol']}\n"
                if green_15:
                    mesaj += f"🟢 4H 15:00 yeşil mum → {his['symbol']}\n"

                mesaj += f"Fiyat: {price}\n"
                mesaj += f"Günlük değişim: {daily}\n"
                mesaj += f"Hacim: {volume}\n"
                mesaj += f"Trend: {trend}\n"
                mesaj += f"Sinyal: {last_signal}\n"
                mesaj += f"Zaman: {sigtime}\n"
                mesaj += f"RSI: {rsi}\n"

                if mesaj:
                    telegram_send(mesaj)

            # Güncel veriyi güncelle
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
            print("Güncelleme tamamlandı.")
        except Exception as e:
            print(f"update_loop hatası: {e}")
            with data_lock:
                LATEST_DATA = {"status": "error", "error": "Hata oluştu"}
        time.sleep(60)

@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

if __name__ == "__main__":
    print("Uygulama başlatılıyor...")
    sistem_bildir()  # Sistem aktif bildirimi
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    print("Sunucu başlatılıyor...")
    app.run(host="0.0.0.0", port=10000)
