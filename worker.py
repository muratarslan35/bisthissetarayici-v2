import threading

from app import (
    ENGINE_SYMBOLS,
    ENABLE_ULTRA_PRICE_ENGINE,
    TRADING_V3_ENABLED,
    scanner_loop,
    rvol_updater,
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

    print("BIST scanner worker starting")
    scanner_loop()
