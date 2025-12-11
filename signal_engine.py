# signal_engine.py
from datetime import datetime
from utils import to_tr_timezone, nearest_support_resistance_from_history

def format_ma_icon(v):
    # v is "price_above" or "price_below" or "golden_cross"/"death_cross"
    if v == "price_above":
        return "🔼"
    if v == "price_below":
        return "🔻"
    if v == "golden_cross":
        return "⚔️"
    if v == "death_cross":
        return "⚔️"
    return ""

def process_signals(item):
    """
    item: dict from fetch_bist.py for one symbol
    returns: list of tuples [(sig_key, message)] - usually single tuple per symbol (combined)
    """
    out = []
    try:
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

        now_tr = to_tr_timezone(datetime.utcnow())
        now_str = now_tr.strftime("%Y-%m-%d %H:%M:%S") if now_tr else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        lines = []
        # Header
        lines.append(f"Hisse Takip: {sym}")
        # AL / SAT
        if last == "AL":
            lines.append(f"🟢 ↑ AL sinyali! Fiyat: {price:.4f} TL | RSI: {rsi:.2f}")
        elif last == "SAT":
            lines.append(f"🔴 ↓ SAT sinyali! Fiyat: {price:.4f} TL | RSI: {rsi:.2f}")

        # RSI thresholds (strong alerts)
        if rsi is not None:
            if rsi < 20:
                lines.append(f"🔻 RSI {rsi:.2f} < 20")
            elif rsi > 80:
                lines.append(f"🔺 RSI {rsi:.2f} > 80")

        # Support / Resistance breaks
        if support_break:
            lines.append("🟢 Destek kırılımı!")
        if resistance_break:
            lines.append("🔴 Direnç kırılımı!")

        # Three peak
        if three_peak:
            lines.append("🔥🔥 3lü tepe kırılımı!")

        # 11:00 / 15:00 green candles
        if green_11:
            lines.append("✅ 11:00'de yeşil mum")
        if green_15:
            lines.append("✅ 15:00'te yeşil mum")

        # MA infos: show icons and nearest values
        ma_lines = []
        for key, v in ma_breaks.items():
            # key like "MA20" or "20x50"
            ic = format_ma_icon(v)
            ma_val = ma_values.get(int(key.replace("MA","")), None) if key.startswith("MA") else None
            if v:
                if key.startswith("MA"):
                    ma_lines.append(f"{ic} {key}: {v.replace('_',' ')}")
                else:
                    # cross
                    ma_lines.append(f"{ic} {key}: {v}")
        if ma_lines:
            lines.append("MA Durumları: " + " | ".join(ma_lines))

        # Kombine A-type conservative rule (isim => "Kombine Sinyal")
        combined_ok = False
        try:
            if (green_11 or green_15) and (last == "AL" or (rsi is not None and rsi < 30)):
                combined_ok = True
        except Exception:
            combined_ok = False

        if combined_ok:
            lines.append("🚀🚀🚀 Kombine Sinyal (Günlük & Kısa periyot uyumu)")

        # Destek/Direnç - hesapla kısa geçmişten (1h / 4h / 1d / 1w için fetch_bist tarafından gelen df yoksa fallback)
        # If fetch_bist provided a 'history' DataFrame in item, use it; else skip.
        supp_res_lines = []
        hist_map = item.get("history", {})  # may contain {"1h": df, "4h": df, "1d": df, "1w": df}
        # if item doesn't include history, process_signals will skip nearest SR
        for tf in ("1h","4h","1d","1w"):
            df_tf = hist_map.get(tf)
            if df_tf is not None:
                try:
                    s, r = nearest_support_resistance_from_history(df_tf)
                    if s is not None or r is not None:
                        s_str = f"{s:.4f}" if s is not None else "-"
                        r_str = f"{r:.4f}" if r is not None else "-"
                        supp_res_lines.append(f"{tf}: Destek {s_str} / Direnç {r_str}")
                except Exception:
                    pass
        if supp_res_lines:
            lines.append("Yakın S/R: " + " | ".join(supp_res_lines))

        # Footer timestamp
        lines.append(f"Sinyal zamanı (TR): {now_str}")

        # Build single message
        full_msg = "\n".join(lines)
        # Choose sig_key for dedupe: symbol + "COMBINED_A" or generic
        sig_key = "COMBINED_ALL"

        out.append((sig_key, full_msg))
    except Exception as e:
        # In app we'll log errors per-symbol
        raise e

    return out
