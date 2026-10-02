from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from professional_technical_engine import chart_geometry, most, rsi_divergence, rsi_wilder_series

TR_TZ = ZoneInfo("Europe/Istanbul")

W, H = 1920, 1640
BG = (8, 17, 30)
PANEL = (17, 31, 49)
PANEL2 = (24, 43, 64)
GRID = (43, 60, 79)
TEXT = (237, 243, 250)
MUTED = (158, 177, 198)
GREEN = (44, 197, 164)
RED = (238, 105, 117)
AMBER = (244, 190, 99)
BLUE = (91, 169, 255)
CYAN = (93, 198, 219)
CHANNEL_UPPER = (99, 196, 189)
CHANNEL_MIDDLE = (130, 147, 169)
CHANNEL_LOWER = (55, 151, 142)
PATTERN_LINE = (226, 170, 100)
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


def _draw_price_path(draw, values, color, x0, x1, low, high, top, height, width=2, dashed=False):
    if len(values) < 2:
        return
    points = [
        (x0 + i * (x1 - x0) / (len(values) - 1), _scale_price(value, low, high, top, height))
        for i, value in enumerate(values)
    ]
    if dashed:
        for p0, p1 in zip(points, points[1:]):
            steps = max(1, int(abs(p1[0] - p0[0]) / 10))
            for step in range(0, steps, 2):
                t0, t1 = step / steps, min(step + 1, steps) / steps
                a = (p0[0] + (p1[0] - p0[0]) * t0, p0[1] + (p1[1] - p0[1]) * t0)
                b = (p0[0] + (p1[0] - p0[0]) * t1, p0[1] + (p1[1] - p0[1]) * t1)
                draw.line((a, b), fill=color, width=width)
    else:
        draw.line(points, fill=color, width=width)


def _channel_position(close, channel):
    try:
        lower = float(channel["lower"][-1])
        upper = float(channel["upper"][-1])
        if upper <= lower:
            return "KANAL HESAPLANAMADI", MUTED
        ratio = (float(close) - lower) / (upper - lower)
    except (KeyError, TypeError, ValueError, IndexError):
        return "KANAL HESAPLANAMADI", MUTED
    if ratio < 0:
        return "KANAL ALTINDA", RED
    if ratio <= 0.15:
        return "ALT BANT · DESTEĞE YAKIN", GREEN
    if ratio < 0.38:
        return "ALT BANTTA", GREEN
    if ratio < 0.62:
        return "ORTA BANTTA", CYAN
    if ratio < 0.85:
        return "ÜST BANTTA", AMBER
    if ratio <= 1:
        return "ÜST BANT · UZAMIŞ", RED
    return "KANAL ÜSTÜNDE", RED


def _triangle_shape(geometry):
    lines = geometry.get("trend_lines") or {}
    upper, lower = lines.get("upper") or [], lines.get("lower") or []
    if len(upper) != 2 or len(lower) != 2:
        return None
    gap_start, gap_end = upper[0] - lower[0], upper[1] - lower[1]
    if gap_start <= 0 or gap_end <= 0 or gap_end >= gap_start * 0.88:
        return None
    upper_slope = (upper[1] - upper[0]) / max(abs(upper[0]), 1e-9)
    lower_slope = (lower[1] - lower[0]) / max(abs(lower[0]), 1e-9)
    threshold = 0.003
    if upper_slope < -threshold and lower_slope > threshold:
        return "SİMETRİK ÜÇGEN"
    if abs(upper_slope) <= threshold and lower_slope > threshold:
        return "YÜKSELEN ÜÇGEN"
    if upper_slope < -threshold and abs(lower_slope) <= threshold:
        return "ALÇALAN ÜÇGEN"
    if upper_slope > 0 and lower_slope > 0:
        return "YÜKSELEN SIKIŞMA"
    if upper_slope < 0 and lower_slope < 0:
        return "DÜŞEN SIKIŞMA"
    return "TREND SIKIŞMASI"


def _draw_candle_chart(draw, df, box, signal, title="", timeframe="4H", max_bars=None, show_rsi=True):
    x0, y0, x1, y1 = box
    pad_l, pad_r, pad_t, pad_b = 66, 92, 64, 58
    x = x0 + pad_l
    y = y0 + pad_t
    w = x1 - x0 - pad_l - pad_r
    h = y1 - y0 - pad_t - pad_b
    reserved = 190 if show_rsi else 92
    price_h = h - reserved
    volume_y, volume_h = y + price_h + 10, 52
    rsi_y, rsi_h = volume_y + volume_h + 16, 88

    _rounded(draw, box, 18, fill=(12, 24, 39), outline=GRID)
    _text(draw, (x0 + 22, y0 + 15), title, 27, WHITE, True)

    if df is None or len(df) < 8:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "Mum verisi yok", 26, MUTED, True, "mm")
        return

    if max_bars is None:
        # Use the available history up to a practical TradingView-like candle
        # density, with enough pixels left for readable wicks and bodies.
        max_bars = max(90, int(w / 7.5))
    df = df.tail(max_bars).copy()
    geometry = chart_geometry(df, lookback=len(df))
    hi = float(pd.to_numeric(df["High"], errors="coerce").max())
    lo = float(pd.to_numeric(df["Low"], errors="coerce").min())

    channel = geometry.get("channel") or {}
    trend_lines = geometry.get("trend_lines") or {}
    triangle = _triangle_shape(geometry)
    channel_values = [value for band in channel.values() for value in (band or [])]
    triangle_values = [value for line in trend_lines.values() for value in (line or [])] if triangle else []
    levels = channel_values + triangle_values
    if levels:
        hi = max(hi, max(levels))
        lo = min(lo, min(levels))

    margin = max((hi - lo) * 0.08, hi * 0.003)
    hi += margin
    lo -= margin

    n = len(df)
    cw = max(4, w / n)
    geometry_bars = n
    geometry_x = x + cw * 0.5
    geometry_right = x + (n - 0.5) * cw
    channel_paths = {}
    for key in ("upper", "middle", "lower"):
        endpoints = channel.get(key) or []
        if len(endpoints) == 2 and endpoints[0] > 0 and endpoints[1] > 0:
            channel_paths[key] = np.exp(np.linspace(np.log(endpoints[0]), np.log(endpoints[1]), geometry_bars))
    def channel_points(path):
        return [
            (geometry_x + i * (geometry_right - geometry_x) / max(geometry_bars - 1, 1), _scale_price(value, lo, hi, y, price_h))
            for i, value in enumerate(path)
        ]
    if all(key in channel_paths for key in ("upper", "middle", "lower")):
        upper_points = channel_points(channel_paths["upper"])
        middle_points = channel_points(channel_paths["middle"])
        lower_points = channel_points(channel_paths["lower"])
        draw.polygon(upper_points + list(reversed(middle_points)), fill=(21, 42, 56))
        draw.polygon(middle_points + list(reversed(lower_points)), fill=(17, 48, 49))

    # Give volume and RSI their own shaded strips beneath the price plot so
    # neither indicator can be mistaken for candlestick data.
    draw.rectangle((x, volume_y, x + w, volume_y + volume_h), fill=(9, 19, 32))
    if show_rsi:
        draw.rectangle((x, rsi_y, x + w, rsi_y + rsi_h), fill=(10, 22, 36))
    draw.line((x, volume_y, x + w, volume_y), fill=GRID, width=1)
    draw.line((x, volume_y + volume_h, x + w, volume_y + volume_h), fill=GRID, width=1)
    if show_rsi:
        draw.line((x, rsi_y, x + w, rsi_y), fill=GRID, width=1)
        draw.line((x, rsi_y + rsi_h, x + w, rsi_y + rsi_h), fill=GRID, width=1)

    for i in range(6):
        yy = y + i * price_h / 5
        draw.line((x, yy, x + w, yy), fill=GRID, width=1)

    # Fit the channel to all visible candles; no independent support or
    # resistance overlays are drawn on top of the channel.
    for key, color, dashed, width in (
        ("upper", CHANNEL_UPPER, False, 2),
        ("middle", CHANNEL_MIDDLE, True, 2),
        ("lower", CHANNEL_LOWER, False, 2),
    ):
        endpoints = channel.get(key) or []
        if len(endpoints) == 2 and endpoints[0] > 0 and endpoints[1] > 0:
            _draw_price_path(draw, channel_paths[key], color, geometry_x, geometry_right, lo, hi, y, price_h, width, dashed)
    if triangle:
        for key in ("upper", "lower"):
            endpoints = trend_lines.get(key) or []
            if len(endpoints) == 2:
                _draw_price_path(draw, endpoints, PATTERN_LINE, geometry_x, geometry_right, lo, hi, y, price_h, 2, True)

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

    _text(draw, (x + w + 10, volume_y + 3), "HACİM", 12, MUTED, True, "lm")

    pattern = geometry.get("pattern") or {}
    if triangle:
        pattern_labels = {
            "SYMMETRIC_TRIANGLE": "SİMETRİK ÜÇGEN",
            "ASCENDING_TRIANGLE": "YÜKSELEN ÜÇGEN",
            "DESCENDING_TRIANGLE": "ALÇALAN ÜÇGEN",
            "RISING_WEDGE": "YÜKSELEN KAMA",
            "FALLING_WEDGE": "DÜŞEN KAMA",
        }
        name = pattern_labels.get(pattern.get("name"), triangle)
        _rounded(draw, (x + 14, y + 12, x + 320, y + 46), 9, fill=(49, 42, 32), outline=PATTERN_LINE)
        _text(draw, (x + 27, y + 29), name, 14, PATTERN_LINE, True, "lm")

    channel_label, channel_color = _channel_position(float(df["Close"].iloc[-1]), channel)
    channel_heading = channel_label if channel_label.startswith("KANAL ") else f"KANAL · {channel_label}"
    _text(draw, (x1 - 26, y0 + 17), channel_heading, 16, channel_color, True, "ra")
    most_result = most(df, period=9, percent=2.0)
    if most_result:
        direction = "YUKARI" if most_result.get("trend") == "UP" else "AŞAĞI"
        most_color = GREEN if direction == "YUKARI" else RED
        _text(draw, (x1 - 26, y0 + 42), f"MOST {timeframe} · {most_result.get('level', 0):.2f} · {direction}", 15, most_color, True, "ra")

    # Price axis labels and TradingView-like time markers.
    for i in range(6):
        value = hi - (hi - lo) * i / 5
        yy = y + price_h * i / 5
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
        line_color = GRID if level == 50 else (54, 71, 88)
        draw.line((x, yy, x + w, yy), fill=line_color, width=1)
        if level in (70, 30):
            _text(draw, (x + w + 10, yy), str(level), 12, color, True, "lm")
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
    draw.rounded_rectangle((x + 2, y + 2, x + w - 2, y + 6), radius=2, fill=color)
    _text(draw, (x + 16, y + 12), title, 17, MUTED, True)
    _text(draw, (x + 16, y + 40), value, 29, color, True)
    if subtitle:
        _text(draw, (x + 16, y + 77), subtitle, 14, MUTED)


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
    height = 2200 if scope == "POSITION" else H

    img = Image.new("RGB", (W, height), BG)
    draw = ImageDraw.Draw(img)

    _rounded(draw, (24, 20, W - 24, 124), 22, fill=(18, 37, 60), outline=GRID)
    _text(draw, (48, 36), symbol, 42, WHITE, True)
    _text(draw, (250, 45), "POZİSYON" if scope == "POSITION" else "GÜN İÇİ", 23, MUTED, True)
    phase = str(signal.get("market_structure_phase") or "-")
    phase_tr = {"STARTING": "HAREKET BAŞLANGICI", "EARLY_TREND": "ERKEN TREND"}.get(phase, phase)
    bot_support = signal.get("bot_support") or {}
    bot_badge = "BOT TEYİTLİ · " if bot_support else ""
    algo_label = {"TREND START": "TREND BAŞLANGICI"}.get(algo.upper(), algo.title())
    _text(draw, (48, 89), f"{bot_badge}{algo_label}", 17, GREEN if bot_support else CYAN, True)
    _text(draw, (W - 48, 65), "BIST · TEKNİK SİNYAL", 15, MUTED, True, "ra")

    entry = _num(signal.get("entry_price"))
    stop = _num(signal.get("stop_loss"))
    tp1 = _num(signal.get("tp1"))
    tp2 = _num(signal.get("tp2"))
    tp3 = _num(signal.get("tp3"))

    rs = _num(signal.get("relative_strength_percentile"))
    r15 = _num(signal.get("rsi_15m"))
    r4 = _num(signal.get("rsi_4h"))
    # The first row answers the trader's immediate decision questions.
    gap = 12
    mw = (W - 48 - gap * 5) / 6
    kpis = [
        ("SİNYAL GÜCÜ", f"{score:.0f}/100", _stage_color(stage), None),
        ("GİRİŞ", f"{entry:.2f}" if entry is not None else "-", CYAN, "planlanan"),
        ("STOP", f"{stop:.2f}" if stop is not None else "-", RED, f"risk %{signal.get('risk_pct','-')}"),
        ("HEDEF 1", f"{tp1:.2f}" if tp1 is not None else "-", GREEN, "ilk realize"),
        ("RS GÜCÜ", "-" if rs is None else f"%{rs:.0f}", BLUE, "BIST evreni"),
        ("4H RSI", "-" if r4 is None else f"{r4:.1f}", AMBER, "trend momentumu"),
    ]
    for i, values in enumerate(kpis):
        _metric(draw, 24 + i * (mw + gap), 144, mw, *values)

    tf = "4h" if scope == "POSITION" else "15m"
    chart_top = 258
    chart_df = _frame(item, tf)
    if scope == "POSITION":
        chart_bottom = 930
        hourly_top, hourly_bottom = 950, 1622
        bottom_top = 1642
        _draw_candle_chart(
            draw,
            chart_df,
            (24, chart_top, W - 24, chart_bottom),
            signal,
            title="4 SAATLİK TRADE GRAFİĞİ",
            timeframe="4H",
            max_bars=None,
            show_rsi=True,
        )
        hourly_df = _frame(item, "1h")
        _draw_candle_chart(
            draw,
            hourly_df,
            (24, hourly_top, W - 24, hourly_bottom),
            signal,
            title="1 SAATLİK TRADE GRAFİĞİ",
            timeframe="1H",
            max_bars=None,
            show_rsi=True,
        )
    else:
        chart_bottom = 950
        bottom_top = 970
        _draw_candle_chart(
            draw,
            chart_df,
            (24, chart_top, W - 24, chart_bottom),
            signal,
            title="15 DAKİKALIK TRADE GRAFİĞİ",
            timeframe="15M",
            max_bars=None,
            show_rsi=True,
        )

    # The top row already contains the trade levels. Keep the lower area open,
    # with compact signal evidence and risk text instead of returning to cards.
    draw.line((24, bottom_top, W - 24, bottom_top), fill=GRID, width=2)
    _text(draw, (44, bottom_top + 20), "SİNYAL TEYİTLERİ", 24, WHITE, True)
    confirmations = list(signal.get("technical_confirmations") or [])
    if not confirmations:
        confirmations = list(signal.get("reasons") or [])
    elif bot_support:
        evidence = "Bot destekli: teorik eşleşme teyidi" if bot_support.get("evidence") == "TEORIK" else "Bot destekli: derinlik teyidi"
        confirmations.insert(0, evidence)
    col_width = (W - 120) / 3
    for index, evidence in enumerate(confirmations[:5]):
        col, row = index % 3, index // 3
        xx, yy = 44 + col * col_width, bottom_top + 62 + row * 58
        _text(draw, (xx, yy + 1), "✓", 20, GREEN, True)
        _wrapped(draw, (xx + 32, yy), evidence, col_width - 42, 19, TEXT, False, 2, 4)

    warnings = list(signal.get("technical_warnings") or [])
    risk_y = bottom_top + 202
    draw.line((44, risk_y - 12, W - 44, risk_y - 12), fill=GRID, width=1)
    if warnings:
        _text(draw, (44, risk_y), "İZLENECEK RİSK", 18, AMBER, True)
        risk_summary = " · ".join(str(warning) for warning in warnings[:2])
        _wrapped(draw, (230, risk_y), risk_summary, W - 460, 19, TEXT, False, 2, 4)
        _text(draw, (W - 44, height - 150), "Girişte bu uyarıyı yeniden kontrol et.", 17, MUTED, False, "ra")
    else:
        _text(draw, (44, risk_y), "RİSK DURUMU", 18, GREEN, True)
        _text(draw, (230, risk_y), "Yapısal veto yok · işlem planındaki stop seviyesini koru.", 19, TEXT)

    # Keep the key limited to lines that are actually visible on this card.
    legend = [
        (CHANNEL_UPPER, "KANAL ÜST BANDI"),
        (CHANNEL_MIDDLE, "KANAL ORTA BANDI"),
        (CHANNEL_LOWER, "KANAL ALT BANDI"),
    ]
    chart_frames = [chart_df] + ([hourly_df] if scope == "POSITION" else [])
    if any(_triangle_shape(chart_geometry(frame)) for frame in chart_frames if frame is not None):
        legend.append((PATTERN_LINE, "ÜÇGEN / TREND SIKIŞMASI"))
    legend_y = height - 82
    _text(draw, (30, legend_y - 25), "GRAFİK GÖSTERGELERİ", 14, MUTED, True)
    xx = 30
    legend_step = (W - 60) / len(legend)
    for color, label in legend:
        draw.line((xx, legend_y, xx + 25, legend_y), fill=color, width=4)
        _text(draw, (xx + 34, legend_y), label, 14, TEXT, True, "lm")
        xx += legend_step

    now = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M:%S")
    _text(draw, (30, height - 18), "Ücretsiz OHLCV · Order-block/FVG mum türevidir, L2 emir defteri değildir.", 14, MUTED, False, "lb")
    _text(draw, (W - 30, height - 18), now, 14, MUTED, False, "rb")

    out_dir = Path("cards")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol}_{scope.lower()}_v5.png"
    img.save(path, "PNG", optimize=True)
    return str(path)
