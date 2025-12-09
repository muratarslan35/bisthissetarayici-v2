import threading
import time
import requests
import os

def start_self_ping():
    def loop():
        while True:
            try:
                url = os.getenv("RENDER_EXTERNAL_URL")
                if url:
                    requests.get(url, timeout=5)
            except:
                pass
            time.sleep(120)  # 2 dk
    threading.Thread(target=loop, daemon=True).start()
