from flask import Flask, jsonify, send_from_directory 
import threading
import time
import requests
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)
LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

# Bildirim fonksiyonu
def telegram_send(text):
    url = f"https://api.telegram.org/bot8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU/sendMessage"
    payload = {"chat_id": 661794787, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except:
        pass

# Sistem başlatma bildirimi
def sistem_bildir():
    telegram_send("🤖 Sistem aktif ve çalışıyor!")

def update_loop():
    while True:
        try:
            data = fetch_bist_data()
            for his in data:
                # Sinyal ve uyarı kontrolü
                rsi = his.get("RSI")
                if rsi is not None:
                    if rsi < 20:
                        telegram_send(f"🔻 {his['symbol']} RSI {rsi:.2f} 20'nin altında!\n")
                    elif rsi > 80:
                        telegram_send(f"🔺 {his['symbol']} RSI {rsi:.2f} 80'in üzerinde!\n")
                # Sapma ve diğer uyarılar
                # (Burada sapma ve diğer kriterler de eklenebilir)
                # Örneğin, destek kırılımı, üç tepe vb.
                # ...
                # Son olarak, detaylı analizi yapıp mesaj atabilirsiniz.
                # Yukarıdaki `check_and_notify()` fonksiyonunu çağırabilirsiniz.
                # ...
                check_and_notify(his)
            with data_lock:
                LATEST_DATA = {"status": "ok", "timestamp": int(time.time()), "data": data}
        except:
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
    sistem_bildir()  # Başlangıç bildirimi
    threading.Thread(target=update_loop, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
