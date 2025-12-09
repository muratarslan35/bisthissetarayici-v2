from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")

def tz_now():
    return datetime.now(TZ)

def complex_daily_4h_logic(daily_df, h4_df):
    """
    daily_df: günlük periyot (pandas Series/DF with Open/Close)
    h4_df: 4H periyot (pandas Series/DF)
    Return True/False if complex condition met:
    - bugünkü günlük mum içinde 'ilk yeşil' (evet) ve 4H içinde art arda 2. yeşil gelirse vb...
    Bu fonksiyon bir örnek; fetch_bist içinde çağrılmak üzere hazırlandı.
    """
    try:
        # Basit örnek: günlükde sondan 2. bar yeşil (yesterday green) ve bugünkü bar da yeşil -> True
        if daily_df is None or h4_df is None:
            return False
        # günlük open/close serileri varsa
        if "Open" in daily_df.columns and "Close" in daily_df.columns:
            today = daily_df.iloc[-1]
            prev = daily_df.iloc[-2] if len(daily_df) >= 2 else None
            today_green = today["Close"] > today["Open"]
            prev_green = prev is not None and prev["Close"] > prev["Open"]
        else:
            return False

        # 4h içinde en son 2 barın yeşil olup olmadığı
        if "Open" in h4_df.columns and "Close" in h4_df.columns:
            last2 = h4_df.iloc[-2:]
            green_count = sum((last2["Close"] > last2["Open"]).tolist())
            fourh_condition = green_count >= 2
        else:
            fourh_condition = False

        # Örnek mantık:
        return (prev_green and today_green and fourh_condition) or (today_green and fourh_condition)
    except Exception:
        return False
