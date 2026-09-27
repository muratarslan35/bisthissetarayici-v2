"""Professional, deterministic market-structure analysis for POSITION signals.

The engine does not make a trade because many indicators happen to be green.
It identifies the phase of the move, rewards only confirmations relevant to
that phase and keeps exhaustion / failed-breakout evidence as a veto layer.
All calculations use already-cached OHLCV frames; no extra data request is
made and no repainting value from an unfinished candle is required.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math

import numpy as np
import pandas as pd


ENGINE_VERSION = "MARKET_STRUCTURE_V1"


def _frame(df, minimum=20):
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < minimum:
        return None
    out = df.copy()
    out.columns = [str(c).title() for c in out.columns]
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(out.columns):
        return None
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=list(required))
    return out if len(out) >= minimum else None


def _atr(df, period=14):
    prev = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    value = tr.rolling(period).mean().iloc[-1]
    return float(value) if pd.notna(value) and value > 0 else None


def _volume_ratio(df, period=20):
    if len(df) < period + 1:
        return 0.0
    base = float(df["Volume"].iloc[-period - 1:-1].mean())
    return float(df["Volume"].iloc[-1]) / base if base > 0 else 0.0


def _pivots(df, radius=2, lookback=90):
    work = df.tail(lookback)
    highs, lows = [], []
    h = work["High"].astype(float).to_numpy()
    lo = work["Low"].astype(float).to_numpy()
    offset = len(df) - len(work)
    for i in range(radius, len(work) - radius):
        if h[i] >= max(h[i - radius:i + radius + 1]):
            highs.append((offset + i, float(h[i])))
        if lo[i] <= min(lo[i - radius:i + radius + 1]):
            lows.append((offset + i, float(lo[i])))
    return highs, lows


def _line(points):
    if len(points) < 2:
        return None
    x = np.asarray([p[0] for p in points], dtype=float)
    y = np.asarray([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    denom = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y - fitted) ** 2)) / denom if denom > 0 else 1.0
    return {"slope": float(slope), "intercept": float(intercept), "r2": r2}


def _at(line, x):
    return line["slope"] * x + line["intercept"]


def _cluster_levels(points, tolerance):
    levels = []
    for _, value in sorted(points, key=lambda p: p[1]):
        target = next((x for x in levels if abs(x["level"] - value) <= tolerance), None)
        if target:
            n = target["touches"]
            target["level"] = (target["level"] * n + value) / (n + 1)
            target["touches"] += 1
        else:
            levels.append({"level": value, "touches": 1})
    return levels


def support_resistance(df, price):
    atr = _atr(df)
    if not atr:
        return {}
    highs, lows = _pivots(df)
    tolerance = max(atr * 0.35, price * 0.0025)
    supports = _cluster_levels(lows, tolerance)
    resistances = _cluster_levels(highs, tolerance)
    below = [x for x in supports if x["level"] < price]
    above = [x for x in resistances if x["level"] > price]
    support = max(below, key=lambda x: x["level"], default=None)
    resistance = min(above, key=lambda x: x["level"], default=None)
    recent = df.iloc[-3:]
    bounce = bool(
        support
        and recent["Low"].min() <= support["level"] + atr * 0.45
        and df["Close"].iloc[-1] > support["level"] + atr * 0.20
        and df["Close"].iloc[-1] > df["Open"].iloc[-1]
    )
    return {
        "support": round(support["level"], 4) if support else None,
        "support_touches": support["touches"] if support else 0,
        "resistance": round(resistance["level"], 4) if resistance else None,
        "resistance_touches": resistance["touches"] if resistance else 0,
        "support_bounce": bounce,
        "overhead_atr": ((resistance["level"] - price) / atr) if resistance else None,
    }


def regression_channel(df, lookback=60):
    work = df.tail(lookback)
    if len(work) < 30:
        return {}
    y = np.log(work["Close"].astype(float).to_numpy())
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    center = slope * x + intercept
    residual = y - center
    sigma = float(np.std(residual))
    if sigma <= 0:
        return {}
    z = float(residual[-1] / (2.0 * sigma))
    annualized = math.exp(float(slope) * 252.0) - 1.0
    return {
        "slope_annualized": round(annualized, 4),
        "position": round(z, 3),
        "rising": slope > 0,
        "upper_band_risk": bool(z >= 0.90),
        "lower_band_turn": bool(slope > 0 and z <= -0.55 and work["Close"].iloc[-1] > work["Open"].iloc[-1]),
    }


def most(df, period=9, percent=2.0):
    """Moving average trailing stop with persistent trend switching.

    The stop cannot loosen while its trend is active. This is the essential
    MOST behaviour missing from a one-bar EMA comparison.
    """
    work = _frame(df, period + 3)
    if work is None:
        return {}
    close = work["Close"].astype(float)
    mov = close.ewm(span=period, adjust=False).mean()
    trend = 1
    stop = float(mov.iloc[0]) * (1.0 - percent / 100.0)
    previous_trend = trend
    crossed_up = crossed_down = False
    for i in range(1, len(close)):
        previous_trend = trend
        if trend == 1:
            stop = max(stop, float(mov.iloc[i]) * (1.0 - percent / 100.0))
            if float(close.iloc[i]) < stop:
                trend = -1
                stop = float(mov.iloc[i]) * (1.0 + percent / 100.0)
        else:
            stop = min(stop, float(mov.iloc[i]) * (1.0 + percent / 100.0))
            if float(close.iloc[i]) > stop:
                trend = 1
                stop = float(mov.iloc[i]) * (1.0 - percent / 100.0)
        if i == len(close) - 1:
            crossed_up = previous_trend == -1 and trend == 1
            crossed_down = previous_trend == 1 and trend == -1
    return {
        "trend": "UP" if trend == 1 else "DOWN",
        "level": round(stop, 4),
        "crossed_up": crossed_up,
        "crossed_down": crossed_down,
    }


def demark_sequential(df):
    """Non-repainting TD Setup, perfection, TDST and active Countdown state."""
    work = _frame(df, 20)
    if work is None:
        return {}
    c, h, lo = (work[x].astype(float).to_numpy() for x in ("Close", "High", "Low"))
    buy = sell = 0
    buy_setup_end = sell_setup_end = None
    for i in range(4, len(c)):
        buy = buy + 1 if c[i] < c[i - 4] else 0
        sell = sell + 1 if c[i] > c[i - 4] else 0
        if buy >= 9:
            buy_setup_end = i
        if sell >= 9:
            sell_setup_end = i
    def perfected(end, side):
        if end is None or end < 8:
            return False
        if side == "buy":
            return min(lo[end], lo[end - 1]) <= min(lo[end - 2], lo[end - 3])
        return max(h[end], h[end - 1]) >= max(h[end - 2], h[end - 3])
    active_side = "BUY" if buy_setup_end is not None and (sell_setup_end is None or buy_setup_end > sell_setup_end) else "SELL" if sell_setup_end is not None else None
    end = buy_setup_end if active_side == "BUY" else sell_setup_end
    countdown = 0
    if end is not None:
        for i in range(end + 1, len(c)):
            if active_side == "BUY" and c[i] <= lo[i - 2]:
                countdown += 1
            elif active_side == "SELL" and c[i] >= h[i - 2]:
                countdown += 1
    tdst = None
    if end is not None:
        start = max(0, end - 8)
        tdst = float(max(h[start:end + 1])) if active_side == "BUY" else float(min(lo[start:end + 1]))
    return {
        "buy_setup": min(buy, 9),
        "sell_setup": min(sell, 9),
        "active_side": active_side,
        "perfected": perfected(end, active_side.lower()) if active_side else False,
        "countdown": min(countdown, 13),
        "tdst": round(tdst, 4) if tdst is not None else None,
        "exhaustion_risk": bool(sell >= 8 or (active_side == "SELL" and countdown >= 12)),
    }


def geometric_patterns(df):
    work = _frame(df, 45)
    if work is None:
        return []
    atr = _atr(work)
    if not atr:
        return []
    highs, lows = _pivots(work, radius=2, lookback=45)
    high_line = _line(highs[-4:])
    low_line = _line(lows[-4:])
    if not high_line or not low_line or min(high_line["r2"], low_line["r2"]) < 0.35:
        return []
    price = float(work["Close"].iloc[-1])
    x = len(work) - 1
    upper, lower = _at(high_line, x), _at(low_line, x)
    hs = high_line["slope"] / price
    ls = low_line["slope"] / price
    converging = (upper - lower) < (_at(high_line, x - 15) - _at(low_line, x - 15))
    vr = _volume_ratio(work)
    breakout = price > upper + atr * 0.10 and vr >= 1.10
    patterns = []
    if converging and hs < -0.0002 and ls > 0.0002:
        patterns.append({"name": "SYMMETRIC_TRIANGLE", "bullish": breakout, "confirmed": breakout})
    elif converging and abs(hs) <= 0.0005 and ls > 0.0002:
        patterns.append({"name": "ASCENDING_TRIANGLE", "bullish": True, "confirmed": breakout})
    elif converging and hs < -0.0002 and abs(ls) <= 0.0005:
        patterns.append({"name": "DESCENDING_TRIANGLE", "bullish": False, "confirmed": price < lower})
    if converging and hs > 0 and ls > 0:
        patterns.append({"name": "RISING_WEDGE", "bullish": False, "confirmed": price < lower})
    elif converging and hs < 0 and ls < 0:
        patterns.append({"name": "FALLING_WEDGE", "bullish": True, "confirmed": breakout})

    # Flag/pennant requires a real impulse followed by a short, contained pause.
    impulse_start = float(work["Close"].iloc[-18])
    impulse_end = float(work["Close"].iloc[-9])
    impulse = impulse_end / impulse_start - 1.0
    pause = work.iloc[-9:]
    retrace = (impulse_end - float(pause["Low"].min())) / max(impulse_end - impulse_start, 1e-9)
    pause_range = (float(pause["High"].max()) - float(pause["Low"].min())) / impulse_end
    if impulse >= 0.06 and retrace <= 0.50 and pause_range <= 0.065:
        flag_break = price > float(pause["High"].iloc[:-1].max()) and vr >= 1.10
        name = "BULL_PENNANT" if converging else "BULL_FLAG"
        patterns.append({"name": name, "bullish": True, "confirmed": flag_break})
    return patterns


def volume_price_evidence(df, price):
    """OHLCV institutional-footprint proxies, never an L2/institution identity claim."""
    work = _frame(df, 35)
    if work is None:
        return {}
    h, lo, c, o, v = (work[x].astype(float) for x in ("High", "Low", "Close", "Open", "Volume"))
    spread = (h - lo).replace(0, np.nan)
    close_location = ((c - lo) / spread).clip(0, 1)
    money_flow_multiplier = ((c - lo) - (h - c)) / spread
    money_flow_volume = money_flow_multiplier.fillna(0.0) * v
    volume_sum = float(v.tail(20).sum())
    cmf = float(money_flow_volume.tail(20).sum() / volume_sum) if volume_sum > 0 else 0.0

    direction = np.sign(c.diff().fillna(0.0))
    obv = (direction * v).cumsum()
    adl = money_flow_volume.cumsum()
    obv_slope = float(np.polyfit(np.arange(10), obv.tail(10).to_numpy(), 1)[0])
    adl_slope = float(np.polyfit(np.arange(10), adl.tail(10).to_numpy(), 1)[0])
    avg_volume = float(v.iloc[-21:-1].mean())
    relative_volume = float(v.iloc[-1] / avg_volume) if avg_volume > 0 else 0.0
    avg_spread = float(spread.iloc[-21:-1].mean())
    last_spread = float(spread.iloc[-1]) if pd.notna(spread.iloc[-1]) else 0.0
    last_clv = float(close_location.iloc[-1]) if pd.notna(close_location.iloc[-1]) else 0.5

    demand_candle = bool(relative_volume >= 1.35 and last_clv >= 0.72 and c.iloc[-1] > o.iloc[-1])
    distribution_candle = bool(relative_volume >= 1.35 and last_clv <= 0.30 and c.iloc[-1] < o.iloc[-1])
    absorption = bool(relative_volume >= 1.60 and last_spread <= avg_spread * 0.75 and last_clv >= 0.55)

    # Anchored VWAP begins at the most recent material 30-bar swing low.
    anchor_window = work.tail(30)
    anchor_label = anchor_window["Low"].idxmin()
    anchored = work.loc[anchor_label:]
    typical = (anchored["High"] + anchored["Low"] + anchored["Close"]) / 3.0
    avwap = float((typical * anchored["Volume"]).sum() / anchored["Volume"].sum()) if anchored["Volume"].sum() > 0 else None

    # Bar-based volume profile is an approximation; without trades/ticks it is
    # useful as context but never represented as exchange volume-at-price.
    typical_all = ((h + lo + c) / 3.0).tail(60)
    volume_all = v.tail(60)
    bins = min(24, max(8, int(math.sqrt(len(typical_all)) * 2)))
    hist, edges = np.histogram(typical_all, bins=bins, weights=volume_all)
    poc_i = int(np.argmax(hist))
    poc = float((edges[poc_i] + edges[poc_i + 1]) / 2.0)

    return {
        "data_class": "OHLCV_PROXY_NOT_ORDER_BOOK",
        "cmf20": round(cmf, 4),
        "obv_slope_positive": obv_slope > 0,
        "adl_slope_positive": adl_slope > 0,
        "relative_volume": round(relative_volume, 3),
        "demand_candle": demand_candle,
        "distribution_candle": distribution_candle,
        "absorption": absorption,
        "anchored_vwap": round(avwap, 4) if avwap else None,
        "above_anchored_vwap": bool(avwap and price >= avwap),
        "volume_profile_poc": round(poc, 4),
        "above_poc": price >= poc,
        "accumulation_proxy": bool(cmf >= 0.08 and obv_slope > 0 and adl_slope > 0),
        "distribution_proxy": bool(cmf <= -0.08 and obv_slope < 0 and adl_slope < 0),
    }


def smart_money_zones(df, price):
    """Candle-derived order-block/FVG zones; explicitly not an order book feed."""
    work = _frame(df, 35)
    if work is None:
        return {}
    atr = _atr(work)
    if not atr:
        return {}
    vma = work["Volume"].rolling(20).mean()
    bullish_blocks = []
    fair_value_gaps = []
    start = max(2, len(work) - 25)
    for i in range(start, len(work) - 3):
        candle = work.iloc[i]
        impulse = work.iloc[i + 1:i + 4]
        displacement = float(impulse["Close"].iloc[-1] - candle["Close"])
        impulse_volume = float(impulse["Volume"].mean())
        baseline = float(vma.iloc[i]) if pd.notna(vma.iloc[i]) else 0.0
        if candle["Close"] < candle["Open"] and displacement >= atr * 1.20 and baseline > 0 and impulse_volume >= baseline * 1.15:
            zone_low = float(candle["Low"])
            zone_high = float(candle["Open"])
            later = work.iloc[i + 4:]
            invalidated = bool(not later.empty and float(later["Close"].min()) < zone_low - atr * 0.15)
            mitigated = bool(not later.empty and float(later["Low"].min()) <= zone_high and float(later["Close"].iloc[-1]) > zone_high)
            bullish_blocks.append({
                "low": round(zone_low, 4), "high": round(zone_high, 4),
                "mitigated_and_held": mitigated and not invalidated,
                "invalidated": invalidated,
            })
        if float(work["Low"].iloc[i + 2]) > float(work["High"].iloc[i]):
            gap_low = float(work["High"].iloc[i])
            gap_high = float(work["Low"].iloc[i + 2])
            later_low = float(work["Low"].iloc[i + 3:].min()) if i + 3 < len(work) else gap_high
            fair_value_gaps.append({
                "low": round(gap_low, 4), "high": round(gap_high, 4),
                "open": later_low > gap_low,
            })
    active_block = next((x for x in reversed(bullish_blocks) if not x["invalidated"]), None)
    active_gap = next((x for x in reversed(fair_value_gaps) if x["open"]), None)
    near_block = bool(active_block and active_block["low"] - atr * 0.25 <= price <= active_block["high"] + atr * 0.75)
    return {
        "data_class": "CANDLE_DERIVED_NOT_L2",
        "bullish_order_block": active_block,
        "near_bullish_order_block": near_block,
        "open_bullish_fvg": active_gap,
    }


def structure_events(df):
    """Swing structure, DMI/ADX trend quality and liquidity-sweep evidence."""
    work = _frame(df, 40)
    if work is None:
        return {}
    highs, lows = _pivots(work, radius=2, lookback=55)
    prev_close = work["Close"].shift(1)
    tr = pd.concat([
        work["High"] - work["Low"],
        (work["High"] - prev_close).abs(),
        (work["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    up = work["High"].diff()
    down = -work["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=work.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=work.index)
    atr14 = tr.rolling(14).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(14).mean() / atr14
    minus_di = 100 * minus_dm.rolling(14).mean() / atr14
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = float(dx.rolling(14).mean().iloc[-1]) if pd.notna(dx.rolling(14).mean().iloc[-1]) else None

    last_close = float(work["Close"].iloc[-1])
    prior_high = highs[-2][1] if len(highs) >= 2 else highs[-1][1] if highs else None
    prior_low = lows[-2][1] if len(lows) >= 2 else lows[-1][1] if lows else None
    bullish_bos = bool(prior_high and last_close > prior_high)
    bearish_bos = bool(prior_low and last_close < prior_low)
    higher_highs = len(highs) >= 2 and highs[-1][1] > highs[-2][1]
    higher_lows = len(lows) >= 2 and lows[-1][1] > lows[-2][1]
    bullish_structure = bool(higher_highs and higher_lows)

    recent = work.iloc[-2]
    bullish_sweep = bool(
        prior_low
        and float(recent["Low"]) < prior_low
        and float(recent["Close"]) > prior_low
        and last_close > float(recent["High"])
    )
    bearish_sweep = bool(
        prior_high
        and float(recent["High"]) > prior_high
        and float(recent["Close"]) < prior_high
        and last_close < float(recent["Low"])
    )
    return {
        "adx14": round(adx, 2) if adx is not None else None,
        "plus_di": round(float(plus_di.iloc[-1]), 2) if pd.notna(plus_di.iloc[-1]) else None,
        "minus_di": round(float(minus_di.iloc[-1]), 2) if pd.notna(minus_di.iloc[-1]) else None,
        "bullish_structure": bullish_structure,
        "bullish_break_of_structure": bullish_bos,
        "bearish_break_of_structure": bearish_bos,
        "bullish_liquidity_sweep": bullish_sweep,
        "bearish_liquidity_sweep": bearish_sweep,
        "trend_quality": bool(adx is not None and adx >= 20 and plus_di.iloc[-1] > minus_di.iloc[-1]),
    }


@dataclass
class TechnicalAssessment:
    version: str
    phase: str
    score_adjustment: int
    eligible: bool
    confirmations: list
    warnings: list
    structures: dict

    def to_dict(self):
        return asdict(self)


def analyze_position_structure(d1, d4, d1h, price):
    daily, four, hour = _frame(d1, 60), _frame(d4, 35), _frame(d1h, 35)
    if daily is None or four is None or hour is None or not price:
        return TechnicalAssessment(ENGINE_VERSION, "INSUFFICIENT_DATA", -8, False, [], ["Yapısal veri yetersiz"], {}).to_dict()

    sr = {"1H": support_resistance(hour, price), "4H": support_resistance(four, price), "1D": support_resistance(daily, price)}
    channels = {"1H": regression_channel(hour, 48), "4H": regression_channel(four, 50), "1D": regression_channel(daily, 60)}
    mosts = {"1H": most(hour), "4H": most(four), "1D": most(daily)}
    demark = {"4H": demark_sequential(four), "1D": demark_sequential(daily)}
    patterns = geometric_patterns(four) + geometric_patterns(daily)
    flow = {"4H": volume_price_evidence(four, price), "1D": volume_price_evidence(daily, price)}
    zones = {"4H": smart_money_zones(four, price), "1D": smart_money_zones(daily, price)}
    market_structure = {"1H": structure_events(hour), "4H": structure_events(four), "1D": structure_events(daily)}

    confirmations, warnings = [], []
    score = 0
    for tf in ("1H", "4H", "1D"):
        if sr[tf].get("support_bounce"):
            confirmations.append(f"{tf} pivot desteğinden doğrulanmış dönüş")
            score += 3 if tf != "1H" else 2
    if mosts["4H"].get("trend") == "UP":
        confirmations.append("4H MOST yukarı")
        score += 3
    if mosts["1D"].get("trend") == "UP":
        confirmations.append("1D MOST yukarı")
        score += 3
    if mosts["4H"].get("crossed_up"):
        confirmations.append("4H MOST yeni yukarı dönüş")
        score += 3
    if channels["4H"].get("rising") and channels["4H"].get("position", 2) < 0.85:
        confirmations.append("4H yükselen kanal içinde sağlıklı konum")
        score += 2
    if channels["1D"].get("lower_band_turn"):
        confirmations.append("Günlük yükselen kanal alt bandından dönüş")
        score += 4

    confirmed_patterns = [p for p in patterns if p["bullish"] and p["confirmed"]]
    bearish_patterns = [p for p in patterns if not p["bullish"] and p["confirmed"]]
    for pattern in confirmed_patterns[:2]:
        confirmations.append(pattern["name"] + " hacimli kırılım teyidi")
        score += 4
    for pattern in bearish_patterns[:2]:
        warnings.append(pattern["name"] + " aşağı kırılım riski")
        score -= 6

    if flow["4H"].get("accumulation_proxy") and flow["1D"].get("accumulation_proxy"):
        confirmations.append("4H/1D fiyat-hacim birikim izi")
        score += 4
    elif flow["4H"].get("accumulation_proxy"):
        confirmations.append("4H fiyat-hacim birikim izi")
        score += 2
    if flow["4H"].get("demand_candle"):
        confirmations.append("4H yüksek hacimli talep mumu")
        score += 3
    if flow["4H"].get("absorption"):
        confirmations.append("4H satış emilimi/absorption olasılığı")
        score += 2
    if flow["1D"].get("distribution_proxy") or flow["4H"].get("distribution_candle"):
        warnings.append("Fiyat-hacim dağıtım izi")
        score -= 5
    if flow["4H"].get("above_anchored_vwap") and flow["1D"].get("above_anchored_vwap"):
        confirmations.append("4H/1D anchored VWAP üzerinde")
        score += 2
    if zones["4H"].get("near_bullish_order_block"):
        block = zones["4H"].get("bullish_order_block") or {}
        if block.get("mitigated_and_held"):
            confirmations.append("4H OHLCV order-block mitigation koruması")
            score += 3
    if market_structure["4H"].get("bullish_break_of_structure"):
        confirmations.append("4H yükseliş yönlü yapı kırılımı/BOS")
        score += 3
    if market_structure["4H"].get("bullish_liquidity_sweep"):
        confirmations.append("4H aşağı likidite süpürmesi sonrası geri alım")
        score += 3
    if market_structure["1D"].get("trend_quality"):
        confirmations.append("Günlük DMI/ADX trend kalitesi yeterli")
        score += 2
    if market_structure["4H"].get("bearish_break_of_structure") or market_structure["4H"].get("bearish_liquidity_sweep"):
        warnings.append("4H düşüş yönlü yapı/likidite riski")
        score -= 6

    if demark["1D"].get("exhaustion_risk"):
        warnings.append("Günlük DeMark yükseliş tükenme riski")
        score -= 5
    if demark["4H"].get("exhaustion_risk"):
        warnings.append("4H DeMark yükseliş tükenme riski")
        score -= 3
    if demark["4H"].get("active_side") == "BUY" and demark["4H"].get("perfected"):
        confirmations.append("4H DeMark perfected buy setup")
        score += 3
    if mosts["4H"].get("trend") == "DOWN":
        warnings.append("4H MOST aşağı")
        score -= 5
    if mosts["1D"].get("trend") == "DOWN":
        warnings.append("1D MOST aşağı")
        score -= 6

    upper_risks = sum(bool(channels[tf].get("upper_band_risk")) for tf in ("4H", "1D"))
    if upper_risks:
        warnings.append("Yükselen kanal üst bandında geç giriş riski")
        score -= 3 * upper_risks
    overhead = [sr[tf].get("overhead_atr") for tf in ("1H", "4H", "1D")]
    close_resistance = any(x is not None and 0 < x < 0.55 for x in overhead)
    if close_resistance and not confirmed_patterns:
        warnings.append("Yakın ve teyitli direnç risk/getiri alanını daraltıyor")
        score -= 4

    breakout_now = bool(confirmed_patterns or mosts["4H"].get("crossed_up"))
    bounce_now = any(sr[tf].get("support_bounce") for tf in ("1H", "4H", "1D"))
    if upper_risks >= 2 or (demark["1D"].get("exhaustion_risk") and close_resistance):
        phase = "EXTENDED"
    elif breakout_now or bounce_now:
        phase = "STARTING"
    elif mosts["4H"].get("trend") == "UP" and mosts["1D"].get("trend") == "UP":
        phase = "EARLY_TREND"
    else:
        phase = "UNCONFIRMED"

    eligible = not bearish_patterns and phase != "EXTENDED" and not (
        mosts["4H"].get("trend") == "DOWN" and mosts["1D"].get("trend") == "DOWN"
    )
    score = max(-15, min(12, int(round(score))))
    structures = {
        "support_resistance": sr,
        "channels": channels,
        "most": mosts,
        "demark": demark,
        "patterns": patterns,
        "volume_price": flow,
        "candle_zones": zones,
        "market_structure": market_structure,
        "data_limit": "No L2/order-book or institution identity; OHLCV evidence only",
    }
    return TechnicalAssessment(ENGINE_VERSION, phase, score, eligible, confirmations[:5], warnings[:5], structures).to_dict()
