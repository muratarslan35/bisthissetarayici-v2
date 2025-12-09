import threading
import time
import requests
import os
import logging

logger = logging.getLogger("self_ping")

def start_self_ping():
    url = os.getenv('SELF_URL')
    interval = int(os.getenv('SELF_PING_INTERVAL', '240'))
    if not url:
        logger.info("[self_ping] No SELF_URL set; skipping self-ping.")
        return

    def ping_loop():
        logger.info(f"[self_ping] Will ping {url} every {interval}s")
        while True:
            try:
                requests.get(url, timeout=10)
            except Exception as e:
                logger.exception(f"[self_ping] ping error: {e}")
            time.sleep(interval)

    threading.Thread(target=ping_loop, daemon=True).start()
    logger.info("[self_ping] Started.")
