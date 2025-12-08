import os
import time
import threading
import requests

def start_self_ping():
    # Render ortamı URL'si varsa otomatik al
    base_url = os.environ.get("RENDER_EXTERNAL_URL")

    if base_url:
        base_url = base_url.rstrip("/")
        ping_url = f"{base_url}"
        print(f"[SELF-PING] Render URL algılandı: {ping_url}")
    else:
        # Lokal çalıştırma için
        ping_url = "http://localhost:10000"
        print(f"[SELF-PING] Lokal ortam algılandı: {ping_url}")

    def loop():
        while True:
            try:
                print("[SELF-PING] Pinging:", ping_url)
                requests.get(ping_url, timeout=8)
            except Exception as e:
                print("[SELF-PING] Hata:", e)
            time.sleep(60)  # 1 dk'da 1 ping

    threading.Thread(target=loop, daemon=True).start()
