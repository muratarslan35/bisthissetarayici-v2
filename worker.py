import threading

from app import (
    ENGINE_SYMBOLS,
    ENABLE_ULTRA_PRICE_ENGINE,
    TRADING_V3_ENABLED,
    scanner_loop,
    rvol_updater,
    kap_watch_loop,
    universe_watch_loop,
    fast_lane_loop,
)
from ultra_price_engine import start_engine
from volume_engine import load_volume_cache, save_volume_cache


if __name__ == "__main__":
    if ENABLE_ULTRA_PRICE_ENGINE:
        print("OPTIONAL ultra price engine enabled")
        start_engine(ENGINE_SYMBOLS)

    if not TRADING_V3_ENABLED:
        # Backward-compatible legacy mode only.
        load_volume_cache()
        threading.Thread(target=save_volume_cache, daemon=True).start()
        threading.Thread(target=rvol_updater, daemon=True).start()

    threading.Thread(target=kap_watch_loop, daemon=True, name="kap-watch").start()
    threading.Thread(target=universe_watch_loop, daemon=True, name="universe-watch").start()
    threading.Thread(target=fast_lane_loop, daemon=True, name="fast-lane").start()

    print("BIST scanner worker starting")
    scanner_loop()
