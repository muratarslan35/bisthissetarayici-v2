import threading 
import time
import requests
import os

def start_self_ping():
    url = os.getenv('SELF_URL')
    interval = int(os.getenv('SELF_PING_INTERVAL', 300))
    if not url:
        return
    def ping_loop():
        while True:
            try:
                requests.get(url)
            except:
                pass
            time.sleep(interval)
    threading.Thread(target=ping_loop, daemon=True).start()
