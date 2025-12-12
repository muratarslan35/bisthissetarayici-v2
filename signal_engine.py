from datetime import datetime
from utils import to_tr_timezone

def ma_arrow(direction):
    if direction in ("price_above","above"): return "🔼 yukarı kırdı"
    if direction in ("price_below","below"): return "🔻 aşağı kırdı"
    return "➡️ yatay"

def format_support_resistance(sr):
    if sr is None:
        return "Veri yok"
    return (
        f"  • 15m → Destek: {sr['15m']['support']} | Direnç: {sr['15m']['resistance']}\n"
        f"  • 1h → Destek: {sr['1h']['support']} | Direnç: {sr['1h']['resistance']}\n"
        f"  • 4h → Destek: {sr['4h']['support']} | Direnç: {sr['4h']['resistance']}\n"
        f"  • 1D → Destek: {sr['1D']['support']} | Direnç: {sr['1D']['resistance']}"
    )

def signal_emoji(sig):
    if sig == "buy": return "🟢⬆️"
    if sig == "sell": return "🔴⬇️"
    return "⚪"

def process_signals(item):
    signals = []
    symbol = item.get("symbol")
    price = item.get("current_price")
    rsi = item.get("RSI")
    volume = item.get("volume")
    change_percent = float(item.get("daily_change",0))
    sr_levels = {
        "15m": {"support": item.get("support_break"), "resistance": item.get("resistance_break")},
        "1h": {"support": item.get("support_break"), "resistance": item.get("resistance_break")},
        "4h": {"support": item.get("support_break"), "resistance": item.get("resistance_break")},
        "1D": {"support": item.get("support_break"), "resistance": item.get("resistance_break")},
    }

    ma20 = item["ma_breaks"].get("MA20")
    ma50 = item["ma_breaks"].get("MA50")
    ma100 = item["ma_breaks"].get("MA100")
    ma200 = item["ma_breaks"].get("MA200")

    # Sinyal tetikleyiciler
    item["buy_signal"] = rsi < 30
    item["sell_signal"] = rsi > 70
    item["combined_signal"] = False  # opsiyonel
    ts = to_tr_timezone(datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")

    if item.get("buy_signal"):
        signals.append((f"BUY-{symbol}",
            f"Hisse Takip: {symbol}\n{signal_emoji('buy')} AL sinyali!\nFiyat: {price} TL | RSI: {rsi}\nHacim: {volume}\nGünlük Değişim: %{change_percent}\nSinyal zamanı (TR): {ts}"))

    if item.get("sell_signal"):
        signals.append((f"SELL-{symbol}",
            f"Hisse Takip: {symbol}\n{signal_emoji('sell')} SAT sinyali!\nFiyat: {price} TL | RSI: {rsi}\nHacim: {volume}\nGünlük Değişim: %{change_percent}\nSinyal zamanı (TR): {ts}"))

    if item.get("three_peak"):
        signals.append((f"TT-{symbol}", f"Hisse Takip: {symbol}\n🔥🔥 3'lü tepe kırılımı!\nSinyal zamanı (TR): {ts}"))

    if item.get("green_1100"):
        signals.append((f"11MUM-{symbol}", f"Hisse Takip: {symbol}\n✅ 11:00'da yeşil mum başladı\nSinyal zamanı (TR): {ts}"))

    if item.get("green_1500"):
        signals.append((f"15MUM-{symbol}", f"Hisse Takip: {symbol}\n✅ 15:00'da yeşil mum başladı\nSinyal zamanı (TR): {ts}"))

    # MA ve destek/direnç mesajları
    ma_msg = (
        f"🔍 MA Durumları:\n"
        f"• MA20 → {ma_arrow(ma20)}\n"
        f"• MA50 → {ma_arrow(ma50)}\n"
        f"• MA100 → {ma_arrow(ma100)}\n"
        f"• MA200 → {ma_arrow(ma200)}"
    )
    sr_msg = "📉 Destek – Direnç Düzeyleri:\n" + format_support_resistance(sr_levels)

    # Kombine sinyal (opsiyonel)
    if item.get("combined_signal"):
        signals.append((f"COMBO-{symbol}",
            f"Hisse Takip: {symbol}\n🚀 Kombine Sinyal!\nFiyat: {price} TL | RSI: {rsi}\nHacim: {volume}\nGünlük Değişim: %{change_percent}\n\n{ma_msg}\n\n{sr_msg}\nSinyal zamanı (TR): {ts}"))

    return signals
