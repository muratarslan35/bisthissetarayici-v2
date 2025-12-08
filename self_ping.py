import threading
import time
import requests
import os

def start_self_ping():
    url = os.getenv('SELF_URL')
    interval = int(os.getenv('SELF_PING_INTERVAL', 300))
    if not url:
        print("SELF_URL ortam değişkeni ayarlı değil, self ping başlatılmayacak.")
        return
    
    def ping():
        while True:
            try:
                response = requests.get(url)
                print(f"Self ping gönderildi, durum: {response.status_code}")
            except Exception as e:
                print(f"Self ping hatası: {e}")
            time.sleep(interval)
    
    threading.Thread(target=ping, daemon=True).start()
