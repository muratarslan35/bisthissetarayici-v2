import threading 
import time
import requests
import os

def keep_alive():
    url = os.getenv("SELF_URL")
    wait = int(os.getenv("SELF_PING_INTERVAL", 240))
    if not url:
        return
    while True:
        try:
            requests.get(url)
        except:
            pass
        time.sleep(wait)

def start_self_ping():
    t = threading.Thread(target=keep_alive, daemon=True)
    t.start()
