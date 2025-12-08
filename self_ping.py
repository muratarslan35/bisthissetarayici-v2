import threading
import time
import requests

PING_URL = "https://YOUR-RENDER-URL.onrender.com"

def self_ping():
    while True:
        try:
            requests.get(PING_URL, timeout=5)
        except:
            pass
        time.sleep(250)  # Render uyku koruması

def start_self_ping():
    threading.Thread(target=self_ping, daemon=True).start()
