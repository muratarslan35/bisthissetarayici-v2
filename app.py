import os
import json
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import threading
import time
from fetch_bist import fetch_bist_data

app = Flask(__name__, static_folder="static")
CORS(app)

# ---- GLOBAL CACHE ----
last_bist_data = {
    "XU100": None,
    "XU030": None,
    "last_update": None
}


# ===============================
#   ROOT → dashboard.html
# ===============================
@app.route("/")
def index():
    return send_from_directory("static", "dashboard.html")


# ===============================
#   API → BIST verileri
# ===============================
@app.route("/api/bist")
def api_bist():
    return jsonify(last_bist_data)


# ===============================
#   Manual Trigger
# ===============================
@app.route("/api/refresh", methods=["POST"])
def manual_refresh():
    global last_bist_data
    last_bist_data = fetch_bist_data()
    return jsonify({"status": "ok", "updated": last_bist_data})


# ===============================
#   Background Auto Updater
# ===============================
def background_updater():
    global last_bist_data
    while True:
        try:
            last_bist_data = fetch_bist_data()
            print("BIST verileri güncellendi.")
        except Exception as e:
            print("Arka plan güncelleme hatası:", e)
        time.sleep(60)  # her 1 dakikada bir çek


# ===============================
#   SELF-PING (Render Uyumluluğu)
# ===============================
def keep_alive():
    while True:
        try:
            import requests
            url = os.environ.get("RENDER_EXTERNAL_URL", None)
            if url:
                requests.get(url, timeout=5)
                print("Self-ping gönderildi:", url)
        except:
            pass
        time.sleep(250)  # 4 dakikada bir ping


# ===============================
#   APP START
# ===============================
if __name__ == "__main__":

    # background fetcher
    t1 = threading.Thread(target=background_updater, daemon=True)
    t1.start()

    # Render self-ping
    t2 = threading.Thread(target=keep_alive, daemon=True)
    t2.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
