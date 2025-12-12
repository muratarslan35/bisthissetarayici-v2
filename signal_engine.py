# signal_engine.py
import numpy as np
from datetime import datetime
from utils import to_tr_timezone

# -------------------- DESTEK – DİRENÇ HESABI --------------------
def calc_support_resistance(df, window=20):
    df = df[-window:]
    highs = df["high"].values
    lows = df["low"].values

    resistance = np.max(highs)
    support = np.min(lows)

    return float(support), float(resistance)


# -------------------- MA TREND YÖNÜ --------------------
def ma_trend_symbol(ma_prev, ma_now, price_prev, price_now):
    if price_now > ma_now and price_prev <= ma_prev:
        return "up"     # yukarı kırdı
    elif price_now < ma_now and price_prev >= ma_prev:
        return "down"   # aşağı kırdı
    else:
        return None


def trend_icon(trend):
    if trend == "up":
        return "🔼 yukarı kırdı"
    elif trend == "down":
        return "🔻 aşağı kırdı"
    return None


# -------------------- PROCESS SIGNALS --------------------
def process_signals(item):
    signals = []
    sym = item.get("symbol")
    if not sym:
        return []

    price = item.get("price")
    rsi = item.get("rsi")
    df = item.get("df")                      # Ana timeframe
    df_15m = item.get("df_15m")
    df_1h = item.get("df_1h")
    df_4h = item.get("df_4h")
    df_1d = item.get("df_1d")

    # Zaman damgası
    now_tr = to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")

    # -------------------- DESTEK – DİRENÇ 4 ZAMAN ÇERÇEVESİ --------------------
    support_list = []
    resist_list = []

    def add_sr(name, dfx):
        if dfx is not None and len(dfx) > 20:
            s, r = calc_support_resistance(dfx, 20)
            support_list.append(f"{name}: {s:.2f}")
            resist_list.append(f"{name}: {r:.2f}")

    add_sr("15m", df_15m)
    add_sr("1H", df_1h)
    add_sr("4H", df_4h)
    add_sr("1D", df_1d)

    support_block = "\n".join(support_list) if support_list else "Veri yok"
    resist_block = "\n".join(resist_list) if resist_list else "Veri yok"

    # -------------------- MUM - 11:00 & 15:00 SİNYAL --------------------
    mum_11 = item.get("green_11", False)
    mum_15 = item.get("green_15", False)

    mum_lines = []
    if mum_11:
        mum_lines.append("✅ 11:00'de yeşil mum")
    if mum_15:
        mum_lines.append("✅ 15:00'de yeşil mum")

    # -------------------- AL / SAT SİNYALİ --------------------
    if item.get("buy_signal"):
        buy_msg = f"🟢 AL Sinyali (RSI: {rsi:.2f})"
    else:
        buy_msg = None

    if item.get("sell_signal"):
        sell_msg = f"🔴 SAT Sinyali (RSI: {rsi:.2f})"
    else:
        sell_msg = None

    # -------------------- 3’LÜ TEPE --------------------
    triple = item.get("triple_top", False)
    triple_msg = "🔥🔥 3’lü tepe kırılımı!" if triple else None

    # -------------------- MA’LER --------------------
    ma_lines = []
    for name, key in [("MA20", "ma20"), ("MA50", "ma50"), ("MA100", "ma100"), ("MA200", "ma200")]:
        ma_val = item.get(key)
        ma_prev = item.get(key + "_prev")

        price_prev = item.get("price_prev")

        if ma_val and ma_prev and price and price_prev:
            trend = ma_trend_symbol(ma_prev, ma_val, price_prev, price)
            icon = trend_icon(trend)
            if icon:
                ma_lines.append(f"{icon} {name}")
    ma_block = "\n".join(ma_lines) if ma_lines else "MA sinyali yok"

    # -------------------- GOLDEN CROSS --------------------
    golden = item.get("golden_cross", False)
    golden_msg = "⚔️ Golden Cross!" if golden else None

    # -------------------- KOMBİNE SİNYAL --------------------
    combine = item.get("combined_signal", False)
    combine_msg = "🚀🚀🚀 Kombine Sinyal" if combine else None

    # -------------------- MESAJ BLOĞU --------------------
    lines = [f"Hisse Takip: {sym}"]

    if buy_msg: lines.append(buy_msg)
    if sell_msg: lines.append(sell_msg)
    if triple_msg: lines.append(triple_msg)
    if mum_lines: lines.extend(mum_lines)
    if golden_msg: lines.append(golden_msg)
    if combine_msg: lines.append(combine_msg)

    lines.append("\nMA Durumları:")
    lines.append(ma_block)

    # DESTEK – DİRENÇ BLOKLARI
    lines.append("\nEn Yakın Destekler:")
    lines.append(support_block)

    lines.append("\nEn Yakın Dirençler:")
    lines.append(resist_block)

    lines.append(f"\nSinyal Zamanı (TR): {now_tr}\n")

    full_message = "\n".join(lines)

    # single output with key
    signals.append(("full_sinyal", full_message))

    return signals
