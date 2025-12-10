# self_ping.py
import threading
import time
import requests
import os

def start_self_ping():
    url = os.getenv('SELF_URL')
    interval = int(os.getenv('SELF_PING_INTERVAL', 240))
    if not url:
        print("[self_ping] No SELF_URL set; skipping self-ping.")
        return
    def ping_loop():
        print("[self_ping] Self-ping started (if SELF_URL set).")
        while True:
            try:
                requests.get(url, timeout=10)
            except Exception as e:
                print("[self_ping] ping error:", e)
            time.sleep(interval)
    threading.Thread(target=ping_loop, daemon=True, name="self_ping").start()
