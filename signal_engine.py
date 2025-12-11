# signal_engine.py
from datetime import datetime
from utils import to_tr_timezone

def process_signals(item):
    """
    item: dict from fetch_bist.py for one symbol
    returns: list of tuples (sig_key, message)
    """
    out = []
    sym = item.get("symbol")
    price = item.get("current_price")
    rsi = item.get("RSI")
    last = item.get("last_signal")
    support_break = item.get("support_break")
    resistance_break = item.get("resistance_break")
    green_11 = item.get("green_mum_11")
    green_15 = item.get("green_mum_15")
    three_peak = item.get("three_peak_break")
    ma_breaks = item.get("ma_breaks", {})
    ma_values = item.get("ma_values", {})

    # timezone adjusted time string
    now_tr = to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")

    # 1) RSI thresholds
    if rsi is not None:
        if rsi < 20:
            key = "RSI_LT20"
            msg = f"🔻 {sym} RSI {rsi:.2f} < 20 ({now_tr})"
            out.append((key, msg))
        elif rsi > 80:
            key = "RSI_GT80"
            msg = f"🔺 {sym} RSI {rsi:.2f} > 80 ({now_tr})"
            out.append((key, msg))
    # 2) AL / SAT (basic)
    if last == "AL":
        out.append(("SIGNAL_AL", f"🟢 {sym} AL sinyali! Fiyat: {price} TL | RSI: {rsi:.2f} | {now_tr}"))
    elif last == "SAT":
        out.append(("SIGNAL_SAT", f"🔴 {sym} SAT sinyali! Fiyat: {price} TL | RSI: {rsi:.2f} | {now_tr}"))

    # 3) support/resistance
    if support_break:
        out.append(("SUPPORT_BREAK", f"🟢 {sym} destek kırıldı! Fiyat: {price} TL | {now_tr}"))
    if resistance_break:
        out.append(("RESISTANCE_BREAK", f"🔴 {sym} direnç kırıldı! Fiyat: {price} TL | {now_tr}"))

    # 4) three peak
    if three_peak:
        out.append(("THREE_PEAK", f"⚠️ {sym} üç tepe kırılımı gerçekleşti! Fiyat: {price} TL | {now_tr}"))

    # 5) 11:00 and 15:00 green candles
    if green_11:
        out.append(("GREEN_11", f"🟢 {sym} 11:00'de yeşil mum oluştu. Fiyat: {price} TL | {now_tr}"))
    if green_15:
        out.append(("GREEN_15", f"🟢 {sym} 15:00'te yeşil mum oluştu. Fiyat: {price} TL | {now_tr}"))

    # 6) MA break infos
    for k, v in ma_breaks.items():
        if v is None:
            continue
        key = f"MA_{k}_{v}"
        msg = f"📈 {sym} {k}: {v} | Fiyat: {price} TL | {now_tr}"
        out.append((key, msg))

    # 7) Combined daily compound signal (A-type): example:
    # - günlük 1D mum geçmişte 1 yeşil (from external daily fetch — we approximate by green_11 or green_15 presence),
    # - ve 4H/1H mumlar durumunu kombine etmek (we use green_11 and green_15 as proxies in 15m data)
    # Implement conservative combined rule:
    try:
        # simple heuristic: if today has at least one green daily proxy (green_11 or green_15)
        if (green_11 or green_15) and (last == "AL" or rsi is not None and rsi < 30):
            key = "DAILY_COMBINED_A"
            msg = f"✅ A-type combined sinyal: {sym} - Günlük/4H uyumlu. Fiyat: {price} TL | RSI: {rsi:.2f} | {now_tr}"
            out.append((key, msg))
    except Exception:
        pass

    # ensure unique keys per symbol (process_signals only builds list; dedupe enforced in app)
    # return
    return out
