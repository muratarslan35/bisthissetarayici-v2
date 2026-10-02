from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from professional_technical_engine import chart_geometry, most_series, rsi_divergence, rsi_wilder_series

TR_TZ = ZoneInfo("Europe/Istanbul")

W, H = 1920, 1640
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
TEAL = (54, 211, 176)
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


def _line_with_label(draw, x, w, yy, color, label, dash=False, width=2, label_y=None):
    if dash:
        for xx in range(int(x), int(x + w), 18):
            draw.line((xx, yy, min(xx + 10, x + w), yy), fill=color, width=width)
    else:
        draw.line((x, yy, x + w, yy), fill=color, width=width)
    label_y = yy if label_y is None else label_y
    if abs(label_y - yy) > 2:
        draw.line((x + w - 122, yy, x + w - 122, label_y), fill=color, width=1)
    _rounded(draw, (x + w - 142, label_y - 13, x + w + 2, label_y + 13), 6, fill=(11, 23, 38))
    _text(draw, (x + w - 8, label_y), label, 13, color, True, "rm")


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
    _text(draw, (x0 + 22, y0 + 15), title, 27, WHITE, True)
    _text(draw, (x1 - 22, y0 + 18), "Mum · Hacim · EMA20/50 · Yapı", 18, MUTED, False, "ra")

    if df is None or len(df) < 8:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "Mum verisi yok", 26, MUTED, True, "mm")
        return

    df = df.tail(max_bars).copy()
    geometry = chart_geometry(df)
    structures = signal.get("technical_structures") or {}
    sr4 = (structures.get("support_resistance") or {}).get(timeframe) or {}
    hi = float(pd.to_numeric(df["High"], errors="coerce").max())
    lo = float(pd.to_numeric(df["Low"], errors="coerce").min())

    channel = geometry.get("channel") or {}
    trend_lines = geometry.get("trend_lines") or {}
    lower_trend = trend_lines.get("lower") or []
    trend_start = _num(lower_trend[0]) if len(lower_trend) == 2 else None
    trend_end = _num(lower_trend[1]) if len(lower_trend) == 2 else None
    rising_trend = trend_start is not None and trend_end is not None and trend_end > trend_start
    visible_trend = [_num(v) for v in lower_trend if _num(v) is not None] if rising_trend else []
    levels = [v for v in (sr4.get("support"), sr4.get("resistance")) if _num(v) is not None] + visible_trend
    if levels:
        hi = max(hi, max(levels))
        lo = min(lo, min(levels))

    margin = max((hi - lo) * 0.08, hi * 0.003)
    hi += margin
    lo -= margin

    for i in range(7):
        yy = y + i * price_h / 6
        draw.line((x, yy, x + w, yy), fill=GRID, width=1)

    # Channel/triangle geometry remains in the signal engine. It is intentionally
    # not overlaid here: the execution chart shows only actionable references.

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

    if rising_trend:
        _draw_series_line(draw, lower_trend, TEAL, x, w, lo, hi, y, price_h, 4)
        trend_y = _scale_price(float(lower_trend[-1]), lo, hi, y, price_h)
        _rounded(draw, (x + 14, trend_y - 16, x + 190, trend_y + 14), 7, fill=(11, 23, 38), outline=TEAL)
        _text(draw, (x + 26, trend_y - 1), "YÜKSELEN TREND", 12, TEAL, True, "lm")

    most_values = most_series(df, period=9, percent=2.0)
    if most_values is not None:
        vals = [float(v) for v in most_values.tail(n)]
        _draw_series_line(draw, vals, (218, 112, 255), x, w, lo, hi, y, price_h, 3)
        _text(draw, (x + 10, y + price_h - 26), f"MOST(9,2) {vals[-1]:.2f}", 13, (218, 112, 255), True)

    structural_levels = (
        (sr4.get("support"), GREEN, f"{timeframe} DESTEK", True),
        (sr4.get("resistance"), RED, f"{timeframe} DİRENÇ", True),
    )
    level_rows = []
    for price, color, label, dashed in structural_levels:
        p = _num(price)
        if p is None:
            continue
        level_rows.append({"p": p, "color": color, "label": label, "dash": dashed, "y": _scale_price(p, lo, hi, y, price_h)})
    level_rows.sort(key=lambda row: row["y"])
    previous = y - 26
    for row in level_rows:
        row["label_y"] = max(row["y"], previous + 27)
        previous = row["label_y"]
    if level_rows and level_rows[-1]["label_y"] > y + price_h - 12:
        shift = level_rows[-1]["label_y"] - (y + price_h - 12)
        for row in level_rows:
            row["label_y"] -= shift
    for row in level_rows:
        _line_with_label(draw, x, w, row["y"], row["color"], f"{row['label']} {row['p']:.2f}", row["dash"], 2, row["label_y"])

    pattern = geometry.get("pattern") or {}
    if pattern:
        name = str(pattern.get("name") or "FORMASYON").replace("_", " ")
        _rounded(draw, (x + 12, y + 12, x + 285, y + 50), 10, fill=(32, 67, 82), outline=CYAN)
        _text(draw, (x + 26, y + 23), f"✓ {name}", 15, CYAN, True)

    # Price axis labels and TradingView-like time markers.
    for i in range(7):
        value = hi - (hi - lo) * i / 6
        yy = y + price_h * i / 6
        _text(draw, (x + w + 10, yy), f"{value:.2f}", 14, MUTED, False, "lm")

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
    _rounded(draw, (x, y, x + w, y + 100), 14, fill=PANEL2, outline=GRID)
    _text(draw, (x + 16, y + 12), title, 17, MUTED, True)
    _text(draw, (x + 16, y + 40), value, 29, color, True)
    if subtitle:
        _text(draw, (x + 16, y + 77), subtitle, 14, MUTED)


def _mini_kpi(draw, x, y, w, h, title, value, color=TEXT):
    _rounded(draw, (x, y, x + w, y + h), 14, fill=PANEL2, outline=GRID)
    _text(draw, (x + 12, y + 14), title, 13, MUTED, True)
    _text(draw, (x + 12, y + 52), value, 25, color, True)


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
    bot_support = signal.get("bot_support") or {}
    bot_badge = "BOT TEYİTLİ · " if bot_support else ""
    _text(draw, (48, 89), f"{bot_badge}{algo.title()}  ·  {phase_tr}", 17, GREEN if bot_support else CYAN, True)
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
    chart_top, chart_bottom = 258, 950
    bottom_top = 970
    chart_df = _frame(item, tf)
    if scope == "POSITION":
        midpoint = W / 2
        _draw_candle_chart(
            draw,
            chart_df,
            (24, chart_top, midpoint - 10, chart_bottom),
            signal,
            title="4 SAATLİK TRADE GRAFİĞİ",
            timeframe="4H",
            max_bars=90,
            show_rsi=True,
        )
        hourly_df = _frame(item, "1h")
        _draw_candle_chart(
            draw,
            hourly_df,
            (midpoint + 10, chart_top, W - 24, chart_bottom),
            signal,
            title="1 SAATLİK TRADE GRAFİĞİ",
            timeframe="1H",
            max_bars=90,
            show_rsi=False,
        )
    else:
        _draw_candle_chart(
            draw,
            chart_df,
            (24, chart_top, W - 24, chart_bottom),
            signal,
            title="15 DAKİKALIK TRADE GRAFİĞİ",
            timeframe="15M",
            max_bars=90,
            show_rsi=True,
        )

    # The upper KPI row already shows entry, stop, and target 1. The wider
    # lower section is reserved for unique market context, evidence, and risk.
    bottom = (24, bottom_top, W - 24, H - 112)
    _rounded(draw, bottom, 20, fill=PANEL, outline=GRID)
    _text(draw, (48, bottom_top + 22), "SİNYALİ OKU · TEYİTLER VE TAKİP", 27, WHITE, True)

    market_box = (44, bottom_top + 78, 448, H - 132)
    _rounded(draw, market_box, 16, fill=(15, 30, 49), outline=GRID)
    _text(draw, (62, bottom_top + 96), "ANLIK DURUM", 20, MUTED, True)
    tech = [
        ("CANLI FİYAT", "-" if current is None else f"{current:.2f}", CYAN),
        ("RSI · 1G", "-" if r1 is None else f"{r1:.1f}", AMBER),
        ("GÜNLÜK", "-" if day is None else f"%{day:.2f}", GREEN if (day or 0) >= 0 else RED),
        ("RVOL", "-" if rvol is None else f"{rvol:.2f}×", BLUE),
    ]
    mini_gap, mini_h = 12, 150
    mini_w = (market_box[2] - market_box[0] - 36 - mini_gap) / 2
    for i, (label, value, color) in enumerate(tech):
        _mini_kpi(
            draw,
            market_box[0] + 12 + (i % 2) * (mini_w + mini_gap),
            bottom_top + 142 + (i // 2) * (mini_h + mini_gap),
            mini_w, mini_h, label, value, color,
        )

    evidence_box = (466, bottom_top + 78, 1290, H - 132)
    _rounded(draw, evidence_box, 16, fill=(15, 30, 49), outline=GRID)
    _text(draw, (evidence_box[0] + 22, bottom_top + 96), "NEDEN SİNYAL?", 23, WHITE, True)
    confirmations = list(signal.get("technical_confirmations") or [])
    if not confirmations:
        confirmations = list(signal.get("reasons") or [])
    elif bot_support:
        evidence = "Bot destekli: teorik eşleşme teyidi" if bot_support.get("evidence") == "TEORIK" else "Bot destekli: derinlik teyidi"
        confirmations.insert(0, evidence)
    evidence_text_x = evidence_box[0] + 76
    yy = bottom_top + 146
    for index, evidence in enumerate(confirmations[:5]):
        if index:
            draw.line((evidence_box[0] + 22, yy - 10, evidence_box[2] - 22, yy - 10), fill=GRID, width=1)
        _rounded(draw, (evidence_box[0] + 22, yy, evidence_box[0] + 57, yy + 35), 12, fill=(24, 58, 53), outline=(33, 99, 80))
        _text(draw, (evidence_box[0] + 39, yy + 17), "✓", 21, GREEN, True, "mm")
        yy = _wrapped(
            draw, (evidence_text_x, yy + 2), evidence,
            evidence_box[2] - evidence_text_x - 22, 24, TEXT, False, 2, 6,
        ) + 14

    warnings = list(signal.get("technical_warnings") or [])
    risk_box = (1308, bottom_top + 78, W - 44, H - 132)
    risk_text_x = risk_box[0] + 24
    risk_text_w = risk_box[2] - risk_text_x - 24
    if warnings:
        _rounded(draw, risk_box, 16, fill=(54, 38, 31), outline=AMBER, width=2)
        _text(draw, (risk_text_x, bottom_top + 102), "TAKİP EDİLECEK RİSK", 23, AMBER, True)
        _rounded(draw, (risk_text_x, bottom_top + 151, risk_box[2] - 24, bottom_top + 191), 12, fill=(83, 54, 36))
        _text(draw, (risk_text_x + 16, bottom_top + 171), "DİKKAT", 17, AMBER, True, "lm")
        ry = bottom_top + 220
        for warning in warnings[:2]:
            _text(draw, (risk_text_x + 2, ry), "•", 24, AMBER, True)
            ry = _wrapped(draw, (risk_text_x + 28, ry), warning, risk_text_w - 32, 22, TEXT, False, 3, 6) + 16
        _wrapped(draw, (risk_text_x, H - 190), "Giriş kararında bu koşulu yeniden kontrol et.", risk_text_w, 19, MUTED, False, 2, 6)
    else:
        _rounded(draw, risk_box, 16, fill=(24, 52, 48), outline=GREEN, width=2)
        _text(draw, (risk_text_x, bottom_top + 102), "RİSK DURUMU", 23, GREEN, True)
        _rounded(draw, (risk_text_x, bottom_top + 151, risk_box[2] - 24, bottom_top + 191), 12, fill=(30, 75, 58))
        _text(draw, (risk_text_x + 16, bottom_top + 171), "YAPISAL VETO YOK", 17, GREEN, True, "lm")
        _wrapped(draw, (risk_text_x, bottom_top + 222), "Mevcut sinyalde ek bir teknik uyarı oluşmadı.", risk_text_w, 24, TEXT, False, 4, 8)
        _wrapped(draw, (risk_text_x, H - 190), "İşlem planındaki stop seviyesini koru.", risk_text_w, 19, MUTED, False, 2, 6)

    # Stable color contract, printed on every Telegram card.
    legend = [
        (BLUE, "EMA20 · kısa trend"),
        (AMBER, "EMA50 · ana trend"),
        ((218, 112, 255), "MOST · trend stop"),
        (GREEN, "Destek"),
        (RED, "Direnç"),
        (TEAL, "Yükselen trend"),
    ]
    legend_y = H - 82
    _text(draw, (30, legend_y - 25), "ÇİZGİ RENKLERİ", 13, MUTED, True)
    xx = 30
    legend_step = (W - 60) / len(legend)
    for color, label in legend:
        draw.line((xx, legend_y, xx + 25, legend_y), fill=color, width=5)
        _text(draw, (xx + 34, legend_y), label, 12, TEXT, True, "lm")
        xx += legend_step

    now = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M:%S")
    _text(draw, (30, H - 18), "Ücretsiz OHLCV · Order-block/FVG mum türevidir, L2 emir defteri değildir.", 12, MUTED, False, "lb")
    _text(draw, (W - 30, H - 18), now, 12, MUTED, False, "rb")

    out_dir = Path("cards")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol}_{scope.lower()}_v5.png"
    img.save(path, "PNG", optimize=True)
    return str(path)
