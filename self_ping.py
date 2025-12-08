import threading
import time
import requests
import os

def start_self_ping():
    url = os.getenv('SELF_URL')
    interval = int(os.getenv('SELF_PING_INTERVAL', 300))
    if not url:
        return
    
    def ping():
        while True:
            try:
                requests.get(url)
            except Exception as e:
                print(f"Self ping hatası: {e}")
            time.sleep(interval)
    
    threading.Thread(target=ping, daemon=True).start()
