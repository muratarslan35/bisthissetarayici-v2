from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import json
import os
from threading import Lock
import time

from utils import (
    detect_three_peaks,
    detect_support_resistance_break,
    get_last_resistance
)
from bist_market_filters import get_brut_list
from volume_engine import get_rvol

_STORE_LOCK = Lock()

def make_key(*parts):
    return "|".join(str(p) for p in parts)

# ======================================================
# PERSIST CONFIG (HAFTALIK + CUMA)
# ======================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

DAILY_STATE_FILE = os.path.join(DATA_DIR, "daily_state.json")
WEEKLY_STATE_FILE = os.path.join(DATA_DIR, "weekly_state.json")


def load_weekly_state():
    if not os.path.exists(WEEKLY_STATE_FILE):
        return {}, {}

    try:
        with open(WEEKLY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("weekly", {}), data.get("friday_prices", {})
    except Exception:
        return {}, {}


def save_weekly_state():
    os.makedirs(DATA_DIR, exist_ok=True)

    with _STORE_LOCK:
        with open(WEEKLY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "weekly": WEEKLY_SUCCESS_TRACKER,
                    "friday_prices": FRIDAY_CLOSE_PRICES,
                },
                f,
                ensure_ascii=False,
                indent=2,
                default=str
            )


def load_daily_state():
    if not os.path.exists(DAILY_STATE_FILE):
        return {}

    try:
        with open(DAILY_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_daily_state():
    os.makedirs(DATA_DIR, exist_ok=True)

    with _STORE_LOCK:
        with open(DAILY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                DAILY_SUCCESS_TRACKER,
                f,
                ensure_ascii=False,
                indent=2,
                default=str
            )

# ======================================================
# GLOBALS
# ======================================================

REPEAT_BLOCK_MINUTES = 45
TARGET_PCT = 0.01
POWER_STRENGTH_THRESHOLD = 20
SCALPING_STRENGTH_THRESHOLD = 35
TP1_PCT = 0.01    # %1
TP2_PCT = 0.03    # %3
TP3_PCT = 0.05    # %5
TP1_HIT_SYMBOLS = {}
DAILY_SYMBOL_ALGO = {}

LAST_SENT = {}
LAST_SIGNAL_STATE = {}
LAST_PUBLISHED_STATE = {}

DAILY_SUCCESS_TRACKER = load_daily_state()
DAILY_CLOSE_PRICES = {}
REENTRY_DAILY_TRACKER = {}
DAILY_SENT = {}

WEEKLY_SUCCESS_TRACKER, FRIDAY_CLOSE_PRICES = load_weekly_state()

TR_TZ = ZoneInfo("Europe/Istanbul")

BRUT_MAP = {}
LAST_BRUT_UPDATE = None
# ======================================================
# ZAMAN
# ======================================================

def tr_now():
    return datetime.now(TR_TZ)


def today_key():
    return tr_now().date().isoformat()


def week_key():
    now = tr_now()
    monday = now - timedelta(days=now.weekday())
    return monday.date().isoformat()


def fmt(v):
    return round(v, 2) if isinstance(v, (int, float)) else None


def is_market_close_final_window():
    now = tr_now()
    return now.hour == 18 and now.minute >= 10
# ======================================================
# BRÜT TAKAS KONTROL
# ======================================================

def refresh_brut_list():

    global BRUT_MAP, LAST_BRUT_UPDATE

    now = tr_now()

    # aynı gün tekrar çekme
    if LAST_BRUT_UPDATE and LAST_BRUT_UPDATE.date() == now.date():
        return

    try:

        print("📡 BRÜT TAKAS LİSTESİ GÜNCELLENİYOR...")

        BRUT_MAP = get_brut_list()

        LAST_BRUT_UPDATE = now

        print(f"✅ BRÜT TAKAS YÜKLENDİ → {len(BRUT_MAP)} hisse")

    except Exception as e:
        print(f"⚠ BRÜT TAKAS ÇEKİLEMEDİ → {e}")
# ======================================================
# RESET MEKANİZMALARI
# ======================================================

def reset_daily_success_if_needed():
    key = today_key()
    if key not in DAILY_SUCCESS_TRACKER:
        DAILY_SUCCESS_TRACKER.clear()
        DAILY_SUCCESS_TRACKER[key] = {}
        save_daily_state()


def reset_weekly_success_if_needed():
    global WEEKLY_SUCCESS_TRACKER

    new_w_key = week_key()

    # Eğer bu hafta zaten varsa → hiçbir şey yapma
    if new_w_key in WEEKLY_SUCCESS_TRACKER:
        return

    # 🔁 Önceki haftalardan HIT OLMAYANLARI topla
    carry_over = {}

    for wk, week_data in WEEKLY_SUCCESS_TRACKER.items():
        if not isinstance(week_data, dict):
            continue

        for k, v in week_data.items():
            if not v.get("hit"):
                carry_over[k] = v

    # 🧹 Her şeyi temizle
    WEEKLY_SUCCESS_TRACKER.clear()

    # 🆕 Yeni haftayı oluştur ve devredenleri ekle
    WEEKLY_SUCCESS_TRACKER[new_w_key] = carry_over

    # 📉 Cuma fiyatları sıfırlanır (yeni hafta için)
    FRIDAY_CLOSE_PRICES.clear()

    save_weekly_state()

def reset_reentry_daily_if_needed():
    key = today_key()
    if key not in REENTRY_DAILY_TRACKER:
        REENTRY_DAILY_TRACKER.clear()
        REENTRY_DAILY_TRACKER[key] = {}

def reset_tp1_tracker_if_needed():
    today = today_key()

    for k in list(TP1_HIT_SYMBOLS.keys()):
        if TP1_HIT_SYMBOLS[k] != today:
            del TP1_HIT_SYMBOLS[k]

    # 🔥 EKLE
    for k in list(DAILY_SENT.keys()):
        if DAILY_SENT[k] != today:
            del DAILY_SENT[k]

def reset_daily_algo_tracker_if_needed():
    today = today_key()

    for k in list(DAILY_SYMBOL_ALGO.keys()):
        if k[0] != today:
            del DAILY_SYMBOL_ALGO[k]

# ======================================================
# HELPER SEVİYELERİ
# ======================================================

HELPER_LEVELS = {
    "ORDER BLOCK": "A",
    "1H YAPISAL KIRILIM": "A",
    "4H TREND KIRILIMI": "A",
    "4H SIKIŞMA KIRILIMI (ONAYLI)": "A",
    "MOST 4H YUKARI": "A",
    "L4 MAJÖR KIRILIM": "A",

    "ÇOKLU ZAMAN EMA ONAYI": "B",
    "GOLDEN CROSS": "B",
    "L3 GÜÇLÜ KIRILIM": "B",
    "MOST 1D YUKARI": "B",

    "RSI DÜŞÜK": "C",
    "RSI AŞIRI SATIM": "C",
    "3LÜ TEPE": "C",
    "L2 KIRILIM": "C",
    "MOST KIRILIMI": "C",
}

HELPER_DESCRIPTIONS = {
    "ORDER BLOCK": "Kurumsal alım bölgesi",
    "1H YAPISAL KIRILIM": "Saatlik yapıda kalıcı direnç aşımı",
    "4H TREND KIRILIMI": "4 saatlik ana trend yukarı kırıldı",
    "4H SIKIŞMA KIRILIMI (ONAYLI)": "4H dar bant sıkışması sonrası hacimli kırılım",
    "MOST 4H YUKARI": "4 saatlik MOST trendi yukarı",

    "ÇOKLU ZAMAN EMA ONAYI": "15m–1H–4H EMA hizalanması",
    "GOLDEN CROSS": "Uzun vadeli trend dönüşü",
    "L3 GÜÇLÜ KIRILIM": "Orta seviye yapısal kırılım",
    "MOST 1D YUKARI": "Günlük MOST ana trend",

    "RSI DÜŞÜK": "Momentum başlangıcı",
    "RSI AŞIRI SATIM": "Aşırı satımdan dönüş",
    "3LÜ TEPE": "Zayıf yapı",
    "L2 KIRILIM": "Zayıf kırılım",
    "MOST KIRILIMI": "MOST aşağı – risk",
    "1H DESTEK KIRILIMI": "Saatlik destek aşağı kırıldı – risk",
    "4H DESTEK KIRILIMI": "4 saatlik destek aşağı kırıldı – yüksek risk",
    "L4 MAJÖR KIRILIM": "Kurumsal majör seviye kırılımı",
}

def classify_volume_from_value(rvol):
    if rvol >= 2.5:
        return "🔥 GÜÇLÜ HACİM"
    elif rvol >= 1.3:
        return "⚡ ORTA HACİM"
    else:
        return "🐢 DÜŞÜK HACİM"

# ======================================================
# REPEAT BLOCK
# ======================================================

def in_repeat_block(symbol, algo):
    t = LAST_SENT.get((symbol, algo))
    return t and tr_now() < t


def mark_sent(symbol, algo):
    LAST_SENT[(symbol, algo)] = tr_now() + timedelta(minutes=REPEAT_BLOCK_MINUTES)

# ======================================================
# EMA TREND
# ======================================================

def ema_trend(e20, e50, e200):
    if e20 > e50 > e200:
        return "📈 YUKARI"
    if e20 < e50 < e200:
        return "📉 AŞAĞI"
    return "➖ YATAY"


def is_true_golden_cross(df):
    """Return True only on an actual EMA50 upward cross of EMA200."""
    if df is None or len(df) < 205:
        return False
    close = df["Close"].astype(float)
    e50 = close.ewm(span=50, adjust=False).mean()
    e200 = close.ewm(span=200, adjust=False).mean()
    return bool(e50.iloc[-2] <= e200.iloc[-2] and e50.iloc[-1] > e200.iloc[-1])

# ======================================================
# CANDLE HELPERS
# ======================================================

def is_green(df, idx):
    return df.iloc[idx]["Close"] > df.iloc[idx]["Open"]


def is_4h_first_green_after_red(df):
    if df is None or len(df) < 3:
        return False
    return (
        df.iloc[-3]["Close"] < df.iloc[-3]["Open"] and
        df.iloc[-2]["Close"] > df.iloc[-2]["Open"]
    )


def is_4h_trend_green(df):
    if df is None or len(df) < 2:
        return False
    return (
        df.iloc[-2]["Close"] > df.iloc[-2]["Open"] or
        df.iloc[-1]["Close"] > df.iloc[-1]["Open"]
    )

# ======================================================
# MOST (MOVING STOP)
# ======================================================

def calculate_most(df, period=9, multiplier=2.0):
    if df is None or len(df) < period + 2:
        return None

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    atr = tr.rolling(period).mean()

    most = [close.iloc[0]]
    trend = "UP"

    for i in range(1, len(close)):
        if trend == "UP":
            level = close.iloc[i] - atr.iloc[i] * multiplier
            level = max(level, most[-1])
            if close.iloc[i] < level:
                trend = "DOWN"
                level = close.iloc[i] + atr.iloc[i] * multiplier
        else:
            level = close.iloc[i] + atr.iloc[i] * multiplier
            level = min(level, most[-1])
            if close.iloc[i] > level:
                trend = "UP"
                level = close.iloc[i] - atr.iloc[i] * multiplier

        most.append(level)

    return {
        "trend": trend,
        "level": most[-1],
        "prev_level": most[-2]
    }


def detect_most_trend(df):
    res = calculate_most(df)
    if not res:
        return None
    return "UP" if res["trend"] == "UP" else "DOWN"

# ======================================================
# 4H SIKIŞMA + ONAYLI KIRILIM
# ======================================================

def detect_4h_squeeze_breakout(df):
    if df is None or len(df) < 10:
        return False

    zone = df.iloc[-10:-2]
    high_range = zone["High"].max()
    low_range = zone["Low"].min()

    if (high_range - low_range) / low_range > 0.06:
        return False

    breakout = df.iloc[-2]
    prev = df.iloc[-3]

    if breakout["Close"] <= prev["High"]:
        return False

    vol_ma = df["Volume"].rolling(10).mean().iloc[-2]
    if breakout["Volume"] <= vol_ma:
        return False

    return True

# ======================================================
# RE-ENTRY (TEKRAR GÜÇLÜ AL) FİLTRESİ
# ======================================================

def allow_reentry(signal_ctx):
    tf1h = signal_ctx.get("tf1h")
    tf4h = signal_ctx.get("tf4h")

    rsi_1h = tf1h.get("rsi") if tf1h else None
    rsi_4h = tf4h.get("rsi") if tf4h else None

    most_4h = signal_ctx.get("most_4h")
    most_4h_level = signal_ctx.get("most_4h_level")

    price = signal_ctx.get("price")
    power = signal_ctx.get("power")
    prev_power = signal_ctx.get("prev_power")
    after_target = signal_ctx.get("after_target")

    # 🔒 SADECE HEDEF SONRASI
    if not after_target:
        return False

    # RSI KİLİTLERİ
    if rsi_1h is not None and rsi_1h >= 70:
        return False
    if rsi_4h is not None and rsi_4h >= 72:
        return False
    if rsi_1h is not None:
        if rsi_1h < 45 or rsi_1h > 68:
            return False

    # MOST
    if most_4h != "UP":
        return False
    if most_4h_level:
        # MOST’un %0.6 altına kadar izin ver
        if price < most_4h_level * 0.994:
            return False

    # POWER
    if prev_power is None:
        return False
    if power - prev_power < 12:
        return False

    return True

# ======================================================
# PROFESYONEL L2 / L3 / L4 SEVİYE ANALİZİ
# ======================================================

def detect_levels(df, tf, tolerance=None):
    if df is None or len(df) < 50:
        return []

    if tolerance is None:
        tolerance = 0.006 if tf == "4h" else 0.004

    levels = []
    closes = df["Close"].values
    volumes = df["Volume"].values

    for i in range(20, len(closes) - 20):
        price = closes[i]
        touches = 0

        for j in range(i - 20, i + 20):
            if abs(closes[j] - price) / price <= tolerance:
                touches += 1

        if touches >= 2:
            levels.append({
                "level": price,
                "touches": touches,
                "volume": volumes[i]
            })

    return levels


def confirm_breakout(df, level):
    if df is None or len(df) < 20:
        return False

    last = df.iloc[-1]
    vol_ma = df["Volume"].rolling(20).mean().iloc[-1]

    return (
        last["Close"] > level and
        last["Volume"] >= vol_ma
    )


def classify_level(lvl, tf):
    t = lvl["touches"]

    # 4H ve 1D'de 3 temas varsa majör
    if t >= 3 and tf in ("4h", "1d"):
        return ("L4 MAJÖR KIRILIM", 18)

    # 4H ve 1D'de 2 temas varsa da majör kabul et
    if t == 2 and tf in ("4h", "1d"):
        return ("L4 MAJÖR KIRILIM", 18)

    # Diğer timeframe'lerde 3 temas güçlü
    if t >= 3:
        return ("L3 GÜÇLÜ KIRILIM", 12)

    # 2 temas normal kırılım
    if t >= 2:
        return ("L2 KIRILIM", 6)

    return None


def detect_l2_l3_l4_pro(df, price, tf):
    if df is None or len(df) < 50:
        return []

    helpers = []

    for lvl in detect_levels(df, tf):
        if price <= lvl["level"]:
            continue
        if not confirm_breakout(df, lvl["level"]):
            continue

        res = classify_level(lvl, tf)
        if res:
            helpers.append(res)

    return helpers


def detect_order_block(df):
    if df is None or len(df) < 20:
        return False

    reds = df.iloc[-8:-1]
    reds = reds[reds["Close"] < reds["Open"]]
    if reds.empty:
        return False

    return df.iloc[-1]["Close"] > reds.iloc[-1]["High"]
    
def get_real_rvol(df):

    try:
        if df is None or len(df) < 20:
            return 0

        last = df["Volume"].iloc[-1]
        avg = df["Volume"].rolling(20).mean().iloc[-1]

        if not avg or avg == 0:
            return 0

        return round(last / avg, 2)

    except:
        return 0
# ======================================================
# HELPER INDICATORS (ANA TOPLAMA)
# ======================================================

def helper_indicators(item):
    helpers = []

    tf15 = item["tf"]["15m"]
    tf1h = item["tf"].get("1h")
    tf4h = item["tf"].get("4h")
    tf1d = item["tf"].get("1d")

    rsi = tf15.get("rsi")
    if rsi is not None:
        if rsi < 28:
            helpers.append(("RSI AŞIRI SATIM", 12))
        elif rsi < 35:
            helpers.append(("RSI DÜŞÜK", 6))

    if tf15.get("volume_ok"):
        helpers.append(("MUMDA GÜÇLÜ HACİM", 10))

    df15 = tf15.get("df")
    if df15 is not None:
        if detect_three_peaks(df15["Close"]):
            # A multi-peak structure is a risk flag, not bullish confirmation.
            helpers.append(("3LÜ TEPE", -8))
        if detect_order_block(df15):
            helpers.append(("ORDER BLOCK", 15))

    helpers.extend(
        detect_l2_l3_l4_pro(
            tf15.get("df"),
            item["current_price"],
            tf="15m"
        )
    )

    if tf1h:
        helpers.extend(
            detect_l2_l3_l4_pro(
                tf1h.get("df"),
                item["current_price"],
                tf="1h"
            )
        )

    if tf4h:
        helpers.extend(
            detect_l2_l3_l4_pro(
                tf4h.get("df"),
                item["current_price"],
                tf="4h"
            )
        )

    if tf1h:
        break_1h = detect_support_resistance_break(tf1h["df"])
        if break_1h and break_1h.get("type") == "RESISTANCE_BREAK":
            helpers.append(("1H YAPISAL KIRILIM", 20))
        elif break_1h and break_1h.get("type") == "SUPPORT_BREAK":
            helpers.append(("1H DESTEK KIRILIMI", -25))

    if tf4h:
        break_4h = detect_support_resistance_break(tf4h["df"])
        if break_4h and break_4h.get("type") == "RESISTANCE_BREAK":
            helpers.append(("4H TREND KIRILIMI", 25))
        elif break_4h and break_4h.get("type") == "SUPPORT_BREAK":
            helpers.append(("4H DESTEK KIRILIMI", -35))
        if detect_4h_squeeze_breakout(tf4h["df"]):
            helpers.append(("4H SIKIŞMA KIRILIMI (ONAYLI)", 30))

    if tf1h and tf4h:
        if (
            tf15["ema20"] > tf15["ema50"] and
            tf1h["ema20"] > tf1h["ema50"] and
            tf4h["ema20"] > tf4h["ema50"]
        ):
            helpers.append(("ÇOKLU ZAMAN EMA ONAYI", 10))

    if tf1d and is_true_golden_cross(tf1d.get("df")):
        helpers.append(("GOLDEN CROSS", 10))

    if tf4h:
        m4 = detect_most_trend(tf4h["df"])
        if m4 == "UP":
            helpers.append(("MOST 4H YUKARI", 18))
        elif m4 == "DOWN":
            helpers.append(("MOST 4H AŞAĞI", -20))

    if tf1d:
        m1 = detect_most_trend(tf1d["df"])
        if m1 == "UP":
            helpers.append(("MOST 1D YUKARI", 22))
        elif m1 == "DOWN":
            helpers.append(("MOST 1D AŞAĞI", -30))

    return helpers
# ======================================================
# RVOL BAR SYSTEM
# ======================================================

def rvol_to_pct(r):
    if r is None:
        return 0
    return round((r - 1) * 100)


def rvol_to_blocks(r, max_blocks=10):
    if r is None:
        return "░" * max_blocks

    # 1.0 = baseline
    filled = int(min(max(r / 3, 0), 1) * max_blocks)
    return "█" * filled + "░" * (max_blocks - filled)


def format_rvol_line(label, r):
    if r is None:
        return f"• {label}: veri yok"

    pct = rvol_to_pct(r)

    if pct >= 0:
        pct_str = f"%{pct} üstü"
    else:
        pct_str = f"%{abs(pct)} altı"

    bar = rvol_to_blocks(r)

    return f"• {label}: {round(r,2)}x → {bar}  ({pct_str})"
# ======================================================
# MAIN ALGORITHMS
# ======================================================

def kombine_signal(item):
    tf15 = item["tf"]["15m"]
    tf1d = item["tf"].get("1d")
    tf4h = item["tf"].get("4h")
    tf1h = item["tf"].get("1h")

    if not tf1d or not tf4h or not tf1h:
        return None
    if not is_green(tf1d["df"], -2):
        return None
    if not is_4h_first_green_after_red(tf4h["df"]):
        return None
    if not is_green(tf1h["df"], -1):
        return None
    if tf1d["rsi"] >= 50:
        return None

    e20 = tf15.get("ema20_live")
    e50 = tf15.get("ema50_live")
    e200 = tf15.get("ema200_live")

    if not (e20 and e50 and e200 and e20 > e50 > e200):
        return None

    return {"main_type": "KOMBİNE", "base_strength": 55}


def super_kombine_signal(item):
    tf15 = item["tf"]["15m"]
    tf1d = item["tf"].get("1d")
    tf4h = item["tf"].get("4h")
    tf1h = item["tf"].get("1h")

    if not tf1d or not tf4h or not tf1h:
        return None

    if not (is_green(tf1d["df"], -2) and is_green(tf1d["df"], -1)):
        return None
    if not is_4h_trend_green(tf4h["df"]):
        return None
    if not is_green(tf1h["df"], -1):
        return None
    if tf1d["rsi"] >= 48:
        return None

    e20 = tf15.get("ema20_live")
    e50 = tf15.get("ema50_live")
    e200 = tf15.get("ema200_live")

    if not (e20 and e50 and e200 and e20 > e50 > e200):
        return None

    return {"main_type": "SÜPER KOMBİNE", "base_strength": 75}

def scalping_signal(item):

    tf15 = item["tf"]["15m"]
    tf1h = item["tf"].get("1h")
    tf4h = item["tf"].get("4h")
    tf1d = item["tf"].get("1d")

    if not tf1h or not tf4h or not tf1d:
        return None

    # ==================================================
    # 0️⃣ SAAT FİLTRESİ (OPENING MOMENTUM)
    # ==================================================

    now = tr_now()

    if now.hour < 10:
        return None

    if now.hour >= 17:
        return None

    # ==================================================
    # 1️⃣ ÜST ZAMAN TREND
    # ==================================================

    most_4h = detect_most_trend(tf4h["df"])
    most_1d = detect_most_trend(tf1d["df"])

    if most_4h != "UP":
        return None

    if most_1d == "DOWN":
        return None

    if not (tf1h["ema20"] > tf1h["ema50"]):
        return None

    # ==================================================
    # 2️⃣ EMA HİZALAMA
    # ==================================================

    tf15 = item["tf"]["15m"]

    if not (tf15["ema20"] > tf15["ema50"]):
        return None

    e20 = tf15.get("ema20_live")
    e50 = tf15.get("ema50_live")
    e200 = tf15.get("ema200_live")

    if not (e20 and e50 and e200 and e20 > e50 > e200):
        return None

    # ==================================================
    # 3️⃣ DATA
    # ==================================================

    df15 = tf15["df"]

    if df15 is None or len(df15) < 40:
        return None

    prev = df15.iloc[-2]
    last = df15.iloc[-1]

    price = item["current_price"]

    # ==================================================
    # 4️⃣ RVOL (RELATIVE VOLUME)
    # ==================================================

    avg_volume_30 = df15["Volume"].rolling(30).mean().iloc[-1]

    if avg_volume_30 == 0:
        return None

    real_rvol = get_real_rvol(df15)
    rvol_live = get_rvol(item["symbol"]) or 0

    if rvol_live < 0.5:
        rvol_live = last["Volume"] / avg_volume_30

    # 🔥 AYRI DOĞRULAMA (MAX YOK)
    if real_rvol < 1.3:
        return None

    if rvol_live < 1.2:
        return None

    # ==================================================
    # 5️⃣ VWAP HESABI
    # ==================================================

    typical_price = (df15["High"] + df15["Low"] + df15["Close"]) / 3
    cumulative_tp_vol = (typical_price * df15["Volume"]).cumsum()
    cumulative_vol = df15["Volume"].cumsum()

    vwap_series = cumulative_tp_vol / cumulative_vol

    current_vwap = vwap_series.iloc[-1]
    vwap_prev = vwap_series.iloc[-5]

    if price < current_vwap:
        return None

    if current_vwap <= vwap_prev:
        return None

    vwap_distance = (price - current_vwap) / current_vwap

    if vwap_distance > 0.015:
        return None

    # ==================================================
    # 6️⃣ LIQUIDITY SWEEP
    # ==================================================

    prev_range = df15.iloc[-22:-2]

    highest_high = prev_range["High"].max()

    sweep_detected = False

    if last["High"] > highest_high and last["Close"] < last["High"]:
        sweep_detected = True

    # ==================================================
    # 7️⃣ BREAKOUT
    # ==================================================

    if prev["Close"] <= highest_high:
        if not sweep_detected:
            return None

    breakout_move = (prev["Close"] - highest_high) / highest_high

    if breakout_move > 0.01:
        return None

    # ==================================================
    # 8️⃣ HACİM ANOMALİSİ
    # ==================================================

    vol_ma_prev = df15["Volume"].rolling(20).mean().iloc[-2]

    if prev["Volume"] < vol_ma_prev * 2:
        return None

    vol_ma_last = df15["Volume"].rolling(20).mean().iloc[-1]

    if last["Volume"] < vol_ma_last * 1.5:
        return None

    # ==================================================
    # 9️⃣ MOMENTUM CANDLE
    # ==================================================

    body = abs(prev["Close"] - prev["Open"])
    full = prev["High"] - prev["Low"]

    if full == 0:
        return None

    if body / full < 0.6:
        return None

    # ==================================================
    # 🔟 CONTINUATION
    # ==================================================

    if last["Close"] <= prev["Close"]:
        return None

    move_pct = (last["Close"] - prev["Close"]) / prev["Close"]

    if move_pct > 0.007:
        return None

    # ==================================================
    # 1️⃣1️⃣ RSI MOMENTUM
    # ==================================================

    rsi = tf15.get("rsi")

    if rsi is None:
        return None

    if rsi < 52 or rsi > 68:
        return None

    # ==================================================
    # 1️⃣2️⃣ DİRENÇ FİLTRESİ
    # ==================================================

    r1h = get_last_resistance(tf1h["df"])

    if r1h:

        dist_pct = ((r1h - price) / price) * 100

        if dist_pct < 1:
            return None

    # ==================================================
    # 1️⃣3️⃣ GÜÇ SKORU
    # ==================================================

    base_strength = 55
    volume_boost = 0
    rvol_boost = 0
    sweep_boost = 0

    if prev["Volume"] > vol_ma_prev * 2.5:
        volume_boost += 6

    if last["Volume"] > vol_ma_last * 2:
        volume_boost += 4

    if sweep_detected:
        sweep_boost += 4

    return {
        "main_type": "SCALPING",
        "base_strength": base_strength + volume_boost + rvol_boost + sweep_boost
    }



    
# ======================================================
# PROCESS SYMBOL SIGNALS
# ======================================================

def process_symbol_signals(item):

    reset_tp1_tracker_if_needed()
    
    symbol = item["symbol"]

    # 🔥 REALTIME GUARD
    price = item.get("current_price")

    if not price:
        return []

    t_key = today_key()

    # 🔥 gecikmiş veri filtre
    now_ts = time.time()
    item_ts = item.get("timestamp", now_ts)

    if now_ts - item_ts > 5:
        return []

    rvol_live = get_rvol(symbol)

    df15 = item.get("tf", {}).get("15m", {}).get("df")

    if df15 is None or len(df15) < 5:
        return []

    real_rvol = get_real_rvol(df15)

    # 🔕 PROD SAFE LOG
    if rvol_live < 1 or real_rvol > 2:
        print(symbol, "tick:", rvol_live, "real:", real_rvol)

    if not rvol_live or rvol_live < 0.5:
        try:
            last = df15.iloc[-1]
            avg = df15["Volume"].rolling(30).mean().iloc[-1]

            if avg and avg > 0:
                rvol_live = last["Volume"] / avg
            else:
                rvol_live = 0
        except:
            rvol_live = 0

    volume_label = classify_volume_from_value(real_rvol)

    # 🔥 ANLIK HACİM
    last_volume = None
    try:
        last = df15.iloc[-1]

        typical_last = (
            last["High"] +
            last["Low"] +
            last["Close"]
        ) / 3

        last_volume = typical_last * last["Volume"]

    except:
        last_volume = None

    # 🔥 GÜNLÜK HACİM (TL BAZLI - PROFESSIONAL)
    daily_volume = None

    try:
        today = tr_now().date()
        df_today = df15[df15.index.date == today]

        if not df_today.empty:
            typical_price = (
                df_today["High"] +
                df_today["Low"] +
                df_today["Close"]
            ) / 3

            daily_volume = (typical_price * df_today["Volume"]).sum()

    except:
        daily_volume = None

    brut_info = BRUT_MAP.get(symbol)

    base_entry = None
    
    reset_daily_success_if_needed()
    reset_weekly_success_if_needed()

    main = (
        super_kombine_signal(item)
        or kombine_signal(item)
        or scalping_signal(item)
    )
    if not main:
        return []

    algo = main["main_type"]

    

    helpers = helper_indicators(item)
    helper_map = {h[0]: h[1] for h in helpers}
    helper_names = set(helper_map.keys())

    base_strength = main.get("base_strength", 0)

    key = (symbol, algo)
    prev = LAST_SIGNAL_STATE.get(key, {})
    prev_power = prev.get("power", 0)

    prev_helpers = set(prev.get("helpers", []))
    added_helpers = list(helper_names - prev_helpers)
    removed_helpers = list(prev_helpers - helper_names)

    tf1h = item["tf"].get("1h")
    tf4h = item["tf"].get("4h")

    most_1h = detect_most_trend(tf1h["df"]) if tf1h else None
    most_4h = detect_most_trend(tf4h["df"]) if tf4h else None

    prev_most_4h = prev.get("most_4h")

    most_downgrade = prev_most_4h == "UP" and most_4h == "DOWN"
    most_upgrade = prev_most_4h == "DOWN" and most_4h == "UP"

    # -----------------------------------------------------
    # MOST DOWNGRADE ETKİSİ
    # -----------------------------------------------------
    if most_downgrade:
        helper_names.add("MOST KIRILIMI")
        helper_map["MOST KIRILIMI"] = -40

    # -----------------------------------------------------
    # FINAL POWER HESABI
    # -----------------------------------------------------
    helper_power = sum(
        v for v in helper_map.values()
        if isinstance(v, (int, float))
    )

    total_power = base_strength + helper_power

    # 🔥 HACİM DOĞRULAMA MODELİ (FINAL)

    if real_rvol >= 1.5 and rvol_live >= 1.5:
        total_power += 6

    elif real_rvol >= 1.5:
        total_power += 3

    elif rvol_live >= 2.2:
        total_power += 2   # spike ama düşük güven

    elif real_rvol < 0.8 and rvol_live < 1.2:
        total_power -= 10
    power_delta = total_power - prev_power

    levels = {"A": 0, "B": 0, "C": 0}
    for h in helper_names:
        lvl = HELPER_LEVELS.get(h)
        if lvl:
            levels[lvl] += 1

    if levels["A"] >= 1:
        action, category, title = "GÜÇLÜ AL", "strong", "🚀 GÜÇLÜ AL"
    elif levels["B"] >= 1:
        action, category, title = "AL", "combo", "📈 AL – B Seviye"
    elif levels["C"] >= 1:
        action, category, title = "İZLE", "watch", "👀 İZLE"
    else:
        return []
        
    now_h = tr_now().strftime("%H:%M")
    history = list(prev.get("history", []))

    if not history:
        history.append((now_h, f"{algo} sinyal"))

    # 🔕 İZLE → sadece state tut, sinyal gönderme
    if action == "İZLE":
        LAST_SIGNAL_STATE[key] = {
            "power": total_power,
            "helpers": list(helper_names),
            "most_4h": most_4h,
            "history": history,
        }
        return []

    # 🔻 MOST downgrade
    if most_downgrade:
        if action == "GÜÇLÜ AL":
            action, category = "AL", "combo"
            title = "⚠️ MOST 4H AŞAĞI – GÜÇ DÜŞÜRÜLDÜ"
        elif action == "AL":
            action, category = "İZLE", "watch"
            title = "⛔ MOST 4H AŞAĞI – İZLE"

    if most_upgrade:
        if action == "İZLE":
            action, category = "AL", "combo"
            title = "✅ MOST 4H YUKARI – TEKRAR AL"
        elif action == "AL":
            action, category = "GÜÇLÜ AL", "strong"
            title = "🚀 MOST 4H YUKARI – GÜÇLÜ AL"

    strengthened = False
    weakened = False

    threshold = (
        SCALPING_STRENGTH_THRESHOLD
        if algo == "SCALPING"
        else POWER_STRENGTH_THRESHOLD
    )

    if power_delta >= threshold:
        strengthened = True
        title = "🔥 GÜÇLENEN SİNYAL"

    elif power_delta <= -threshold:
        weakened = True
        title = "⚠️ ZAYIFLAYAN SİNYAL"
        category = "watch"

    

    if added_helpers:
        history.append((now_h, f"Eklendi: {', '.join(added_helpers)}"))
    if removed_helpers:
        history.append((now_h, f"Çıktı: {', '.join(removed_helpers)}"))
    if most_downgrade:
        history.append((now_h, "MOST 4H aşağı – downgrade"))
    if most_upgrade:
        history.append((now_h, "MOST 4H yukarı – upgrade"))
    if strengthened:
        history.append((now_h, f"Güç arttı (+{power_delta})"))
    if weakened:
        history.append((now_h, f"Güç düştü ({power_delta})"))
    # 🔎 Repeat sonrası basit yapı sadeleşmesi
    if removed_helpers and not strengthened and not weakened and levels["A"] >= 1 and not most_downgrade:
        history.append((now_h, "Mevcut sinyal zayıfladı (Trend korunuyor)"))

    
    r1h = get_last_resistance(tf1h["df"]) if tf1h else None
    r4h = get_last_resistance(tf4h["df"]) if tf4h else None

    r1h_dist_pct = round(((r1h - price) / price) * 100, 2) if r1h else None
    r4h_dist_pct = round(((r4h - price) / price) * 100, 2) if r4h else None

    most_1h_level = None
    most_4h_level = None

    if tf1h:
        m1 = calculate_most(tf1h["df"])
        if m1:
            most_1h_level = round(m1["level"], 2)

    if tf4h:
        m4 = calculate_most(tf4h["df"])
        if m4:
            most_4h_level = round(m4["level"], 2)

    t_key = today_key()
    w_key = week_key()

    entry_price = None
    k = make_key(symbol, algo)

    if k in DAILY_SUCCESS_TRACKER.get(t_key, {}):
        entry_price = DAILY_SUCCESS_TRACKER[t_key][k]["entry"]

    after_target = False
    k = make_key(symbol, algo)
    if k in WEEKLY_SUCCESS_TRACKER.get(w_key, {}):
        if WEEKLY_SUCCESS_TRACKER[w_key][k].get("hit"):
            after_target = True

    if algo == "SCALPING":
        is_reentry = False
    else:
        is_reentry = allow_reentry({
        "tf1h": tf1h,
        "tf4h": tf4h,
        "most_4h": most_4h,
        "most_4h_level": most_4h_level,
        "price": price,
        "power": total_power,
        "prev_power": prev_power,
        "after_target": after_target,
    })

    if is_reentry:
        base_entry = price
    else:
        base_entry = entry_price if entry_price is not None else price
    # 🔒 TP1 GLOBAL LOCK (algo bağımsız)
    if any(
        k[0] == symbol and v == today_key()
        for k, v in TP1_HIT_SYMBOLS.items()
    ):
        return []
        
    tp1 = None
    tp2 = None
    tp3 = None

    if base_entry:
        tp1 = fmt(base_entry * (1 + TP1_PCT))
        tp2 = fmt(base_entry * (1 + TP2_PCT))
        tp3 = fmt(base_entry * (1 + TP3_PCT))

    if is_reentry:
        reset_reentry_daily_if_needed()
        r_store = REENTRY_DAILY_TRACKER.setdefault(t_key, {})

        r_key = (symbol, algo, tr_now().strftime("%H:%M:%S"))
        if r_key not in r_store:
            r_store[r_key] = {
                "symbol": symbol,
                "algo": algo,
                "entry": base_entry,
                "tp1": tp1,
                "hit": False,
                "hit_price": None,
                "hit_time": None,
                "signal_type": "reentry",
            }
    LAST_SIGNAL_STATE[key] = {
        "power": total_power,
        "helpers": list(helper_names),
        "most_4h": most_4h,
        "history": history,
        "entry": base_entry,
    }

    live_gain_pct = None
    if base_entry:
        live_gain_pct = round(((price - base_entry) / base_entry) * 100, 2)

    signal = {
        "symbol": symbol,
        "brut": brut_info,
        "entry_price": fmt(base_entry),
        "reentry": is_reentry,
        "price": fmt(price),
        "live_gain_pct": live_gain_pct,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "title": "♻️ TEKRAR GÜÇLÜ AL (RE-ENTRY)" if is_reentry else title,
        "action": action,
        "category": "strong" if is_reentry else category,
        "main_algorithm": algo,
        "ema_trend": ema_trend(
            item["tf"]["15m"]["ema20_live"],
            item["tf"]["15m"]["ema50_live"],
            item["tf"]["15m"]["ema200_live"]
        ),
        "helpers": list(helper_names),
        "helpers_detail": [
            {
                "name": h,
                "level": HELPER_LEVELS[h],
                "desc": HELPER_DESCRIPTIONS.get(h, "")
            }
            for h in helper_names if h in HELPER_LEVELS
        ],
        "history": history,
        "time": tr_now().strftime("%H:%M:%S"),
        "resistance_1h": fmt(r1h),
        "resistance_4h": fmt(r4h),
        "resistance_1h_pct": r1h_dist_pct,
        "resistance_4h_pct": r4h_dist_pct,
        "most_1h": most_1h,
        "most_4h": most_4h,
        "most_1h_level": most_1h_level,
        "most_4h_level": most_4h_level,
        "volume_label": volume_label,
        "rvol_real": round(real_rvol, 2),
        "rvol_live": round(rvol_live, 2),
        "volume": int(last_volume) if last_volume else None,
        "daily_volume": int(daily_volume) if daily_volume else None,
        "power": total_power,
        "power_delta": power_delta,
        "signal_type": "reentry" if is_reentry else "primary",
        "published": True,
    }

    # =====================================
    # 🔁 REPEAT BLOCK
    # =====================================

    if key in LAST_PUBLISHED_STATE:
        if (
            in_repeat_block(symbol, algo)
            and not (strengthened or weakened or most_upgrade or most_downgrade)
        ):
            return []

    # =====================================
    # 🔒 GÜN İÇİ TEKRAR FİLTRESİ (SYMBOL BAZLI)
    # =====================================

    today = t_key  # tek kaynak

    # 🚫 TP1 vurmuşsa → tamamen sustur (FULL LOCK)
    if TP1_HIT_SYMBOLS.get(symbol) == today:
        return []

    # 🔁 RE-ENTRY → günde sadece 1 adet
    if is_reentry:
        if any(
            k[0] == symbol and k[2] == "re" and v == today
            for k, v in DAILY_SENT.items()
        ):
            return []

    # 🚫 PRIMARY → aynı hisseden tekrar sinyal gelmesin
    else:
        if any(
            k[0] == symbol and k[2] == "pr" and v == today
            for k, v in DAILY_SENT.items()
        ):
            return []

    # =====================================
    # 🔥 FİLTRELER (EN KRİTİK YER)
    # =====================================

    if action == "GÜÇLÜ AL":

        # 🔥 minimum kalite filtresi
        if real_rvol < 0.8 and rvol_live < 1.5:
            return []

        # 🔥 güçlü sinyal için en az bir taraf sağlam olmalı
        if real_rvol < 1.2 and rvol_live < 1.8:
            return []

        if "ORDER BLOCK" not in helper_names:
            return []

    # sadece strong gönder
    if signal["category"] != "strong":
        return []

    # =====================================
    # ✅ PUBLISH STATE
    # =====================================

    LAST_PUBLISHED_STATE[key] = tr_now()
    mark_sent(symbol, algo)

    DAILY_SENT[(symbol, algo, "re" if is_reentry else "pr")] = today_key()

    # =====================================
    # ✅ SADECE YAYINLANAN SİNYAL TRACKER'A
    # =====================================

    if not is_reentry and algo != "SCALPING":

        d_store = DAILY_SUCCESS_TRACKER.setdefault(t_key, {})
        w_store = WEEKLY_SUCCESS_TRACKER.setdefault(w_key, {})

        k = make_key(symbol, algo)

        if k not in d_store:
            d_store[k] = {
                "symbol": symbol,
                "algo": algo,
                "helpers": list(helper_names),
                "entry": base_entry,
                "target": price * (1 + TARGET_PCT),
                "hit": False,
                "entry_time": tr_now().strftime("%H:%M:%S"),
                "entry_date": tr_now().date(),
                "published": True
            }
            save_daily_state()

        if k not in w_store:
            w_store[k] = {
                "symbol": symbol,
                "algo": algo,
                "helpers": list(helper_names),
                "entry": base_entry,
                "target": price * (1 + TARGET_PCT),
                "hit": False,
                "hit_price": None,
                "hit_time": None,
                "hit_day": None,
                "entry_day": tr_now().strftime("%A"),
                "entry_date": tr_now().date(),
                "entry_time": tr_now().strftime("%H:%M:%S"),
                "published": True
            }
            save_weekly_state()

    return [signal]
# ======================================================
# FRIDAY CLOSE SNAPSHOT
# ======================================================

def capture_friday_close(symbol, price):
    now = tr_now()

    if now.weekday() != 4:
        return

    if now.hour == 18 and 5 <= now.minute <= 10:
        FRIDAY_CLOSE_PRICES.setdefault(symbol, price)
        save_weekly_state()


def capture_daily_close(symbol, price):
    if not is_market_close_final_window():
        return

    DAILY_CLOSE_PRICES.setdefault(symbol, price)
    save_daily_state()


# ======================================================
# SUCCESS TARGET UPDATE (DAILY + WEEKLY)
# ======================================================

def update_success_targets(symbol, price):
    capture_daily_close(symbol, price)

    reset_daily_success_if_needed()
    reset_weekly_success_if_needed()

    capture_friday_close(symbol, price)

    t_key = today_key()
    w_key = week_key()

    success_signals = []

    daily = DAILY_SUCCESS_TRACKER.get(t_key, {})
    weekly = WEEKLY_SUCCESS_TRACKER.get(w_key, {})

    # ---------- DAILY ----------
    for k, d in daily.items():
        if not d.get("published"):
            continue
        sym = d["symbol"]
        algo = d["algo"]
        if d.get("hit"):
            continue
        if sym != symbol:
            continue

        if price >= d["target"]:
            TP1_HIT_SYMBOLS[symbol] = today_key()
            d["hit"] = True
            d["hit_price"] = price
            d["hit_time"] = tr_now().strftime("%H:%M:%S")
            save_daily_state()

            entry = d["entry"]
            gain_pct = round(((price - entry) / entry) * 100, 2)

            success_signals.append({
                "symbol": sym,
                "title": "🎯 HEDEF GELDİ",
                "price": fmt(price),
                "action": "BAŞARILI",
                "category": "success",
                "main_algorithm": algo,
                "entry_price": fmt(entry),
                "target_price": fmt(d["target"]),
                "hit_price": fmt(price),
                "gain_pct": gain_pct,
                "time": d["hit_time"],
                "helpers": d.get("helpers", []),
                "history": [
                    ("ENTRY", f"{fmt(entry)}"),
                    ("TARGET", f"{fmt(d['target'])}"),
                    ("HIT", f"{fmt(price)}"),
                ],
            })

            # 🔁 DAILY kapanıştan gelen hit → WEEKLY’ye de yaz
            wk = make_key(sym, algo)
            w = weekly.get(wk)
            if w and not w.get("hit"):
                w["hit"] = True
                w["hit_price"] = price
                w["hit_time"] = d["hit_time"]
                w["hit_day"] = tr_now().strftime("%A")
                save_weekly_state()

    # ---------- WEEKLY (canlı takip) ----------
    for k, d in weekly.items():
        if not d.get("published"):
            continue
        sym = d["symbol"]
        algo = d["algo"]
        if d.get("hit"):
            continue
        if sym != symbol:
            continue

        if price >= d["target"]:
            d["hit"] = True
            d["hit_price"] = price
            d["hit_time"] = tr_now().strftime("%H:%M:%S")
            d["hit_day"] = tr_now().strftime("%A")
            save_weekly_state()
            
    # ---------- RE-ENTRY ----------
    t_key = today_key()
    r_day = REENTRY_DAILY_TRACKER.get(t_key, {})

    for r in r_day.values():
        if r.get("signal_type") != "reentry":
            continue
        if r.get("hit"):
            continue

        if r.get("symbol") != symbol:
            continue

        entry = r.get("entry")
        tp1 = r.get("tp1")

        if not isinstance(entry, (int, float)):
            continue
        if not isinstance(tp1, (int, float)):
            continue

        if price >= tp1:
            r["hit"] = True
            r["hit_price"] = price
            r["hit_time"] = tr_now().strftime("%H:%M:%S")
            r["gain_pct"] = round(((price - entry) / entry) * 100, 2)
        # ---------- SCALPING TP1 ----------
    from dashboard import SCALPING_SIGNALS

    for s in SCALPING_SIGNALS:
        if s.get("symbol") != symbol:
            continue

        if s.get("main_algorithm") != "SCALPING":
            continue

        if s.get("tp1") is None:
            continue
        if not s.get("entry_price"):
            continue

        if s.get("tp1_hit"):
            continue  # daha önce vurmuşsa tekrar gönderme

        if price >= s["tp1"]:
            s["tp1_hit"] = True

            success_signals.append({
                "symbol": symbol,
                "title": "⚡ SCALPING TP1 GELDİ",
                "price": price,
                "action": "TP1 BAŞARILI",
                "category": "success",
                "main_algorithm": "SCALPING",
                "entry_price": s.get("entry_price"),
                "target_price": s.get("tp1"),
                "hit_price": price,
                "gain_pct": round(((price - s.get("entry_price")) / s.get("entry_price")) * 100, 2) if s.get("entry_price") else None,
                "time": tr_now().strftime("%H:%M:%S"),
                "helpers": s.get("helpers", []),
                "history": [
                    ("ENTRY", str(s.get("entry_price"))),
                    ("TP1", str(s.get("tp1"))),
                    ("HIT", str(price)),
                ],
            })

    return success_signals 

# ======================================================
# DAILY SUCCESS REPORT (TELEGRAM)
# ======================================================

def build_daily_success_report():
    t_key = today_key()
    day_data = DAILY_SUCCESS_TRACKER.get(t_key, {})

    if not day_data:
        return None

    for d in day_data.values():
        if d.get("hit"):
            continue

        close_price = DAILY_CLOSE_PRICES.get(d["symbol"])
        if not close_price:
            continue

        if close_price >= d["target"]:
            d["hit"] = True
            d["hit_price"] = close_price
            d["hit_time"] = "18:10"
            save_daily_state()

            # WEEKLY’ye de işle
            w_key = week_key()
            wk = make_key(d["symbol"], d["algo"])
            w = WEEKLY_SUCCESS_TRACKER.get(w_key, {}).get(wk)
            if w and not w.get("hit"):
                w["hit"] = True
                w["hit_price"] = close_price
                w["hit_time"] = "18:10"
                w["hit_day"] = tr_now().strftime("%A")
                save_weekly_state()

    hits = [d for d in day_data.values() if d.get("hit")]
    fails = [d for d in day_data.values() if not d.get("hit")]

    lines = []
    lines.append("📊 GÜNLÜK BAŞARI RAPORU")
    lines.append(f"📅 {tr_now().strftime('%d.%m.%Y')}")
    lines.append("")
    lines.append(f"📡 Toplam Sinyal: {len(day_data)}")
    lines.append(f"✅ Başarılı: {len(hits)}")
    lines.append(f"❌ Başarısız: {len(fails)}")

    if hits:
        lines.append("")
        lines.append("🎯 BAŞARILI:")
        for d in hits:
            gain = round(((d["hit_price"] - d["entry"]) / d["entry"]) * 100, 2)
            lines.append(f"• {d['symbol']} | {d['algo']} | %{gain}")

    if fails:
        lines.append("")
        lines.append("⛔ HEDEF GELMEYENLER:")
        for d in fails:
            lines.append(f"• {d['symbol']} | {d['algo']}")

    lines.append("")
    lines.append(f"🕒 {tr_now().strftime('%H:%M')}")

    return "\n".join(lines)


# ======================================================
# WEEKLY SUCCESS REPORT (CUMA)
# ======================================================

def build_weekly_success_report():
    w_key = week_key()
    week_data = WEEKLY_SUCCESS_TRACKER.get(w_key, {})

    if not week_data:
        return None

    hits = [d for d in week_data.values() if d.get("hit")]
    fails = [d for d in week_data.values() if not d.get("hit")]

    lines = []
    lines.append("📅 HAFTALIK BAŞARI RAPORU")
    lines.append(f"📆 Hafta: {w_key}")
    lines.append("")
    lines.append(f"📡 Toplam: {len(week_data)}")
    lines.append(f"✅ Başarılı: {len(hits)}")
    lines.append(f"❌ Başarısız: {len(fails)}")

    if hits:
        lines.append("")
        lines.append("🎯 BAŞARILI SİNYALLER:")
        for d in hits:
            base_gain = round(((d["hit_price"] - d["entry"]) / d["entry"]) * 100, 2)

            friday_price = FRIDAY_CLOSE_PRICES.get(d["symbol"])
            friday_gain = None
            if friday_price:
                friday_gain = round(((friday_price - d["entry"]) / d["entry"]) * 100, 2)

            line = (
                f"• {d['symbol']} | {d['algo']} | "
                f"{d['entry_day']} → {d['hit_day']} | "
                f"Hedef: %{base_gain}"
            )

            if friday_gain is not None:
                line += f" | Cuma: %{friday_gain}"

            lines.append(line)

    if fails:
        lines.append("")
        lines.append("⛔ HEDEF GELMEYENLER:")
        for d in fails:
            lines.append(f"• {d['symbol']} | {d['algo']}")

    lines.append("")
    lines.append(f"🕒 {tr_now().strftime('%d.%m.%Y %H:%M')}")

    return "\n".join(lines)


# ======================================================
# TELEGRAM FORMAT
# ======================================================

def format_signal_message(signal):

    if signal.get("category") == "success":
        return "\n".join([
            "🎯 HEDEF GELDİ",
            "────────────",
            f"📊 {signal['symbol']}",
            "",
            f"🎯 Giriş: {signal['entry_price']}",
            f"📈 Hedef: {signal['target_price']}",
            f"✅ Gerçekleşen: {signal['hit_price']}",
            "",
            f"💰 Kazanç: %{signal['gain_pct']}",
            "────────────",
            f"⏰ {signal['time']}",
        ])

    lines = []
    lines.append(f"📊 {signal['symbol']}")
    lines.append("────────────")

    # BRÜT TAKAS
    if signal.get("brut"):
        days = signal["brut"].get("days_left")
        if days is not None and days >= 0:
            lines.append(f"‼️‼️ BRÜT TAKAS VAR ({days} gün kaldı)")
            lines.append("────────────")

    # TITLE
    if signal.get("signal_type") == "reentry":
        lines.append("♻️ TEKRAR GÜÇLÜ AL (RE-ENTRY)")
    else:
        lines.append(f"🏷 {signal['title']}")

    lines.append("")
    lines.append("────────────")

    # ENTRY
    if signal.get("entry_price"):
        lines.append(f"🎯 Giriş: {signal['entry_price']}")

    lines.append(f"💰 Canlı: {signal['price']}")
    lines.append(f"⚡ {signal['action']} | 🧠 {signal['main_algorithm']}")
    lines.append(f"📈 Trend: {signal['ema_trend']}")

    # =====================================
    # 🔥 HACİM BLOĞU (DÜZELTİLMİŞ)
    # =====================================
    if signal.get("volume_label"):

        vol = signal.get("volume")
        dvol = signal.get("daily_volume")

        def format_vol(v):
            if not v:
                return "-"
            if v >= 1_000_000_000:
                return f"{round(v/1_000_000_000,2)}B"
            elif v >= 1_000_000:
                return f"{round(v/1_000_000,2)}M"
            elif v >= 1_000:
                return f"{round(v/1_000,1)}K"
            return str(v)

        lines.append(
            f"📊 {signal['volume_label']} | "
            f"Anlık: {format_vol(vol)} | "
            f"Günlük: {format_vol(dvol)}"
        )

        lines.append(
            format_rvol_line("REAL", signal.get("rvol_real"))
        )

        lines.append(
            format_rvol_line("LIVE", signal.get("rvol_live"))
        )

    # =====================================
    # TP
    # =====================================
    if signal.get("tp1"):
        lines.append("")
        lines.append("────────────")
        lines.append("🎯 HEDEF")
        lines.append(f"• TP1: {signal['tp1']}")
        
    # =====================================
    # LIVE GAIN
    # =====================================
    if signal.get("live_gain_pct") is not None:
        lines.append("────────────")
        lines.append(f"💹 Anlık Kazanç: %{signal['live_gain_pct']}")

    # =====================================
    # HELPERS
    # =====================================
    helpers = signal.get("helpers_detail") or []
    if helpers:
        lines.append("")
        lines.append("────────────")
        lines.append("🧩 YARDIMCILAR")
        for h in helpers:
            lines.append(f"• [{h['level']}] {h['name']}")

    # =====================================
    # HISTORY
    # =====================================
    history = signal.get("history") or []
    if history:
        lines.append("")
        lines.append("────────────")
        lines.append("📜 HAREKETLER")
        for t, msg in history[-4:]:
            lines.append(f"{t} → {msg}")

    lines.append("")
    lines.append("────────────")
    lines.append(f"⏰ {signal['time']}")

    return "\n".join(lines)

# ======================================================
# BULK PROCESS
# ======================================================

def process_signals(data):
    out = []

    refresh_brut_list()

    zero_streak = 0

    for item in data:
        time.sleep(0.10)

        try:
            symbol = item.get("symbol")
            price = item.get("current_price")

            # 🔥 TEK KAYNAK RVOL (item'dan gelirse kullan, yoksa fallback)
            rvol = item.get("rvol")
            if rvol is None:
                rvol = 0

            # 🔥 SOFT DATA CHECK
            if not price or rvol < 0.05:
                zero_streak += 1
            else:
                zero_streak = 0

            # 🔥 DATA YAVAŞSA BEKLE AMA DURMA
            if zero_streak >= 12:
                print("⚠️ DATA YAVAŞ → kısa bekleme (5sn)")
                time.sleep(5)
                zero_streak = 0
                continue

            # 🔥 SİNYAL ÜRET
            signals = process_symbol_signals(item)
            if signals:
                out.extend(signals)

            # 🔥 TARGET UPDATE
            update_success_targets(symbol, price)

        except Exception as e:
            print(f"❌ PROCESS ERROR ({item.get('symbol')}):", e)
            continue

    return out
