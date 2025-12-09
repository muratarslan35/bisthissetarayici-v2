# self_ping.py
import time, requests

def start_self_ping(url):
    while True:
        try:
            requests.get(url, timeout=5)
        except:
            pass
        time.sleep(300)
