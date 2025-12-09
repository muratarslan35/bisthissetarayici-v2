import threading
import time
import requests
import os

def start_self_ping():
    url = os.getenv("SELF_URL")
    if not url:
        # no SELF_URL provided -> skip
        print("[self_ping] No SELF_URL set; skipping self-ping.")
        return

    interval = int(os.getenv("SELF_PING_INTERVAL", "240"))
    def ping_loop():
        print("[self_ping] starting ping loop to", url, "interval:", interval)
        while True:
            try:
                requests.get(url, timeout=10)
                # print basic dot for visibility
                print("[self_ping] ping ok", time.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as e:
                print("[self_ping] ping error:", e)
            time.sleep(interval)
    threading.Thread(target=ping_loop, daemon=True).start()
