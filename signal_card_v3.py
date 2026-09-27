from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from professional_technical_engine import chart_geometry, most_series, rsi_divergence, rsi_wilder_series

TR_TZ = ZoneInfo("Europe/Istanbul")

W, H = 1440, 2420
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


def _line_with_label(draw, x, w, yy, color, label, dash=False, width=2):
    if dash:
        for xx in range(int(x), int(x + w), 18):
            draw.line((xx, yy, min(xx + 10, x + w), yy), fill=color, width=width)
    else:
        draw.line((x, yy, x + w, yy), fill=color, width=width)
    _rounded(draw, (x + w - 116, yy - 13, x + w + 2, yy + 13), 6, fill=(11, 23, 38))
    _text(draw, (x + w - 8, yy), label, 13, color, True, "rm")


def _draw_series_line(draw, values, color, x, w, lo, hi, y, h, width=2):
    if not values or any(_num(v) is None for v in values):
        return
    pts = []
    count = len(values)
    for i, value in enumerate(values):
        xx = x + (i / max(count - 1, 1)) * w
        pts.append((xx, _scale_price(float(value), lo, hi, y, h)))
    if len(pts) > 1:
        draw.line(pts, fill=color, width=width)


def _draw_candle_chart(draw, df, box, signal, title="", timeframe="4H", max_bars=90, show_rsi=True):
    x0, y0, x1, y1 = box
    pad_l, pad_r, pad_t, pad_b = 66, 92, 64, 58
    x = x0 + pad_l
    y = y0 + pad_t
    w = x1 - x0 - pad_l - pad_r
    h = y1 - y0 - pad_t - pad_b
    reserved = 190 if show_rsi else 92
    price_h = h - reserved
    volume_y, volume_h = y + price_h + 10, 52
    rsi_y, rsi_h = volume_y + volume_h + 22, 88

    _rounded(draw, box, 18, fill=(14, 28, 46), outline=GRID)
    _text(draw, (x0 + 22, y0 + 15), title, 22, WHITE, True)
    _text(draw, (x1 - 22, y0 + 18), "Mum · Hacim · EMA20/50 · Yapı", 15, MUTED, False, "ra")

    if df is None or len(df) < 8:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "Mum verisi yok", 26, MUTED, True, "mm")
        return

    df = df.tail(max_bars).copy()
    geometry = chart_geometry(df)
    structures = signal.get("technical_structures") or {}
    sr4 = (structures.get("support_resistance") or {}).get(timeframe) or {}
    entry = _num(signal.get("entry_price"))
    stop = _num(signal.get("stop_loss"))
    target = _num(signal.get("tp1"))
    hi = float(pd.to_numeric(df["High"], errors="coerce").max())
    lo = float(pd.to_numeric(df["Low"], errors="coerce").min())

    channel = geometry.get("channel") or {}
    trend_lines = geometry.get("trend_lines") or {}
    extra = []
    for values in list(channel.values()) + list(trend_lines.values()):
        extra.extend([_num(v) for v in values if _num(v) is not None])
    levels = [v for v in (entry, stop, target, sr4.get("support"), sr4.get("resistance")) if _num(v) is not None] + extra
    if levels:
        hi = max(hi, max(levels))
        lo = min(lo, min(levels))

    margin = max((hi - lo) * 0.08, hi * 0.003)
    hi += margin
    lo -= margin

    for i in range(7):
        yy = y + i * price_h / 6
        draw.line((x, yy, x + w, yy), fill=GRID, width=1)

    # Rising-channel bands sit behind candles, like a TradingView overlay.
    _draw_series_line(draw, channel.get("upper"), (73, 103, 135), x, w, lo, hi, y, price_h, 2)
    _draw_series_line(draw, channel.get("middle"), CYAN, x, w, lo, hi, y, price_h, 3)
    _draw_series_line(draw, channel.get("lower"), (73, 103, 135), x, w, lo, hi, y, price_h, 2)

    if trend_lines.get("upper"):
        _draw_series_line(draw, trend_lines["upper"], AMBER, x, w, lo, hi, y, price_h, 3)
    if trend_lines.get("lower"):
        _draw_series_line(draw, trend_lines["lower"], GREEN, x, w, lo, hi, y, price_h, 3)

    n = len(df)
    cw = max(4, w / n)
    volume = pd.to_numeric(df.get("Volume"), errors="coerce") if "Volume" in df else None
    vmax = float(volume.max()) if volume is not None and not volume.empty else 0.0
    for i, row in enumerate(df.itertuples()):
        o = _num(getattr(row, "Open"))
        c = _num(getattr(row, "Close"))
        hh = _num(getattr(row, "High"))
        ll = _num(getattr(row, "Low"))
        if None in (o, c, hh, ll):
            continue
        color = GREEN if c >= o else RED
        cx = x + i * cw + cw * 0.5
        yhi = _scale_price(hh, lo, hi, y, price_h)
        ylo = _scale_price(ll, lo, hi, y, price_h)
        yo = _scale_price(o, lo, hi, y, price_h)
        yc = _scale_price(c, lo, hi, y, price_h)
        draw.line((cx, yhi, cx, ylo), fill=color, width=2)
        top, bot = sorted((yo, yc))
        if bot - top < 2:
            bot = top + 2
        draw.rectangle((cx - cw * 0.26, top, cx + cw * 0.26, bot), fill=color)
        if vmax > 0 and volume is not None:
            vh = float(volume.iloc[i]) / vmax * volume_h
            draw.rectangle((cx - cw * 0.28, volume_y + volume_h - vh, cx + cw * 0.28, volume_y + volume_h), fill=(*color[:3],))

    close = pd.to_numeric(df["Close"], errors="coerce")
    for span, color in ((20, BLUE), (50, AMBER)):
        ema = _ema(close, span)
        pts = []
        for i, val in enumerate(ema):
            if pd.isna(val):
                continue
            pts.append((x + i * cw + cw * 0.5, _scale_price(float(val), lo, hi, y, price_h)))
        if len(pts) > 1:
            draw.line(pts, fill=color, width=3)

    most_values = most_series(df, period=9, percent=2.0)
    if most_values is not None:
        vals = [float(v) for v in most_values.tail(n)]
        _draw_series_line(draw, vals, (218, 112, 255), x, w, lo, hi, y, price_h, 3)
        _text(draw, (x + 10, y + price_h - 26), f"MOST(9,2) {vals[-1]:.2f}", 13, (218, 112, 255), True)

    structural_levels = (
        (sr4.get("support"), GREEN, f"{timeframe} DESTEK", True),
        (sr4.get("resistance"), RED, f"{timeframe} DİRENÇ", True),
        (entry, CYAN, "GİRİŞ", False),
        (stop, RED, "STOP", False),
        (target, GREEN, "H1", False),
    )
    for price, color, label, dashed in structural_levels:
        p = _num(price)
        if p is None:
            continue
        yy = _scale_price(p, lo, hi, y, price_h)
        _line_with_label(draw, x, w, yy, color, f"{label} {p:.2f}", dashed, 2)

    pattern = geometry.get("pattern") or {}
    if pattern:
        name = str(pattern.get("name") or "FORMASYON").replace("_", " ")
        _rounded(draw, (x + 12, y + 12, x + 285, y + 50), 10, fill=(32, 67, 82), outline=CYAN)
        _text(draw, (x + 26, y + 23), f"✓ {name}", 15, CYAN, True)

    # Price axis labels and TradingView-like time markers.
    for i in range(7):
        value = hi - (hi - lo) * i / 6
        yy = y + price_h * i / 6
        _text(draw, (x + w + 10, yy), f"{value:.2f}", 12, MUTED, False, "lm")

    if not show_rsi:
        if isinstance(df.index, pd.DatetimeIndex):
            for i in (0, len(df) // 2, len(df) - 1):
                ts = df.index[i]
                _text(draw, (x + i * cw + cw * 0.5, volume_y + volume_h + 9), ts.strftime("%d.%m\n%H:%M"), 11, MUTED, False, "ma")
        return

    # RSI(14) panel; values are derived from the same displayed bars.
    draw.line((x, rsi_y, x + w, rsi_y), fill=GRID, width=1)
    rsi = rsi_wilder_series(df["Close"], 14).tail(n)
    for level, color in ((70, RED), (50, GRID), (30, GREEN)):
        yy = rsi_y + (100 - level) / 100 * rsi_h
        for xx in range(int(x), int(x + w), 18):
            draw.line((xx, yy, min(xx + 9, x + w), yy), fill=color, width=1)
        _text(draw, (x + w + 10, yy), str(level), 11, color, False, "lm")
    rsi_pts = []
    for i, value in enumerate(rsi):
        if pd.notna(value):
            rsi_pts.append((x + i * cw + cw * 0.5, rsi_y + (100 - float(value)) / 100 * rsi_h))
    if len(rsi_pts) > 1:
        draw.line(rsi_pts, fill=AMBER, width=3)
    divergence = rsi_divergence(df)
    dtype = divergence.get("type")
    dcolor = GREEN if dtype == "BULLISH" else RED if dtype == "BEARISH" else MUTED
    _text(draw, (x + 8, rsi_y + 5), f"{timeframe} RSI(14) {float(rsi.dropna().iloc[-1]):.1f} · {divergence.get('label')}", 13, dcolor, True)
    pivots = divergence.get("pivots") or []
    if len(pivots) == 2 and all(0 <= p < n for p in pivots):
        points = []
        for p in pivots:
            value = rsi.iloc[p]
            if pd.notna(value):
                points.append((x + p * cw + cw * 0.5, rsi_y + (100 - float(value)) / 100 * rsi_h))
        if len(points) == 2:
            draw.line(points, fill=dcolor, width=4)
    if isinstance(df.index, pd.DatetimeIndex):
        for i in (0, len(df) // 2, len(df) - 1):
            ts = df.index[i]
            label = ts.strftime("%d.%m\n%H:%M")
            _text(draw, (x + i * cw + cw * 0.5, rsi_y + rsi_h + 8), label, 11, MUTED, False, "ma")


def _metric(draw, x, y, w, title, value, color=TEXT, subtitle=None):
    _rounded(draw, (x, y, x + w, y + 94), 14, fill=PANEL2, outline=GRID)
    _text(draw, (x + 14, y + 12), title, 14, MUTED, True)
    _text(draw, (x + 14, y + 39), value, 25, color, True)
    if subtitle:
        _text(draw, (x + 14, y + 72), subtitle, 11, MUTED)


def _wrapped(draw, xy, text, max_width, size=16, fill=TEXT, bold=False, max_lines=3, line_gap=5):
    words = str(text).split()
    lines, current = [], ""
    font = _font(size, bold)
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    x, y = xy
    for line in lines[:max_lines]:
        draw.text((x, y), line, font=font, fill=fill)
        y += size + line_gap
    return y


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

    _rounded(draw, (24, 20, W - 24, 124), 22, fill=(18, 37, 60), outline=GRID)
    _text(draw, (48, 36), symbol, 42, WHITE, True)
    _text(draw, (250, 45), "POZİSYON" if scope == "POSITION" else "GÜN İÇİ", 23, MUTED, True)
    phase = str(signal.get("market_structure_phase") or "-")
    phase_tr = {"STARTING": "HAREKET BAŞLANGICI", "EARLY_TREND": "ERKEN TREND"}.get(phase, phase)
    _text(draw, (48, 89), f"{algo.title()}  ·  {phase_tr}", 17, CYAN, True)
    _text(draw, (W - 48, 39), f"{stage} · {score:.0f}/100", 27, _stage_color(stage), True, "ra")
    _text(draw, (W - 48, 85), "PROFESYONEL YAPI MOTORU", 14, MUTED, True, "ra")

    entry = _num(signal.get("entry_price"))
    current = _num(signal.get("current_price"), entry)
    stop = _num(signal.get("stop_loss"))
    tp1 = _num(signal.get("tp1"))
    tp2 = _num(signal.get("tp2"))
    tp3 = _num(signal.get("tp3"))

    rs = _num(signal.get("relative_strength_percentile"))
    rvol = _num(signal.get("session_rvol"))
    r15 = _num(signal.get("rsi_15m"))
    r4 = _num(signal.get("rsi_4h"))
    r1 = _num(signal.get("rsi_1d"))
    day = _num(signal.get("day_change_pct"))

    # The first row answers the trader's immediate decision questions.
    gap = 12
    mw = (W - 48 - gap * 5) / 6
    kpis = [
        ("SİNYAL GÜCÜ", f"{score:.0f}/100", _stage_color(stage), phase_tr),
        ("GİRİŞ", f"{entry:.2f}" if entry is not None else "-", CYAN, "planlanan"),
        ("STOP", f"{stop:.2f}" if stop is not None else "-", RED, f"risk %{signal.get('risk_pct','-')}"),
        ("HEDEF 1", f"{tp1:.2f}" if tp1 is not None else "-", GREEN, "ilk realize"),
        ("RS GÜCÜ", "-" if rs is None else f"%{rs:.0f}", BLUE, "BIST evreni"),
        ("4H RSI", "-" if r4 is None else f"{r4:.1f}", AMBER, "trend momentumu"),
    ]
    for i, values in enumerate(kpis):
        _metric(draw, 24 + i * (mw + gap), 144, mw, *values)

    tf = "4h" if scope == "POSITION" else "15m"
    chart_df = _frame(item, tf)
    _draw_candle_chart(
        draw,
        chart_df,
        (24, 258, W - 24, 1120),
        signal,
        title=f"{('4 SAATLİK' if tf == '4h' else '15 DAKİKALIK')} TRADE GRAFİĞİ",
        timeframe="4H" if tf == "4h" else "15M",
        max_bars=90,
        show_rsi=True,
    )

    # Position cards also carry a denser 1H execution view in the same image.
    bottom_top = 1140
    if scope == "POSITION":
        hourly_df = _frame(item, "1h")
        _draw_candle_chart(
            draw,
            hourly_df,
            (24, 1140, W - 24, 1880),
            signal,
            title="1 SAATLİK YAKIN PLAN · GİRİŞ ZAMANLAMASI",
            timeframe="1H",
            max_bars=120,
            show_rsi=False,
        )
        bottom_top = 1900

    # Bottom-left: trade plan and compact technical KPIs.
    left = (24, bottom_top, 552, H - 48)
    _rounded(draw, left, 18, fill=PANEL, outline=GRID)
    oy = bottom_top - 1140
    _text(draw, (46, 1160 + oy), "TRADE PLANI", 20, WHITE, True)
    _text(draw, (46, 1200 + oy), "Giriş", 14, MUTED, True)
    _text(draw, (182, 1195 + oy), f"{entry:.2f}" if entry else "-", 24, CYAN, True)
    _text(draw, (46, 1242 + oy), "Stop", 14, MUTED, True)
    _text(draw, (182, 1237 + oy), f"{stop:.2f}" if stop else "-", 24, RED, True)
    _text(draw, (46, 1284 + oy), "Hedefler", 14, MUTED, True)
    _text(draw, (182, 1279 + oy), " / ".join(f"{x:.2f}" for x in (tp1, tp2, tp3) if x is not None) or "-", 21, GREEN, True)
    _text(draw, (46, 1334 + oy), "TEKNİK KPI", 17, MUTED, True)
    tech = [
        ("RSI 1D", "-" if r1 is None else f"{r1:.1f}"),
        ("Hacim", "-" if rvol is None else f"{rvol:.2f}x"),
        ("Günlük", "-" if day is None else f"%{day:.2f}"),
        ("Canlı", "-" if current is None else f"{current:.2f}"),
    ]
    for i, (label, value) in enumerate(tech):
        xx = 46 + (i % 2) * 248
        yy = 1370 + oy + (i // 2) * 72
        _text(draw, (xx, yy), label, 13, MUTED, True)
        _text(draw, (xx, yy + 24), value, 21, TEXT, True)
    calibration = signal.get("calibration_status") or "LEARNING"
    sample = signal.get("calibration_sample_size") or 0
    _rounded(draw, (44, 1524 + oy, 532, 1608 + oy), 12, fill=(15, 30, 49), outline=GRID)
    _text(draw, (62, 1540 + oy), "İSTATİSTİKSEL DOĞRULAMA", 13, MUTED, True)
    _text(draw, (62, 1568 + oy), f"{calibration} · n={sample}", 19, AMBER if calibration != "CALIBRATED" else GREEN, True)

    # Bottom-right: only evidence that formed; no generic indicator dump.
    right = (572, bottom_top, W - 24, H - 48)
    _rounded(draw, right, 18, fill=PANEL, outline=GRID)
    _text(draw, (596, 1160 + oy), "NEDEN SİNYAL?", 20, WHITE, True)
    confirmations = list(signal.get("technical_confirmations") or [])
    if not confirmations:
        confirmations = list(signal.get("reasons") or [])
    yy = 1202 + oy
    for evidence in confirmations[:5]:
        _text(draw, (598, yy), "✓", 18, GREEN, True)
        yy = _wrapped(draw, (628, yy), evidence, 744, 16, TEXT, False, 2, 4) + 12

    warnings = list(signal.get("technical_warnings") or [])
    if warnings:
        _rounded(draw, (594, 1485 + oy, W - 46, 1598 + oy), 12, fill=(54, 38, 31), outline=AMBER)
        _text(draw, (614, 1501 + oy), "İZLENEN RİSK", 14, AMBER, True)
        _wrapped(draw, (614, 1530 + oy), warnings[0], 730, 15, TEXT, False, 2, 4)
    else:
        _rounded(draw, (594, 1510 + oy, W - 46, 1598 + oy), 12, fill=(24, 52, 48), outline=GREEN)
        _text(draw, (614, 1527 + oy), "YAPISAL VETO YOK", 15, GREEN, True)
        _text(draw, (614, 1555 + oy), "Stop seviyesi yapının bozulduğu alanı temsil eder.", 14, MUTED)

    now = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M:%S")
    _text(draw, (30, H - 22), "Ücretsiz OHLCV · Order-block/FVG mum türevidir, L2 emir defteri değildir.", 12, MUTED, False, "lb")
    _text(draw, (W - 30, H - 22), now, 12, MUTED, False, "rb")

    out_dir = Path("cards")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol}_{scope.lower()}_v5.png"
    img.save(path, "PNG", optimize=True)
    return str(path)
