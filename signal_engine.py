import math
from datetime import datetime
from utils import to_tr_timezone

# ----------------------------------------------------
#  YARDIMCI FONKSİYONLAR (SİNYAL EMOJİ DÖNÜŞÜMLERİ)
# ----------------------------------------------------

def ma_arrow(direction):
    """MA yönünü emojiye dönüştürür."""
    if direction == "above":      # price above MA
        return "🔼 yukarı kırdı"
    if direction == "below":      # price below MA
        return "🔻 aşağı kırdı"
    return "➡️ yatay"

def format_support_resistance(sr):
    """Destek/direnç sözlüğünü yazıya döker."""
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

# ----------------------------------------------------
#                ANA SİNYAL MOTORU
# ----------------------------------------------------
def process_signals(item):
    signals = []
    symbol = item.get("symbol")
    price = item.get("price")
    rsi = item.get("rsi")
    volume = item.get("volume")
    change_percent = item.get("change_percent")
    sr_levels = item.get("support_resistance")  # 15m/1h/4h/1D bu objede olmalı

    tr_time = to_tr_timezone(datetime.utcnow())
    ts = tr_time.strftime("%Y-%m-%d %H:%M:%S")

    # ----------------------------------------------------
    # ÖRNEK: AL / SAT / FORMASYON / MUM
    # ----------------------------------------------------

    # BUY EXAMPLE
    if item.get("buy_signal"):
        sig_key = f"BUY-{symbol}"
        message = (
            f"Hisse Takip: {symbol}\n"
            f"{signal_emoji('buy')} AL sinyali!\n"
            f"Fiyat: {price} TL | RSI: {rsi}\n"
            f"Hacim: {volume}\n"
            f"Günlük Değişim: %{change_percent}\n"
        )
        signals.append((sig_key, message))

    # SELL EXAMPLE
    if item.get("sell_signal"):
        sig_key = f"SELL-{symbol}"
        message = (
            f"Hisse Takip: {symbol}\n"
            f"{signal_emoji('sell')} SAT sinyali!\n"
            f"Fiyat: {price} TL | RSI: {rsi}\n"
            f"Hacim: {volume}\n"
            f"Günlük Değişim: %{change_percent}\n"
        )
        signals.append((sig_key, message))

    # 3’lü tepe
    if item.get("triple_top"):
        signals.append((
            f"TT-{symbol}",
            f"Hisse Takip: {symbol}\n🔥🔥 3'lü tepe kırılımı!"
        ))

    # 11:00 Y E Ş İ L M U M
    if item.get("green_1100"):
        signals.append((
            f"11MUM-{symbol}",
            f"Hisse Takip: {symbol}\n✅ 11:00'da yeşil mum başladı"
        ))

    # 15:00 YESIL MUM
    if item.get("green_1500"):
        signals.append((
            f"15MUM-{symbol}",
            f"Hisse Takip: {symbol}\n✅ 15:00'da yeşil mum başladı"
        ))

    # ----------------------------------------------------
    # MA YÖNLERİNİ MESAJ FORMATINA ÇEVİR
    # ----------------------------------------------------
    ma_msg = (
        f"🔍 MA Durumları:\n"
        f"• MA20 → {ma_arrow(item.get('ma20'))}\n"
        f"• MA50 → {ma_arrow(item.get('ma50'))}\n"
        f"• MA100 → {ma_arrow(item.get('ma100'))}\n"
        f"• MA200 → {ma_arrow(item.get('ma200'))}"
    )

    # ----------------------------------------------------
    # DESTEK – DİRENÇ BİLGİLERİ
    # ----------------------------------------------------
    sr_msg = "📉 Destek – Direnç Düzeyleri:\n" + format_support_resistance(sr_levels)

    # ----------------------------------------------------
    # KOMBİNE SİNYAL
    # ----------------------------------------------------
    if item.get("combined_signal"):
        final_msg = (
            f"Hisse Takip: {symbol}\n"
            f"🚀🚀🚀 Kombine Sinyal!\n"
            f"Fiyat: {price} TL | RSI: {rsi}\n"
            f"Hacim: {volume}\n"
            f"Günlük Değişim: %{change_percent}\n\n"
            f"{ma_msg}\n\n"
            f"{sr_msg}\n\n"
            f"Sinyal zamanı (TR): {ts}"
        )
        signals.append((f"COMBO-{symbol}", final_msg))

    # ----------------------------------------------------
    # TÜM MESAJLAR SON HALİ
    # ----------------------------------------------------
    final_signals = []
    for key, msg in signals:
        clean = msg + f"\n\nSinyal zamanı (TR): {ts}"
        final_signals.append((key, clean))

    return final_signals
