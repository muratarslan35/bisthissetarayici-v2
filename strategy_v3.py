import math
import os
from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

TR_TZ = ZoneInfo("Europe/Istanbul")

MIN_DAILY_TURNOVER = float(os.getenv("MIN_AVG_DAILY_TURNOVER_TL", "20000000"))
POSITION_RS_MIN = float(os.getenv("POSITION_RS_PERCENTILE_MIN", "0.75"))
INTRADAY_RS_MIN = float(os.getenv("INTRADAY_RS_PERCENTILE_MIN", "0.80"))
IGNITION_RS_MIN = float(os.getenv("IGNITION_RS_PERCENTILE_MIN", "0.90"))

_LAST_SENT = {}


def _now():
    return datetime.now(TR_TZ)


def _num(value, default=None):
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return default


def _frame(item, tf):
    try:
        df = item.get("tf", {}).get(tf, {}).get("df")
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df.copy()
    except Exception:
        pass
    return None


def _closed(df, intraday=False):
    if df is None or len(df) < 3:
        return df

    out = df.copy()

    # The newest intraday/resampled bar may still be forming.
    if intraday:
        return out.iloc[:-1].copy() if len(out) > 3 else out

    try:
        last_date = out.index[-1]
        if getattr(last_date, "tzinfo", None) is not None:
            last_date = last_date.tz_convert(TR_TZ)
        if last_date.date() >= _now().date():
            return out.iloc[:-1].copy()
    except Exception:
        pass

    return out


def _return_n(df, n):
    if df is None or len(df) <= n:
        return None
    a = _num(df["Close"].iloc[-n - 1])
    b = _num(df["Close"].iloc[-1])
    if not a or b is None:
        return None
    return b / a - 1.0


def _true_range(df):
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(df, period=14):
    if df is None or len(df) < period + 2:
        return None
    value = _true_range(df).rolling(period).mean().iloc[-1]
    return _num(value)


def _rsi_wilder(series, period=14):
    if series is None or len(series) < period + 2:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    if len(values) < period + 2:
        return None

    delta = values.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = gains.iloc[1:period + 1].mean()
    avg_loss = losses.iloc[1:period + 1].mean()

    if math.isnan(avg_gain) or math.isnan(avg_loss):
        return None

    for i in range(period + 1, len(values)):
        avg_gain = ((avg_gain * (period - 1)) + gains.iloc[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses.iloc[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _turnover_20d(df):
    if df is None or len(df) < 20:
        return 0.0
    t = (df["Close"] * df["Volume"]).tail(20).mean()
    return _num(t, 0.0) or 0.0


def _slope(series, bars=5):
    if series is None or len(series) < bars + 1:
        return 0.0
    start = _num(series.iloc[-bars - 1])
    end = _num(series.iloc[-1])
    if not start or end is None:
        return 0.0
    return end / start - 1.0


def _local_intraday(df):
    if df is None or df.empty:
        return None
    out = df.copy()
    try:
        if out.index.tz is None:
            out.index = pd.to_datetime(out.index, utc=True)
        out.index = out.index.tz_convert(TR_TZ)
    except Exception:
        return None
    return out


def session_vwap(df):
    local = _local_intraday(df)
    if local is None or local.empty:
        return None

    today = _now().date()
    current = local[local.index.date == today]
    if current.empty:
        # Yahoo may be delayed or market may be closed. Use latest available session.
        last_date = local.index[-1].date()
        current = local[local.index.date == last_date]

    if current.empty:
        return None

    typical = (current["High"] + current["Low"] + current["Close"]) / 3.0
    vol = current["Volume"].astype(float)
    den = vol.sum()
    if den <= 0:
        return None
    return _num((typical * vol).sum() / den)


def session_rvol(df, lookback_sessions=5):
    """
    Relative cumulative session volume using the same number of completed
    15-minute bars from prior sessions. No synthetic API-polling ticks.
    """
    local = _local_intraday(df)
    if local is None or len(local) < 20:
        return 0.0

    sessions = []
    for _, g in local.groupby(local.index.date):
        g = g.sort_index()
        if not g.empty:
            sessions.append(g)

    if len(sessions) < 2:
        return 0.0

    current = sessions[-1]
    if len(current) > 1:
        current = current.iloc[:-1]
    if current.empty:
        return 0.0

    bars = len(current)
    current_volume = _num(current["Volume"].sum(), 0.0) or 0.0
    comparable = []

    for g in sessions[-(lookback_sessions + 1):-1]:
        if len(g) >= bars:
            comparable.append(_num(g.iloc[:bars]["Volume"].sum(), 0.0) or 0.0)

    comparable = [v for v in comparable if v > 0]
    if not comparable:
        return 0.0

    base = median(comparable)
    if base <= 0:
        return 0.0

    return round(current_volume / base, 3)


def _compression_ratio(df):
    closed = _closed(df, intraday=True)
    if closed is None or len(closed) < 25:
        return None

    tr = _true_range(closed)
    short = _num(tr.tail(6).mean())
    long = _num(tr.tail(24).mean())

    if short is None or not long:
        return None
    return short / long


def _momentum_acceleration(df):
    closed = _closed(df, intraday=True)
    if closed is None or len(closed) < 6:
        return (0.0, 0.0)

    c = closed["Close"].astype(float)
    r1 = _num(c.iloc[-1] / c.iloc[-2] - 1.0, 0.0)
    r2 = _num(c.iloc[-2] / c.iloc[-3] - 1.0, 0.0)
    r3 = _num(c.iloc[-3] / c.iloc[-4] - 1.0, 0.0)

    velocity = r1 * 0.55 + r2 * 0.30 + r3 * 0.15
    acceleration = (r1 - r2) * 0.65 + (r2 - r3) * 0.35
    return velocity, acceleration


def _breakout_level(df, bars=20):
    closed = _closed(df, intraday=True)
    if closed is None or len(closed) < bars + 2:
        return None
    return _num(closed["High"].iloc[-bars:].max())


def _daily_breakout_level(df, bars=20):
    closed = _closed(df, intraday=False)
    if closed is None or len(closed) < bars + 2:
        return None
    # Exclude the latest confirmed close from the resistance window.
    return _num(closed["High"].iloc[-bars - 1:-1].max())


def _rank_percentiles(values):
    clean = [(k, v) for k, v in values.items() if v is not None and math.isfinite(v)]
    clean.sort(key=lambda x: x[1])
    n = len(clean)
    if n <= 1:
        return {k: 0.5 for k, _ in clean}
    return {k: i / (n - 1) for i, (k, _) in enumerate(clean)}


def build_market_context(market_data):
    """
    Build one cross-sectional context per market snapshot.
    All strategies reuse it; no network request is made here.
    """
    intraday_returns = {}
    position_returns = {}
    breadth_15 = []
    breadth_daily = []

    for item in market_data:
        symbol = item.get("symbol")
        if not symbol:
            continue

        d15 = _closed(_frame(item, "15m"), intraday=True)
        d1 = _closed(_frame(item, "1d"), intraday=False)

        intraday_returns[symbol] = _return_n(d15, 4) if d15 is not None else None
        position_returns[symbol] = _return_n(d1, 20) if d1 is not None else None

        try:
            tf15 = item["tf"]["15m"]
            breadth_15.append(
                1.0 if tf15["ema20"] > tf15["ema50"] else 0.0
            )
        except Exception:
            pass

        if d1 is not None and len(d1) >= 55:
            ema50 = d1["Close"].ewm(span=50, adjust=False).mean()
            breadth_daily.append(1.0 if d1["Close"].iloc[-1] > ema50.iloc[-1] else 0.0)

    intraday_valid = [v for v in intraday_returns.values() if v is not None]
    position_valid = [v for v in position_returns.values() if v is not None]

    intraday_median = median(intraday_valid) if intraday_valid else 0.0
    position_median = median(position_valid) if position_valid else 0.0

    intraday_rs = {
        k: (v - intraday_median) if v is not None else None
        for k, v in intraday_returns.items()
    }
    position_rs = {
        k: (v - position_median) if v is not None else None
        for k, v in position_returns.items()
    }

    intraday_rank = _rank_percentiles(intraday_rs)
    position_rank = _rank_percentiles(position_rs)

    b15 = sum(breadth_15) / len(breadth_15) if breadth_15 else 0.5
    bd = sum(breadth_daily) / len(breadth_daily) if breadth_daily else 0.5

    regime_score = 0
    if b15 >= 0.60:
        regime_score += 1
    elif b15 <= 0.35:
        regime_score -= 1

    if bd >= 0.60:
        regime_score += 1
    elif bd <= 0.35:
        regime_score -= 1

    if intraday_median > 0.002:
        regime_score += 1
    elif intraday_median < -0.004:
        regime_score -= 1

    if regime_score >= 2:
        regime = "RISK_ON"
    elif regime_score <= -2:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"

    return {
        "regime": regime,
        "regime_score": regime_score,
        "breadth_intraday": round(b15, 3),
        "breadth_daily": round(bd, 3),
        "intraday_market_return": intraday_median,
        "position_market_return": position_median,
        "intraday_rs": intraday_rs,
        "position_rs": position_rs,
        "intraday_rank": intraday_rank,
        "position_rank": position_rank,
        "universe_size": len(market_data),
    }


def _fresh_kap(symbol, kap_cache, max_minutes=20):
    if not kap_cache:
        return None
    kap = kap_cache.get(symbol)
    if not kap:
        return None

    ts = kap.get("time")
    if not ts:
        return kap

    try:
        now = datetime.now(ts.tzinfo) if getattr(ts, "tzinfo", None) else datetime.now()
        age = (now - ts).total_seconds() / 60.0
        if age <= max_minutes:
            return kap
    except Exception:
        return kap

    return None


def _cooldown_ok(symbol, algo, minutes):
    key = (symbol, algo)
    now = _now()
    last = _LAST_SENT.get(key)
    if last and (now - last).total_seconds() < minutes * 60:
        return False
    return True


def _mark_sent(symbol, algo):
    _LAST_SENT[(symbol, algo)] = _now()


def _risk_levels(entry, atr_value, structural=None, position=False):
    if not entry or not atr_value or atr_value <= 0:
        return None

    atr_mult = 1.5 if position else 1.0
    fallback_stop = entry - atr_value * atr_mult

    candidates = [fallback_stop]
    if structural is not None and structural < entry:
        candidates.append(structural)

    # Prefer the tighter valid structural invalidation, but never below 4.5% risk.
    stop = max(candidates)
    max_risk = entry * (0.045 if position else 0.025)
    if entry - stop > max_risk:
        stop = entry - max_risk

    min_risk = entry * (0.006 if not position else 0.012)
    risk = max(entry - stop, min_risk)
    stop = entry - risk

    if position:
        return {
            "stop_loss": round(stop, 2),
            "tp1": round(entry + risk * 1.5, 2),
            "tp2": round(entry + risk * 3.0, 2),
            "tp3": round(entry + risk * 5.0, 2),
            "risk_pct": round(risk / entry * 100, 2),
        }

    return {
        "stop_loss": round(stop, 2),
        "tp1": round(entry + risk * 1.2, 2),
        "tp2": round(entry + risk * 2.5, 2),
        "tp3": round(entry + risk * 4.0, 2),
        "risk_pct": round(risk / entry * 100, 2),
    }


def _base_signal(item, scope, algo, score, reasons, ctx, rs_pct, risk):
    price = _num(item.get("current_price"))
    if price is None:
        return None

    d15 = _closed(_frame(item, "15m"), intraday=True)
    d4 = _closed(_frame(item, "4h"), intraday=True)
    d1 = _closed(_frame(item, "1d"), intraday=False)

    rsi_15m = _rsi_wilder(d15["Close"], 14) if d15 is not None else None
    rsi_4h = _rsi_wilder(d4["Close"], 14) if d4 is not None else None
    rsi_1d = _rsi_wilder(d1["Close"], 14) if d1 is not None else None

    return {
        "symbol": item.get("symbol"),
        "signal_scope": scope,
        "main_algorithm": algo,
        "entry_price": round(price, 2),
        "price": round(price, 2),
        "score": int(round(score)),
        "quality": "A+" if score >= 85 else "A" if score >= 75 else "B",
        "category": "strong",
        "action": "POZİSYON" if scope == "POSITION" else "INTRADAY",
        "title": algo.replace("_", " "),
        "reasons": reasons,
        "market_regime": ctx.get("regime"),
        "market_breadth": ctx.get("breadth_intraday"),
        "relative_strength_percentile": round((rs_pct or 0.0) * 100, 1),
        "rsi_15m": rsi_15m,
        "rsi_4h": rsi_4h,
        "rsi_1d": rsi_1d,
        "data_confidence": item.get("data_confidence"),
        "data_age_minutes": item.get("data_age_minutes"),
        "data_source": item.get("data_source"),
        "time": _now().strftime("%H:%M:%S"),
        **risk,
    }


def evaluate_position_signals(item, ctx, kap_cache=None):
    symbol = item.get("symbol")
    price = _num(item.get("current_price"))
    if _num(item.get("data_confidence"), 100.0) < 70:
        return []
    d1 = _closed(_frame(item, "1d"), intraday=False)
    d4 = _closed(_frame(item, "4h"), intraday=True)
    d1h = _closed(_frame(item, "1h"), intraday=True)

    if not symbol or not price or d1 is None or len(d1) < 220:
        return []

    if _turnover_20d(d1) < MIN_DAILY_TURNOVER:
        return []

    close = d1["Close"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    atr_d = _atr(d1)
    if not atr_d:
        return []

    rs_pct = ctx.get("position_rank", {}).get(symbol, 0.0)
    if rs_pct < POSITION_RS_MIN:
        return []

    trend_up = (
        close.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1]
        and _slope(ema50, 10) > 0
    )

    four_hour_up = False
    if d4 is not None and len(d4) >= 25:
        c4 = d4["Close"].astype(float)
        e20_4 = c4.ewm(span=20, adjust=False).mean()
        e50_4 = c4.ewm(span=50, adjust=False).mean()
        four_hour_up = e20_4.iloc[-1] > e50_4.iloc[-1]

    one_hour_turn = False
    if d1h is not None and len(d1h) >= 22:
        c1 = d1h["Close"].astype(float)
        e20_1 = c1.ewm(span=20, adjust=False).mean()
        one_hour_turn = c1.iloc[-1] >= e20_1.iloc[-1] and c1.iloc[-1] > c1.iloc[-2]

    volume_ratio = 0.0
    if len(d1) >= 21:
        base_vol = _num(d1["Volume"].iloc[-21:-1].mean(), 0.0) or 0.0
        if base_vol > 0:
            volume_ratio = (_num(d1["Volume"].iloc[-1], 0.0) or 0.0) / base_vol

    candidates = []

    # KOMBINE V3: controlled pullback in an established position trend.
    pullback_distance = abs(price - _num(ema20.iloc[-1], price)) / price
    if trend_up and four_hour_up and one_hour_turn and pullback_distance <= 0.035:
        score = 58 + rs_pct * 20
        reasons = [
            "1D EMA50 > EMA200 ve EMA50 eğimi yukarı",
            "4H ana trend yukarı",
            "1 saatlik fiyat ivmesi yeniden yukarı",
            f"20 günlük göreceli güç yüzdelik: %{round(rs_pct * 100)}",
        ]
        if volume_ratio >= 1.15:
            score += 7
            reasons.append(f"Günlük hacim genişlemesi {volume_ratio:.2f}x")
        if ctx.get("regime") == "RISK_ON":
            score += 7
            reasons.append("Piyasa genel görünümü pozitif")
        elif ctx.get("regime") == "RISK_OFF":
            score -= 8

        structural = _num(d1["Low"].tail(10).min())
        risk = _risk_levels(price, atr_d, structural, position=True)
        if risk and score >= 72 and _cooldown_ok(symbol, "KOMBINE_V3", 24 * 60):
            sig = _base_signal(
                item, "POSITION", "KOMBINE_V3", score, reasons, ctx, rs_pct, risk
            )
            if sig:
                sig["holding_horizon"] = "2-10 işlem günü"
                candidates.append(sig)

    # SUPER KOMBINE V3: daily structural breakout + relative-strength acceleration.
    breakout = _daily_breakout_level(d1, 20)
    if trend_up and four_hour_up and breakout and price >= breakout:
        extension_atr = (price - breakout) / atr_d if atr_d else 99.0
        if extension_atr <= 1.5:
            score = 66 + rs_pct * 20
            reasons = [
                "20 günlük yapısal direnç kırılımı",
                "Günlük ve 4 saatlik ana eğilim uyumlu",
                f"20 günlük göreceli güç yüzdelik: %{round(rs_pct * 100)}",
                f"Kırılım sonrası uzaklık {extension_atr:.2f} ATR",
            ]
            if volume_ratio >= 1.25:
                score += 8
                reasons.append(f"Günlük hacim teyidi {volume_ratio:.2f}x")
            if ctx.get("regime") == "RISK_ON":
                score += 6
            elif ctx.get("regime") == "RISK_OFF":
                score -= 10

            risk = _risk_levels(price, atr_d, breakout - atr_d * 0.35, position=True)
            if risk and score >= 76 and _cooldown_ok(symbol, "SUPER_KOMBINE_V3", 24 * 60):
                sig = _base_signal(
                    item, "POSITION", "SUPER_KOMBINE_V3", score, reasons, ctx, rs_pct, risk
                )
                if sig:
                    sig["holding_horizon"] = "2-10 işlem günü"
                    candidates.append(sig)

    # Position engine may use a verified KAP released after the previous
    # close; keep it eligible into the next session.
    kap = _fresh_kap(symbol, kap_cache, max_minutes=1080)
    if kap and kap.get("verified") and trend_up and four_hour_up and rs_pct >= 0.80:
        score = 72 + rs_pct * 16 + min(8, max(0, _num(kap.get("score"), 0.0) or 0.0))
        reasons = [
            "Taze KAP olayı",
            "Günlük ve 4 saatlik ana eğilim KAP hareketini destekliyor",
            f"20 günlük göreceli güç yüzdelik: %{round(rs_pct * 100)}",
        ]
        risk = _risk_levels(price, atr_d, _num(d1["Low"].tail(8).min()), position=True)
        if risk and score >= 78 and _cooldown_ok(symbol, "KAP_POSITION_V3", 24 * 60):
            sig = _base_signal(
                item, "POSITION", "KAP_POSITION_V3", score, reasons, ctx, rs_pct, risk
            )
            if sig:
                sig["holding_horizon"] = "2-10 işlem günü"
                sig["event_title"] = kap.get("title")
                candidates.append(sig)

    if not candidates:
        return []

    # Only the best position setup is sent to the bot for this symbol/snapshot.
    best = max(candidates, key=lambda x: x["score"])
    _mark_sent(symbol, best["main_algorithm"])
    return [best]


def evaluate_intraday_signals(item, ctx, kap_cache=None):
    symbol = item.get("symbol")
    price = _num(item.get("current_price"))
    if _num(item.get("data_confidence"), 100.0) < 80:
        return []
    d15 = _frame(item, "15m")
    d1 = _closed(_frame(item, "1d"), intraday=False)

    if not symbol or not price or d15 is None or len(d15) < 80:
        return []

    now = _now()
    if now.hour < 10 or now.hour >= 17:
        return []

    if d1 is not None and _turnover_20d(d1) < MIN_DAILY_TURNOVER:
        return []

    closed15 = _closed(d15, intraday=True)
    if closed15 is None or len(closed15) < 40:
        return []

    close15 = closed15["Close"].astype(float)
    ema20 = close15.ewm(span=20, adjust=False).mean()
    ema50 = close15.ewm(span=50, adjust=False).mean()

    atr15 = _atr(closed15)
    if not atr15:
        return []

    vwap = session_vwap(d15)
    srvol = session_rvol(d15)
    velocity, acceleration = _momentum_acceleration(d15)
    compression = _compression_ratio(d15)
    breakout = _breakout_level(d15, 20)
    rs_pct = ctx.get("intraday_rank", {}).get(symbol, 0.0)

    if vwap is None or breakout is None:
        return []

    trend_up = ema20.iloc[-1] > ema50.iloc[-1]
    above_vwap = price >= vwap
    breakout_distance = (price - breakout) / breakout
    extension_atr = max(0.0, price - breakout) / atr15

    candidates = []

    # 1) MOMENTUM IGNITION: compression -> acceleration -> near/through breakout.
    ignition_conditions = (
        rs_pct >= IGNITION_RS_MIN
        and trend_up
        and above_vwap
        and velocity > 0
        and acceleration > 0
        and compression is not None
        and compression <= 0.95
        and breakout_distance >= -0.006
        and extension_atr <= 1.5
        and srvol >= 0.9
    )

    if ignition_conditions:
        score = 55
        score += min(18, rs_pct * 18)
        score += min(10, max(0.0, acceleration * 10000))
        score += 8 if srvol >= 1.2 else 4
        score += 6 if ctx.get("regime") == "RISK_ON" else 0
        score -= 8 if ctx.get("regime") == "RISK_OFF" else 0

        reasons = [
            "Volatilite sıkışması sonrası fiyat ivmesi artıyor",
            f"Intraday göreceli güç yüzdelik: %{round(rs_pct * 100)}",
            f"Seans göreli hacmi: {srvol:.2f}x",
            f"Yapısal kırılıma mesafe: %{breakout_distance * 100:.2f}",
            "Fiyat seans ortalama maliyetinin (VWAP) üzerinde",
        ]

        risk = _risk_levels(price, atr15, max(vwap, breakout - atr15 * 0.5), position=False)
        if risk and score >= 75 and _cooldown_ok(symbol, "MOMENTUM_IGNITION_V3", 90):
            sig = _base_signal(
                item, "INTRADAY", "MOMENTUM_IGNITION_V3", score, reasons, ctx, rs_pct, risk
            )
            if sig:
                sig["session_rvol"] = srvol
                sig["session_vwap"] = round(vwap, 2)
                sig["momentum_velocity"] = round(velocity * 100, 3)
                sig["momentum_acceleration"] = round(acceleration * 100, 3)
                sig["valid_until"] = "17:30"
                candidates.append(sig)

    # 2) INTRADAY CONTINUATION: confirmed strength, VWAP and structure.
    continuation_conditions = (
        rs_pct >= INTRADAY_RS_MIN
        and trend_up
        and above_vwap
        and srvol >= 1.05
        and velocity > 0
        and breakout_distance >= -0.004
        and extension_atr <= 2.0
    )

    if continuation_conditions:
        score = 54 + rs_pct * 18
        if price >= breakout:
            score += 9
        if srvol >= 1.35:
            score += 8
        if ctx.get("regime") == "RISK_ON":
            score += 6
        elif ctx.get("regime") == "RISK_OFF":
            score -= 10

        reasons = [
            "15m EMA20 > EMA50",
            "Fiyat seans ortalama maliyetinin (VWAP) üzerinde",
            f"Intraday göreceli güç yüzdelik: %{round(rs_pct * 100)}",
            f"Seans göreli hacmi: {srvol:.2f}x",
            "Yakın yapısal kırılım ve devam hareketi",
        ]

        risk = _risk_levels(price, atr15, max(vwap, breakout - atr15 * 0.65), position=False)
        if risk and score >= 74 and _cooldown_ok(symbol, "INTRADAY_MOMENTUM_V3", 120):
            sig = _base_signal(
                item, "INTRADAY", "INTRADAY_MOMENTUM_V3", score, reasons, ctx, rs_pct, risk
            )
            if sig:
                sig["session_rvol"] = srvol
                sig["session_vwap"] = round(vwap, 2)
                sig["valid_until"] = "17:30"
                candidates.append(sig)

    # 3) KAP EVENT MOMENTUM: event and technical confirmation are measured separately.
    # Intraday KAP momentum must be genuinely fresh and verified.
    kap = _fresh_kap(symbol, kap_cache, max_minutes=30)
    if kap and kap.get("verified") and trend_up and above_vwap and rs_pct >= 0.75 and velocity > 0:
        kap_score = _num(kap.get("score"), 0.0) or 0.0
        score = 62 + rs_pct * 16 + min(10, max(0.0, kap_score))
        if srvol >= 1.10:
            score += 6
        if price >= breakout:
            score += 6
        if ctx.get("regime") == "RISK_OFF":
            score -= 8

        reasons = [
            "Taze KAP olayı + teknik teyit",
            f"Intraday göreceli güç yüzdelik: %{round(rs_pct * 100)}",
            "Fiyat seans ortalama maliyetinin (VWAP) üzerinde",
            f"Seans göreli hacmi: {srvol:.2f}x",
        ]

        risk = _risk_levels(price, atr15, max(vwap, breakout - atr15 * 0.6), position=False)
        if risk and score >= 76 and _cooldown_ok(symbol, "KAP_EVENT_INTRADAY_V3", 120):
            sig = _base_signal(
                item, "INTRADAY", "KAP_EVENT_INTRADAY_V3", score, reasons, ctx, rs_pct, risk
            )
            if sig:
                sig["event_title"] = kap.get("title")
                sig["event_link"] = kap.get("link")
                sig["session_rvol"] = srvol
                sig["session_vwap"] = round(vwap, 2)
                sig["valid_until"] = "17:30"
                candidates.append(sig)

    if not candidates:
        return []

    # Different intraday families may qualify, but avoid channel spam:
    # publish only the strongest current setup for this symbol.
    best = max(candidates, key=lambda x: x["score"])
    _mark_sent(symbol, best["main_algorithm"])
    return [best]



def evaluate_fast_entry_signal(item, ctx, kap_cache=None):
    """
    Event-driven early-entry evaluator used by the fast watchlist lane.

    The full market snapshot remains the structural source of truth (daily/15m
    trend, ATR, VWAP, breakout, cross-sectional relative strength). Only the
    current price/short-horizon acceleration comes from the fast quote overlay.
    This prevents a sub-minute quote from bypassing liquidity/risk controls.
    """
    symbol = item.get("symbol")
    price = _num(item.get("current_price"))
    quote = item.get("fast_quote") or {}
    fast_age = _num(item.get("fast_age_seconds"), 999.0)

    if not symbol or not price or fast_age is None or fast_age > 45:
        return []

    now = _now()
    if now.hour < 10 or now.hour >= 17:
        return []

    d15 = _frame(item, "15m")
    d1 = _closed(_frame(item, "1d"), intraday=False)
    if d15 is None or len(d15) < 40 or d1 is None or len(d1) < 60:
        return []

    turnover = _turnover_20d(d1)
    metrics = item.get("universe_metrics") or {}
    slow_burn = bool(metrics.get("slow_burn"))
    min_turnover = MIN_DAILY_TURNOVER if not slow_burn else max(
        8_000_000.0,
        MIN_DAILY_TURNOVER * 0.40,
    )
    if turnover < min_turnover:
        return []

    rs_pct = ctx.get("intraday_rank", {}).get(symbol, 0.0)
    fast_rvol = _num(quote.get("rvol"), 0.0) or 0.0
    ch15 = _num(quote.get("change_15s_pct"), 0.0) or 0.0
    ch30 = _num(quote.get("change_30s_pct"), 0.0) or 0.0
    ch60 = _num(quote.get("change_60s_pct"), 0.0) or 0.0
    day_change = _num(quote.get("day_change_pct"), 0.0) or 0.0

    # Do not chase a stock already pinned close to the daily upper limit.
    if day_change >= 9.7:
        return []

    close1 = d1["Close"].astype(float)
    ema20d = close1.ewm(span=20, adjust=False).mean()
    ema50d = close1.ewm(span=50, adjust=False).mean()
    daily_constructive = (
        close1.iloc[-1] >= ema20d.iloc[-1]
        and ema20d.iloc[-1] >= ema50d.iloc[-1] * 0.995
    )
    if not daily_constructive:
        return []

    vwap = session_vwap(d15)
    atr15 = _atr(_closed(d15, intraday=True))
    breakout = _breakout_level(d15, 20)
    if not vwap or not atr15 or not breakout:
        return []

    kap = _fresh_kap(symbol, kap_cache, max_minutes=30)
    kap_verified = bool(kap and kap.get("verified"))

    standard_mover = (
        rs_pct >= 0.78
        and fast_rvol >= 1.20
        and (ch60 >= 0.22 or ch30 >= 0.16 or ch15 >= 0.10)
    )
    slow_burn_mover = (
        slow_burn
        and rs_pct >= 0.70
        and fast_rvol >= 0.90
        and ch60 >= 0.12
    )
    kap_mover = (
        kap_verified
        and rs_pct >= 0.65
        and fast_rvol >= 0.90
        and ch60 >= 0.05
    )

    if not (standard_mover or slow_burn_mover or kap_mover):
        return []

    # "Early" means close to the structural trigger, not several ATRs after it.
    breakout_gap_pct = (price / breakout - 1.0) * 100.0
    extension_atr = max(0.0, price - breakout) / atr15
    near_trigger = breakout_gap_pct >= -0.45 and extension_atr <= 0.90
    if not near_trigger:
        return []

    if price < vwap and not kap_mover:
        return []

    score = 56.0 + rs_pct * 16.0
    score += min(12.0, max(0.0, (fast_rvol - 0.85) * 12.0))
    score += min(12.0, max(0.0, ch60 * 12.0))
    score += min(6.0, max(0.0, day_change * 1.2))

    reasons = [
        "Hızlı izleme listesinde erken fiyat ivmesi",
        f"60 sn fiyat ivmesi %{round(ch60, 2)}",
        f"Anlık RVOL {round(fast_rvol, 2)}x",
        f"Intraday göreceli güç yüzdelik: %{round(rs_pct * 100)}",
        f"Yapısal kırılıma mesafe %{round(breakout_gap_pct, 2)}",
        "Günlük trend yapısı pozitif",
    ]

    if slow_burn:
        score += 4.0
        reasons.append("3-5 günlük istikrarlı slow-burn aday havuzu")

    if kap_verified:
        score += min(10.0, max(4.0, _num(kap.get("score"), 0.0) or 0.0))
        reasons.insert(0, "Doğrulanmış taze KAP + erken fiyat teyidi")

    if ctx.get("regime") == "RISK_ON":
        score += 5.0
    elif ctx.get("regime") == "RISK_OFF":
        score -= 7.0

    algorithm = "KAP_EARLY_IGNITION_V3" if kap_verified else "EARLY_IGNITION_V3"
    threshold = 76 if kap_verified else 78

    structural = max(vwap, breakout - atr15 * 0.70)
    risk = _risk_levels(price, atr15, structural, position=False)
    if not risk or score < threshold or not _cooldown_ok(symbol, algorithm, 120):
        return []

    sig = _base_signal(
        item,
        "INTRADAY",
        algorithm,
        score,
        reasons,
        ctx,
        rs_pct,
        risk,
    )
    if not sig:
        return []

    sig["session_vwap"] = round(vwap, 2)
    sig["session_rvol"] = round(fast_rvol, 2)
    sig["fast_change_15s_pct"] = round(ch15, 3)
    sig["fast_change_30s_pct"] = round(ch30, 3)
    sig["fast_change_60s_pct"] = round(ch60, 3)
    sig["fast_source"] = item.get("fast_source")
    sig["valid_until"] = "17:30"
    if kap_verified:
        sig["event_title"] = kap.get("title")
        sig["event_link"] = kap.get("link")

    _mark_sent(symbol, algorithm)
    return [sig]


def _algorithm_tr(algo):
    labels = {
        "KOMBINE_V3": "Kombine Trend Dönüşü",
        "SUPER_KOMBINE_V3": "Güçlü Trend Kırılımı",
        "KAP_POSITION_V3": "KAP Destekli Pozisyon",
        "MOMENTUM_IGNITION_V3": "Erken İvme Başlangıcı",
        "INTRADAY_MOMENTUM_V3": "Gün İçi İvme Devamı",
        "KAP_EVENT_INTRADAY_V3": "KAP Destekli Gün İçi İvme",
        "EARLY_IGNITION_V3": "Erken Hareket Başlangıcı",
        "KAP_EARLY_IGNITION_V3": "KAP Destekli Erken Hareket",
    }
    return labels.get(str(algo or ""), str(algo or "Bilinmeyen strateji").replace("_", " ").title())


def _market_regime_tr(regime):
    return {
        "RISK_ON": "Pozitif — risk iştahı yüksek",
        "RISK_OFF": "Temkinli — risk iştahı düşük",
        "NEUTRAL": "Dengeli / nötr",
    }.get(str(regime or ""), str(regime or "Bilinmiyor"))


def _signal_strength_tr(score):
    score = _num(score, 0.0) or 0.0
    if score >= 90:
        return "🔥 ÇOK GÜÇLÜ"
    if score >= 85:
        return "💪 GÜÇLÜ"
    if score >= 80:
        return "✅ YÜKSEK"
    return "🟡 SEÇİCİ"


def _rsi_story(signal):
    r15 = _num(signal.get("rsi_15m"))
    r4 = _num(signal.get("rsi_4h"))
    r1 = _num(signal.get("rsi_1d"))

    if r15 is not None and r4 is not None:
        if r15 >= 75 and r4 >= 70:
            return "Kısa ve orta vadeli fiyat gücü ileri aşamada; şişkinlik riski yükselmiş."
        if r15 >= 60 and 52 <= r4 < 70:
            return "Kısa vadeli fiyat gücü yüksek, 4 saatlik eğilim de destekliyor; hareket olgunlaşıyor."
        if r15 < 60 and 50 <= r4 < 65:
            return "Fiyat gücü yeni artıyor; 4 saatlik yapı henüz aşırı bölgeye taşınmamış."
        if r4 < 50:
            return "Kısa vadeli hareket olsa da 4 saatlik ana güç henüz tam teyit vermiyor."

    if r4 is not None and r1 is not None:
        if r4 >= 75 and r1 >= 70:
            return "4 saatlik ve günlük fiyat gücü yüksek; ana eğilim güçlü ancak geç kalma/şişkinlik riski artmış."
        if 52 <= r4 < 70 and 50 <= r1 < 68:
            return "4 saatlik ve günlük RSI uyumlu; ana eğilim güçlü fakat aşırı bölgeye taşınmamış."
        if r4 < 50 <= r1:
            return "Günlük yapı korunuyor ancak 4 saatlik fiyat gücü yeniden artış aşamasında."

    return "RSI görünümü tek başına karar üretmez; hacim, ana eğilim, kırılım ve göreceli güç ile birlikte değerlendiriliyor."


def format_v3_signal_message(signal):
    scope = signal.get("signal_scope")
    score = signal.get("score")
    strength = _signal_strength_tr(score)
    algo_tr = _algorithm_tr(signal.get("main_algorithm"))
    regime_tr = _market_regime_tr(signal.get("market_regime"))
    symbol = str(signal.get("symbol") or "").replace(".IS", "")

    if scope == "POSITION":
        header = "📌 <b>POZİSYON / SWING SİNYALİ</b>"
        meaning = "2–10 işlem günlük ana eğilim fırsatı; kısa sıçramadan çok kalıcı güç ve yapısal devam aranıyor."
    else:
        header = "⚡ <b>GÜN İÇİ ERKEN HAREKET SİNYALİ</b>"
        meaning = "Hacim ve fiyat gücü yeni artarken, hareket aşırı uzamadan erken yakalama amacı taşıyor."

    lines = [
        header,
        f"📊 <b>{symbol}</b>",
        f"🔥 <b>SİNYAL GÜCÜ: {strength}</b>",
        f"⭐ Güç puanı: <b>{score}/100</b>",
        f"🧠 Strateji: <b>{algo_tr}</b>",
        "",
        "💡 <b>Bu bildirim ne anlatıyor?</b>",
        meaning,
        _rsi_story(signal),
        "",
        "📍 <b>Fiyat ve risk planı</b>",
        f"• İzleme / giriş bölgesi: <b>{signal.get('entry_price')}</b>",
        f"• Koruyucu stop: <b>{signal.get('stop_loss')}</b>",
        f"• 1. hedef: <b>{signal.get('tp1')}</b>",
        f"• 2. hedef: <b>{signal.get('tp2')}</b>",
        f"• Ana hedef / iz süren stop: <b>{signal.get('tp3')}</b>",
        f"• Başlangıç fiyat riski: <b>%{signal.get('risk_pct')}</b>",
        "",
        "📊 <b>Teknik görünüm</b>",
        f"• Piyasa rejimi: {regime_tr}",
        f"• BIST içi göreceli güç: %{signal.get('relative_strength_percentile')}",
    ]

    if signal.get("session_rvol") is not None:
        lines.append(f"• Seans göreli hacmi: {signal.get('session_rvol')}x")
    if signal.get("session_vwap") is not None:
        lines.append(f"• Seans ortalama maliyeti (VWAP): {signal.get('session_vwap')}")

    if signal.get("rsi_15m") is not None:
        lines.append(f"• RSI(14) — 15 dakika: {signal.get('rsi_15m')}")
    if signal.get("rsi_4h") is not None:
        lines.append(f"• RSI(14) — 4 saat: {signal.get('rsi_4h')}")
    if signal.get("rsi_1d") is not None and scope == "POSITION":
        lines.append(f"• RSI(14) — günlük: {signal.get('rsi_1d')}")

    if signal.get("fast_change_60s_pct") is not None:
        lines.append(f"• Son 60 saniye fiyat ivmesi: %{signal.get('fast_change_60s_pct')}")
    if signal.get("holding_horizon"):
        lines.append(f"• Beklenen takip ufku: {signal.get('holding_horizon')}")
    if signal.get("valid_until"):
        lines.append(f"• Gün içi geçerlilik: bugün {signal.get('valid_until')}'a kadar")
    if signal.get("event_title"):
        lines.append(f"• KAP desteği: {signal.get('event_title')}")

    reasons = signal.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append("🔎 <b>Sinyali güçlendiren nedenler</b>")
        for reason in reasons[:6]:
            lines.append(f"• {reason}")

    lines.extend([
        "",
        "⚠️ <b>Not:</b> Bu bildirim algoritmik piyasa taramasıdır. RSI tek başına sinyal üretmez; fiyat, hacim, ana eğilim, kırılım ve risk koşulları birlikte doğrulanır.",
    ])
    return "\n".join(lines)
