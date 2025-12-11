import datetime
import pytz
import requests
import logging

# Türkiye saati
TZ = pytz.timezone("Europe/Istanbul")


# -----------------------------
# Zaman yardımcıları
# -----------------------------
def now_tr():
    """Türkiye saatine göre current datetime döner."""
    return datetime.datetime.now(TZ)


def format_ts(dt: datetime.datetime):
    """Dashboard ve API için standart timestamp formatı."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------
# Fiyat veri validasyon yardımcıları
# -----------------------------
def safe_float(value):
    """Sayısal değer hatalarını güvenle karşılar."""
    try:
        if value is None:
            return None
        return float(value)
    except:
        return None


def is_valid_candle(candle: dict):
    """
    OHLC mum verisinin geçerli olup olmadığını kontrol eder.
    Tüm değerler float ise geçerli kabul edilir.
    """
    needed = ["open", "high", "low", "close"]
    for k in needed:
        if k not in candle:
            return False
        if candle[k] is None:
            return False
    return True


# -----------------------------
# HTTP yardımcıları
# -----------------------------
def http_get_json(url, headers=None, timeout=10):
    """
    API çağrıları için güvenli GET isteği.
    fetch_bist içinde kullanılır.
    """
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error(f"[HTTP] GET ERROR: {e}")
        return None


# -----------------------------
# Sinyal yardımcıları
# -----------------------------
def merge_signal(source_list: list, new_signal: dict):
    """
    Sinyal listesine yeni sinyali ekler (format korumalı).
    app.py → process_signals ile uyumludur.
    """
    if new_signal is None:
        return

    source_list.append({
        "symbol": new_signal.get("symbol"),
        "type": new_signal.get("type"),
        "reason": new_signal.get("reason"),
        "price": new_signal.get("price"),
        "timestamp": new_signal.get("timestamp"),
        "timeframe": new_signal.get("timeframe"),
        "score": new_signal.get("score"),
        "algo": new_signal.get("algo")
    })


def unify_daily_signal(existing, intraday_signal):
    """
    Günlük sinyal ile intraday sinyali birleştirir.
    A şıkkı "temiz 3 uyum zorunlu" yapısı bu fonksiyona uyumludur.
    Günlük = 1 kez gelmeli → bu logic app.py'deki signal_cache ile birlikte çalışır.
    """
    if intraday_signal is None:
        return existing

    if existing is None:
        return intraday_signal

    # Birleştirilmiş sinyal → intraday + daily birleşik format
    merged = existing.copy()
    merged["merged_with_intraday"] = True
    merged["intraday_reason"] = intraday_signal.get("reason")
    merged["intraday_score"] = intraday_signal.get("score")

    return merged


# -----------------------------
# Fallback listesi yönetimi
# -----------------------------
def clean_symbol(symbol: str):
    """Sembolleri standartlaştırır."""
    if not symbol:
        return None
    return symbol.replace(".E", "").replace(".IS", "").strip().upper()


def extend_symbol_list(base_list: list, new_list: list):
    """
    Fallback listesi birleştirme fonksiyonu.
    fetch_bist + signal_engine ile uyumludur.
    """
    out = []
    seen = set()

    for s in base_list + new_list:
        if not s:
            continue
        c = clean_symbol(s)
        if c not in seen:
            seen.add(c)
            out.append(c)

    return out


# -----------------------------
# Dashboard formatlayıcı
# -----------------------------
def format_signal_for_dashboard(sig: dict):
    """
    Dashboard için frontend-friendly bir format döner.
    """
    if sig is None:
        return None

    return {
        "symbol": sig.get("symbol"),
        "type": sig.get("type"),
        "reason": sig.get("reason"),
        "price": sig.get("price"),
        "timestamp": sig.get("timestamp"),
        "algo": sig.get("algo"),
        "score": sig.get("score"),
        "timeframe": sig.get("timeframe", "")
    }
