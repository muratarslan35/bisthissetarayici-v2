import threading
import time
import requests
import os

def ping_loop():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return

    while True:
        try:
            requests.get(url, timeout=5)
        except:
            pass

        time.sleep(60)


def start_self_ping():
    threading.Thread(target=ping_loop, daemon=True).start()
