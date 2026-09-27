from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

TR_TZ = ZoneInfo("Europe/Istanbul")

W, H = 1280, 1120
BG = (10, 20, 34)
PANEL = (20, 38, 60)
PANEL2 = (25, 48, 75)
GRID = (49, 70, 96)
TEXT = (246, 249, 252)
MUTED = (171, 188, 208)
GREEN = (48, 210, 120)
RED = (245, 86, 86)
AMBER = (245, 181, 64)
BLUE = (94, 158, 255)
CYAN = (50, 212, 236)
WHITE = (255, 255, 255)


def _font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _num(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


def _frame(item, tf):
    try:
        df = item.get("tf", {}).get(tf, {}).get("df")
        if isinstance(df, pd.DataFrame) and not df.empty:
            out = df.copy()
            out.columns = [str(c).title() for c in out.columns]
            req = {"Open", "High", "Low", "Close"}
            if req.issubset(set(out.columns)):
                return out
    except Exception:
        pass
    return None


def _rounded(draw, xy, radius=20, fill=PANEL, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _text(draw, xy, text, size=24, fill=TEXT, bold=False, anchor=None):
    draw.text(xy, str(text), font=_font(size, bold), fill=fill, anchor=anchor)


def _scale_price(price, low, high, top, height):
    if high <= low:
        return top + height / 2
    return top + height - ((price - low) / (high - low)) * height


def _ema(series, span):
    return pd.to_numeric(series, errors="coerce").astype(float).ewm(span=span, adjust=False).mean()


def _draw_candle_chart(draw, df, box, entry=None, stop=None, target=None, title=""):
    x0, y0, x1, y1 = box
    pad_l, pad_r, pad_t, pad_b = 54, 68, 44, 34
    x = x0 + pad_l
    y = y0 + pad_t
    w = x1 - x0 - pad_l - pad_r
    h = y1 - y0 - pad_t - pad_b

    _rounded(draw, box, 18, fill=(14, 28, 46), outline=GRID)
    _text(draw, (x0 + 18, y0 + 12), title, 20, MUTED, True)

    if df is None or len(df) < 8:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "Mum verisi yok", 26, MUTED, True, "mm")
        return

    df = df.tail(42).copy()
    hi = float(pd.to_numeric(df["High"], errors="coerce").max())
    lo = float(pd.to_numeric(df["Low"], errors="coerce").min())

    levels = [v for v in (entry, stop, target) if _num(v) is not None]
    if levels:
        hi = max(hi, max(levels))
        lo = min(lo, min(levels))

    margin = max((hi - lo) * 0.08, hi * 0.003)
    hi += margin
    lo -= margin

    for i in range(6):
        yy = y + i * h / 5
        draw.line((x, yy, x + w, yy), fill=GRID, width=1)

    n = len(df)
    cw = max(4, w / n)
    for i, row in enumerate(df.itertuples()):
        o = _num(getattr(row, "Open"))
        c = _num(getattr(row, "Close"))
        hh = _num(getattr(row, "High"))
        ll = _num(getattr(row, "Low"))
        if None in (o, c, hh, ll):
            continue
        color = GREEN if c >= o else RED
        cx = x + i * cw + cw * 0.5
        yhi = _scale_price(hh, lo, hi, y, h)
        ylo = _scale_price(ll, lo, hi, y, h)
        yo = _scale_price(o, lo, hi, y, h)
        yc = _scale_price(c, lo, hi, y, h)
        draw.line((cx, yhi, cx, ylo), fill=color, width=2)
        top, bot = sorted((yo, yc))
        if bot - top < 2:
            bot = top + 2
        draw.rectangle((cx - cw * 0.26, top, cx + cw * 0.26, bot), fill=color)

    close = pd.to_numeric(df["Close"], errors="coerce")
    for span, color in ((20, BLUE), (50, AMBER)):
        ema = _ema(close, span)
        pts = []
        for i, val in enumerate(ema):
            if pd.isna(val):
                continue
            pts.append((x + i * cw + cw * 0.5, _scale_price(float(val), lo, hi, y, h)))
        if len(pts) > 1:
            draw.line(pts, fill=color, width=3)

    for price, color, label in ((entry, CYAN, "Giriş"), (stop, RED, "Stop"), (target, GREEN, "H1")):
        p = _num(price)
        if p is None:
            continue
        yy = _scale_price(p, lo, hi, y, h)
        draw.line((x, yy, x + w, yy), fill=color, width=2)
        _text(draw, (x + w + 5, yy), f"{label} {p:.2f}", 14, color, True, "lm")


def _metric(draw, x, y, w, title, value, color=TEXT):
    _rounded(draw, (x, y, x + w, y + 78), 14, fill=PANEL2, outline=GRID)
    _text(draw, (x + 14, y + 11), title, 15, MUTED, True)
    _text(draw, (x + 14, y + 37), value, 23, color, True)


def _stage_color(stage):
    if stage == "ÇOK GÜÇLÜ":
        return GREEN
    if stage == "GÜÇLÜ":
        return BLUE
    if stage == "UZAMIŞ":
        return RED
    return AMBER


def build_signal_card(signal, item, state=None):
    symbol = str(signal.get("symbol") or "").replace(".IS", "")
    scope = str(signal.get("signal_scope") or "")
    algo = str(signal.get("main_algorithm") or "").replace("_V3", "").replace("_", " ")
    score = _num(signal.get("score"), 0.0) or 0.0
    stage = (state or {}).get("stage") or ("ÇOK GÜÇLÜ" if score >= 90 else "GÜÇLÜ" if score >= 83 else "ADAY")

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    _rounded(draw, (24, 20, W - 24, 112), 22, fill=(18, 37, 60), outline=GRID)
    _text(draw, (48, 37), symbol, 38, WHITE, True)
    _text(draw, (230, 43), "POZİSYON" if scope == "POSITION" else "GÜN İÇİ", 24, MUTED, True)
    _text(draw, (W - 48, 37), f"{stage} · {score:.0f}/100", 25, _stage_color(stage), True, "ra")
    _text(draw, (48, 82), algo.title(), 17, MUTED)

    entry = _num(signal.get("entry_price"))
    current = _num(signal.get("current_price"), entry)
    stop = _num(signal.get("stop_loss"))
    tp1 = _num(signal.get("tp1"))
    tp2 = _num(signal.get("tp2"))
    tp3 = _num(signal.get("tp3"))

    tf = "4h" if scope == "POSITION" else "15m"
    chart_df = _frame(item, tf)
    _draw_candle_chart(
        draw,
        chart_df,
        (24, 132, W - 24, 650),
        entry=entry,
        stop=stop,
        target=tp1,
        title=f"GERÇEK MUM · {('4 SAAT' if tf == '4h' else '15 DAKİKA')} · EMA20 / EMA50",
    )

    gap = 12
    mw = (W - 48 - gap * 3) / 4
    vals = [
        ("GİRİŞ", f"{entry:.2f}" if entry is not None else "-", CYAN),
        ("CANLI", f"{current:.2f}" if current is not None else "-", GREEN if current and entry and current >= entry else RED),
        ("STOP", f"{stop:.2f}" if stop is not None else "-", RED),
        ("1. HEDEF", f"{tp1:.2f}" if tp1 is not None else "-", GREEN),
    ]
    for i, (title, value, color) in enumerate(vals):
        _metric(draw, 24 + i * (mw + gap), 670, mw, title, value, color)

    rs = _num(signal.get("relative_strength_percentile"))
    rvol = _num(signal.get("session_rvol"))
    r15 = _num(signal.get("rsi_15m"))
    r4 = _num(signal.get("rsi_4h"))
    r1 = _num(signal.get("rsi_1d"))
    day = _num(signal.get("day_change_pct"))

    _rounded(draw, (24, 765, W - 24, 930), 18, fill=PANEL, outline=GRID)
    _text(draw, (44, 782), "TEKNİK ÖZET", 18, MUTED, True)

    tech = [
        ("RSI 15dk", "-" if r15 is None else f"{r15:.1f}"),
        ("RSI 4s", "-" if r4 is None else f"{r4:.1f}"),
        ("RSI 1g", "-" if r1 is None else f"{r1:.1f}"),
        ("Göreceli güç", "-" if rs is None else f"%{rs:.0f}"),
        ("Göreli hacim", "-" if rvol is None else f"{rvol:.2f}x"),
        ("Günlük hareket", "-" if day is None else f"%{day:.2f}"),
    ]
    tw = (W - 88) / 3
    for i, (label, value) in enumerate(tech):
        row = i // 3
        col = i % 3
        xx = 44 + col * tw
        yy = 818 + row * 48
        _text(draw, (xx, yy), label, 14, MUTED)
        _text(draw, (xx + 145, yy), value, 18, TEXT, True)

    _rounded(draw, (24, 950, W - 24, H - 24), 18, fill=(15, 30, 49), outline=GRID)
    _text(draw, (44, 967), "SİNYAL AKIŞI", 17, MUTED, True)
    history = list((state or {}).get("history") or [])
    if not history:
        reasons = signal.get("reasons") or []
        history = [{"time": signal.get("time") or "", "text": x} for x in reasons[:3]]
    yy = 997
    for event in history[-3:]:
        _text(draw, (46, yy), f"{event.get('time','')}  {event.get('text','')}", 14, TEXT)
        yy += 30

    now = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M:%S")
    _text(draw, (W - 34, H - 32), now, 13, MUTED, False, "rb")

    out_dir = Path("cards")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol}_{scope.lower()}_v5.png"
    img.save(path, "PNG", optimize=True)
    return str(path)
