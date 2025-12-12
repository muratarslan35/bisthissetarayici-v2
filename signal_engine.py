# signal_engine.py
from datetime import datetime
from utils import to_tr_timezone

def ma_arrow_text(direction):
    if direction in ("above","price_above"):
        return "🔼 yukarı kırdı"
    if direction in ("below","price_below"):
        return "🔻 aşağı kırdı"
    return "➡️ yatay"

def format_ma_block(ma_values_15m, ma_dirs_15m):
    parts = []
    for w in (20,50,100,200):
        mv = ma_values_15m.get(w) if ma_values_15m else None
        md = ma_dirs_15m.get(w) if ma_dirs_15m else None
        arrow = ma_arrow_text(md) if md else "—"
        parts.append(f"MA{w}: {arrow}")
    return " | ".join(parts)

def build_support_text(sr):
    try:
        if not sr:
            return "Destek/Direnç veri yok"
        return (
            f"En yakın destek/direnç (15m/1h/4h/1D):\n"
            f"  • 15m → {sr['15m']['support']} / {sr['15m']['resistance']}\n"
            f"  • 1h  → {sr['1h']['support']} / {sr['1h']['resistance']}\n"
            f"  • 4h  → {sr['4h']['support']} / {sr['4h']['resistance']}\n"
            f"  • 1D  → {sr['1D']['support']} / {sr['1D']['resistance']}"
        )
    except Exception:
        return "Destek/Direnç hesaplanamadı"

def process_signals(item):
    """
    Aggregate all signals for one symbol into a single message (so user gets one consolidated message).
    Returns list with ONE tuple (sig_key, message) if any signal exists, else [].
    """
    try:
        sym = item.get("symbol")
        price = item.get("current_price")
        rsi = item.get("RSI") or item.get("rsi_15")
        last = item.get("last_signal")
        support_break = item.get("support_break")
        resistance_break = item.get("resistance_break")
        three_peak = item.get("three_peak_break")
        ma_breaks = item.get("ma_breaks", {})      # legacy 15m map
        ma_values = item.get("ma_values", {})
        tf = item.get("tf", {})
        sr = item.get("support_resistance", {})

        now_tr = to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")

        lines = []
        header = f"Hisse Takip: {sym}"
        lines.append(header)

        # AL/SAT
        if last == "AL":
            lines.append("⬆️ 🟢 AL sinyali!")
        elif last == "SAT":
            lines.append("⬇️ 🔴 SAT sinyali!")

        # RSI
        if rsi is not None:
            if rsi < 20:
                lines.append(f"🔻 RSI {rsi:.2f} < 20")
            elif rsi > 80:
                lines.append(f"🔺 RSI {rsi:.2f} > 80")
            else:
                lines.append(f"RSI: {rsi:.2f}")

        # Support/Resistance breaks
        if support_break:
            lines.append("🔻 Direnç değil — Destek kırıldı!")  # keep message clear
        if resistance_break:
            lines.append("🔺 Direnç kırıldı!")

        # three peak
        if three_peak:
            lines.append("🔥🔥 3'lü tepe kırılımı!")

        # MA block (15m summary)
        ma_vals_15 = ma_values.get("15m", {}) if isinstance(ma_values, dict) else {}
        ma_dirs_15 = ma_breaks if ma_breaks else (tf.get("15m", {}).get("ma_dirs") or {})
        ma_block = format_ma_block(ma_vals_15, ma_dirs_15)
        lines.append("MA Durumları: " + ma_block)

        # add volume & daily change if present
        if item.get("volume") is not None:
            lines.append(f"Hacim: {item.get('volume')}")
        if item.get("daily_change") is not None:
            lines.append(f"Günlük değişim: {item.get('daily_change')}")

        # support/resistance numeric
        lines.append(build_support_text(sr))

        # MA cross extras (golden/death)
        ma20x50 = tf.get("15m", {}).get("ma_values", {}) or ma_values.get("15m", {})
        # if golden/death present in outbound ma_breaks dictionary
        try:
            if ma_breaks.get("20x50") == "golden_cross" or (tf.get("15m",{}).get("ma_values") and tf.get("15m").get("ma_values").get("20x50") == "golden_cross"):
                lines.append("⚔️ 20x50 Golden Cross!")
            elif ma_breaks.get("20x50") == "death_cross":
                lines.append("⚔️ 20x50 Death Cross!")
        except Exception:
            pass

        # legacy green 11/15 (kept if needed for combo)
        if item.get("green_mum_11"):
            lines.append("✅ 11:00'de yeşil mum (proxy)")
        if item.get("green_mum_15"):
            lines.append("✅ 15:00'te yeşil mum (proxy)")

        # combined A-type (legacy)
        if item.get("composite_signal"):
            lines.append("🚀 Kombine Sinyal (A-type)")

        # SUPER COMBINED (2. seçenek) => strong signal
        if item.get("super_combined_ok"):
            # mark with rockets and bonus if any
            bonus_txt = " (BONUS: direnç kırılımı mevcut)" if item.get("super_bonus") else ""
            lines.append(f"🚀🚀🚀 Süper Kombine Sinyal!{bonus_txt}")

        # Final timestamp
        lines.append(f"Sinyal zamanı (TR): {now_tr}")

        # Build single message
        message = "\n".join(lines)

        # create unique key so dedupe per symbol works (single bundle key)
        sig_key = f"BUNDLE-{sym}"

        return [(sig_key, message)]

    except Exception as e:
        # in case of error, ensure app can log and continue
        print("[signal_engine] error for", item.get("symbol"), e)
        return []
