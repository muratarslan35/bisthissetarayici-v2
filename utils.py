# utils.py
from datetime import datetime, timedelta

# Tekrar bildirim engelleme için hafıza
LAST_SENT = {}

def tr_now():
    """Her zaman GMT+3 döndürür."""
    return (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

def should_send(symbol, signal_type):
    """
    Aynı sinyalin aynı hissede tekrarını engeller.
    symbol+signal_type birlikte kontrol edilir.
    """
    key = f"{symbol}_{signal_type}"
    now = datetime.utcnow()

    last = LAST_SENT.get(key)
    if last is None:
        LAST_SENT[key] = now
        return True

    # 1 saat içinde tekrar göndermesin
    if (now - last).total_seconds() > 3600:
        LAST_SENT[key] = now
        return True

    return False
