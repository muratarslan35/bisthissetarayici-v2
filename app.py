
import time
import os
from flask import Flask, jsonify, send_from_directory
from fetch_bist import fetch_bist_data
from self_ping import start_self_ping

app = Flask(__name__)

LATEST_DATA = {"status": "init", "data": None}
data_lock = threading.Lock()

def update_data():
    global LATEST_DATA
    interval = int(os.getenv("FETCH_INTERVAL", 60))
    while True:
        try:
            data = fetch_bist_data()
            with data_lock:
                LATEST_DATA = {
                    "status": "ok",
                    "timestamp": int(time.time()),
                    "data": data
                }
        except Exception as e:
            with data_lock:
                LATEST_DATA = {"status": "error", "error": str(e)}
        time.sleep(interval)

@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    with data_lock:
        return jsonify(LATEST_DATA)

if __name__ == "__main__":
    threading.Thread(target=update_data, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
