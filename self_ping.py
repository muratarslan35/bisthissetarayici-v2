import threading
import time
import requests
import os

def start_self_ping():
    url = os.getenv("SELF_URL")
    interval = int(os.getenv("SELF_PING_INTERVAL", 300))

    if not url:
        return

    def loop():
        while True:
            try:
                requests.get(url, timeout=10)
            except:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
