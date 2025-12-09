import threading
import time
import os
import requests

def start_self_ping():
    url = os.getenv("SELF_URL")
    interval = int(os.getenv("SELF_PING_INTERVAL", "240"))
    if not url:
        print("[self_ping] No SELF_URL set; skipping self-ping.", flush=True)
        return
    def ping_loop():
        print("[self_ping] self-ping loop starting to", url, "every", interval, "s", flush=True)
        while True:
            try:
                requests.get(url, timeout=10)
            except Exception as e:
                print("[self_ping] ping error:", e, flush=True)
            time.sleep(interval)
    threading.Thread(target=ping_loop, daemon=True).start()
