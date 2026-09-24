import time
from collections import defaultdict, deque
from threading import Lock
import json
import os

# ======================================================
# CONFIG
# ======================================================
MIN_TICKS = 30
WINDOW_SECONDS = 60
MAX_TICKS = 600
SAVE_INTERVAL = 15
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "data", "volume_cache.json")

# ======================================================
# GLOBAL
# ======================================================

TICKS = defaultdict(lambda: deque(maxlen=MAX_TICKS))
LAST_PRICE = {}
LOCK = Lock()

RUNNING = True

# ======================================================
# LOAD CACHE (RESTART SAFE)
# ======================================================

def load_volume_cache():

    if not os.path.exists(CACHE_FILE):
        return

    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)

        now = time.time()

        with LOCK:
            for sym, ticks in data.items():

                for t in ticks:
                    # sadece son 3 dakika al
                    if now - t["ts"] <= 180:
                        TICKS[sym].append(t)

        print(f"📦 volume cache loaded: {len(TICKS)}")

    except Exception as e:
        print("volume load error:", e)

# ======================================================
# SAVE CACHE (THREAD)
# ======================================================

def save_volume_cache():

    while RUNNING:
        try:

            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

            with LOCK:
                data = {
                    sym: list(ticks)[-200:]
                    for sym, ticks in TICKS.items()
                }

            with open(CACHE_FILE, "w") as f:
                json.dump(data, f)

        except Exception as e:
            print("volume save error:", e)

        time.sleep(SAVE_INTERVAL)

# ======================================================
# UPDATE TICK (🔥 FIXED - HER ZAMAN YAZAR)
# ======================================================

def update_tick(symbol, price):

    now = time.time()

    with LOCK:

        # 🔥 İLK GELİŞ
        if symbol not in LAST_PRICE:
            LAST_PRICE[symbol] = price

        # 🔥 KRİTİK: HER ZAMAN TICK EKLE (ESKİ BUG FIX)
        TICKS[symbol].append({
            "price": price,
            "ts": now
        })

        LAST_PRICE[symbol] = price

# ======================================================
# REAL VOLUME (1 DK)
# ======================================================

def get_tick_volume(symbol):

    now = time.time()

    with LOCK:
        ticks = TICKS.get(symbol, [])

        return sum(1 for t in ticks if now - t["ts"] <= WINDOW_SECONDS)

# ======================================================
# SMART RVOL (STABLE)
# ======================================================

def get_rvol(symbol):

    now = time.time()

    with LOCK:
        ticks = list(TICKS.get(symbol, []))

    if len(ticks) < MIN_TICKS:
        return 0

    last_60 = sum(1 for t in ticks if now - t["ts"] <= 60)
    prev_180 = sum(1 for t in ticks if 60 < now - t["ts"] <= 180)

    if prev_180 <= 0:
        return 0

    base = prev_180 / 2

    if base <= 0:
        return 0

    rvol = last_60 / base

    # 🔥 clamp (uç değerleri kontrol altına al)
    if rvol > 10:
        rvol = 10

    return round(rvol, 2)

# ======================================================
# SPIKE
# ======================================================

def detect_volume_spike(symbol):

    return get_rvol(symbol) >= 2
