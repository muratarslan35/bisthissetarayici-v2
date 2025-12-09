import requests
import time

def start_self_ping():
    URL = "https://bisthissetarayici-v2.onrender.com"
    while True:
        try:
            requests.get(URL, timeout=5)
        except:
            pass
        time.sleep(60)
