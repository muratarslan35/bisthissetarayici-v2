from flask import Flask, jsonify, send_from_directory
import threading
import time
from fetcher import fetch_bist_data
from self_ping import start_self_ping
import os

app = Flask(__name__)

LATEST_DATA = {"status": "init", "data": None}

# ----------------
# BACKGROUND FETCH
# ----------------
def loop_fetch():
    global LATEST_DATA
    interval = int(os.getenv("FETCH_INTERVAL", 60))

    while True:
        try:
            data = fetch_bist_data()
            LATEST_DATA = {
                "status": "ok",
                "timestamp": int(time.time()),
                "data": data
            }
        except Exception as e:
            LATEST_DATA = {"status": "error", "error": str(e)}
        time.sleep(interval)

# ----------------
# ROUTES
# ----------------
@app.route("/")
def dashboard():
    return send_from_directory("static", "dashboard.html")

@app.route("/api")
def api():
    return jsonify(LATEST_DATA)

# ----------------
# STARTUP
# ----------------
if __name__ == "__main__":
    threading.Thread(target=loop_fetch, daemon=True).start()
    start_self_ping()
    app.run(host="0.0.0.0", port=10000)
