# app.py
from flask import Flask, jsonify
from fetch_bist import fetch_bist_data
from signal_engine import check_signals
from utils import tr_now
import threading
import time

app = Flask(__name__)

LATEST_DATA = []

def background_job():
    global LATEST_DATA
    while True:
        try:
            data = fetch_bist_data()
            LATEST_DATA = data
            check_signals(data)
        except Exception as e:
            print("Background Error:", e)

        time.sleep(60)

threading.Thread(target=background_job, daemon=True).start()

@app.get("/")
def index():
    return jsonify({"status": "OK", "time_tr": tr_now()})

@app.get("/data")
def data():
    return jsonify(LATEST_DATA)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
