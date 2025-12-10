# signal_engine.py
from datetime import datetime
from zoneinfo import ZoneInfo
import threading

# Keys: choose stable keys representing each rule
def process_signals(data, notified_state: dict, tz=None):
    """
    data: list of dict (from fetch_bist.fetch_bist_data)
    notified_state: dict symbol -> set(event_keys)
    tz: timezone object for formatting times (ZoneInfo("Europe/Istanbul"))
    returns: list of (symbol, event_key, message)
    """
    events = []
    if tz is None:
        tz = ZoneInfo("Europe/Istanbul")

    for item in data:
        symbol = item.get("symbol")
        if not symbol:
            continue
        # ensure structure
        if symbol not in notified_state:
            notified_state[symbol] = set()

        current_price = item.get("current_price")
        rsi = item.get("RSI")
        last_signal = item.get("last_signal")
        support_break = item.get("support_break")
        resistance_break = item.get("resistance_break")
        three_peak = item.get("three_peak_break")
        green_11 = item.get("green_mum_11")
        green_15 = item.get("green_mum_15")
        ma_breaks = item.get("ma_breaks", {})  # e.g. MA20: price_above
        ma_values = item.get("ma_values", {})

        # Build a helper to notify once per event_key
        def notify_once(key, text):
            if key not in notified_state[symbol]:
                notified_state[symbol].add(key)
                events.append((symbol, key, text))

        def clear_if_false(key):
            if key in notified_state[symbol]:
                notified_state[symbol].remove(key)

        # 1) RSI extremes
        if rsi is not None:
            if rsi < 20:
                notify_once("RSI_LT_20", f"🔻 {symbol} RSI düşük: {rsi:.2f} (Fiyat: {current_price} TL)")
            else:
                clear_if_false("RSI_LT_20")

            if rsi > 80:
                notify_once("RSI_GT_80", f"🔺 {symbol} RSI yüksek: {rsi:.2f} (Fiyat: {current_price} TL)")
            else:
                clear_if_false("RSI_GT_80")

        # 2) AL / SAT (based on last_signal)
        if last_signal == "AL":
            notify_once("SIGNAL_AL", f"🟢 {symbol} AL sinyali! RSI: {rsi:.2f} Fiyat: {current_price} TL")
        else:
            clear_if_false("SIGNAL_AL")

        if last_signal == "SAT":
            notify_once("SIGNAL_SAT", f"🔴 {symbol} SAT sinyali! RSI: {rsi:.2f} Fiyat: {current_price} TL")
        else:
            clear_if_false("SIGNAL_SAT")

        # 3) support/resistance
        if support_break:
            notify_once("SUPPORT_BREAK", f"🟢 {symbol} destek kırıldı! Fiyat: {current_price} TL")
        else:
            clear_if_false("SUPPORT_BREAK")
        if resistance_break:
            notify_once("RESISTANCE_BREAK", f"🔴 {symbol} direnç kırıldı! Fiyat: {current_price} TL")
        else:
            clear_if_false("RESISTANCE_BREAK")

        # 4) three peak
        if three_peak:
            notify_once("THREE_PEAK", f"⚠️ {symbol} üç tepe kırılımı gerçekleşti! Fiyat: {current_price} TL")
        else:
            clear_if_false("THREE_PEAK")

        # 5) 11:00 ve 15:00 yeşil mum (if True notify)
        if green_11:
            notify_once("GREEN_11", f"🟢 {symbol} 11:00'de yeşil mum oluştu. Fiyat: {current_price} TL")
        else:
            clear_if_false("GREEN_11")
        if green_15:
            notify_once("GREEN_15", f"🟢 {symbol} 15:00'te yeşil mum oluştu. Fiyat: {current_price} TL")
        else:
            clear_if_false("GREEN_15")

        # 6) MA break notifications (MA20,50,100,200 price_above/price_below)
        for k, v in ma_breaks.items():
            # create event key like "MA20_above"
            if v == "price_above":
                notify_once(f"{k}_ABOVE", f"📈 {symbol} {k} üzerinde. ({v}) Fiyat: {current_price} TL")
            elif v == "price_below":
                notify_once(f"{k}_BELOW", f"📉 {symbol} {k} altında. ({v}) Fiyat: {current_price} TL")
            else:
                # clear for that MA
                clear_if_false(f"{k}_ABOVE"); clear_if_false(f"{k}_BELOW")

        # 7) combination rules you asked for:
        # - If any symbol has: daily candle green (we approximate with positive daily_change) AND 4H green OR 2nd green etc.
        # Since we use 15m bars only, we provide a heuristic: if daily %>0 and green_11 or green_15 -> notify composite
        daily_change = item.get("daily_change", "0")
        # daily_change string like "%2.71" -> extract
        try:
            dchg = float(str(daily_change).replace("%",""))
        except:
            dchg = 0.0
        # Composite condition sample:
        if dchg > 0 and (green_11 or green_15):
            notify_once("COMBO_DAILY_4H", f"✅ Kombine: {symbol} günlük mum pozitif (%{dchg}) ve saat 11/15'te yeşil mum var. Fiyat: {current_price} TL")

    return events
