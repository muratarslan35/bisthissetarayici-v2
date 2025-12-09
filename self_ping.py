import threading
import time
import requests
import os

def start_self_ping():
    url = os.getenv("SELF_URL")
    if not url:
        print("[self_ping] No SELF_URL set; skipping self-ping.")
        return
    interval = int(os.getenv("SELF_PING_INTERVAL", "300"))
    print("[self_ping] Self-ping started ->", url, "interval", interval)
    def ping_loop():
        while True:
            try:
                requests.get(url, timeout=10)
            except Exception as e:
                print("[self_ping] ping error:", e)
            time.sleep(interval)
    threading.Thread(target=ping_loop, daemon=True).start()
