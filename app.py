from flask import Flask, jsonify, send_from_directory 
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

# Bu liste, başlangıçta boş, bot ilk mesajla otomatik eklenir
chat_ids = []

# Telegram bot token
TELEGRAM_TOKEN = "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU"

# Bildirim fonksiyonu, herkese mesaj gönderir
def telegram_send(text):
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload)
        except:
            pass

# Kullanıcı /start veya herhangi bir mesaj yolladığında, ID'yi kaydet
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return "OK"
    try:
        chat_id = data['message']['chat']['id']
        # Chat ID'yi listeye ekle
        if chat_id not in chat_ids:
            chat_ids.append(chat_id)
            print(f"Yeni chat ID eklendi: {chat_id}")
            # Kullanıcıya onay mesajı
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": "Merhaba! Bildirimler size de geliyor.", "parse_mode": "HTML"}
            )
    except:
        pass
    return "OK"

# Sistem başlangıç bildirimi
def sistem_bildir():
    telegram_send("🤖 Sistem başlatıldı ve aktif!")

# Ana veri güncelleme döngüsü
def update_loop():
    while True:
        try:
            data = fetch_bist_data()
            for his in data:
                # RSI ve sapma kontrolleri
                rsi = his.get("RSI")
                if rsi is not None:
                    if rsi < 20:
                        telegram_send(f"🔻 {his['symbol']} RSI {rsi:.2f} 20'nin altında!\n")
                    elif rsi > 80:
                        telegram_send(f"🔺 {his['symbol']} RSI {rsi:.2f} 80'in üzerinde!\n")
                # Sapma bildirimi
                sapma = his.get("sapma_pct")
                if sapma is not None and abs(sapma) > 5:
                    telegram_send(f"🔎 {his['symbol']} Sapma: {sapma:.2f}% (Yfinance & TradingView)\n")
                # Diğer uyarılar ve sinyaller
                check_and_notify(his)
            # Güncel veriyi kaydet
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
        except:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": "Hata oluştu"}
        time.sleep(60)

# Dashboard ve API
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

# Sistem başlatıldığında otomatik mesaj
@app.before_first_request
def sistem_baslangici():
    telegram_send("🤖 Sistem aktif ve çalışıyor!")

if __name__ == "__main__":
    # İlk mesaj (kullanıcılar bu URL'e mesaj gönderdiğinde ID kaydedilir)
    threading.Thread(target=system_bildir).start()
    # Veri güncelleme döngüsü
    threading.Thread(target=update_loop, daemon=True).start()
    # Self ping (sunucu kapanmasını engellemek için)
    start_self_ping()
    # Uygulamayı başlat
    app.run(host="0.0.0.0", port=10000)
