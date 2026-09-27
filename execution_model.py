"""Conservative paper-execution model using only freely available inputs.

The model deliberately estimates worse-than-last-price fills.  It is not an
order-book simulator; its purpose is to keep paper results from treating a
displayed quote as a frictionless executable price.
"""

import math
import os


MODEL_VERSION = "FREE_DATA_EXECUTION_V1"

COMMISSION_BPS_PER_SIDE = max(
    0.0, float(os.getenv("PAPER_COMMISSION_BPS_PER_SIDE", "3.0"))
)
BASE_SPREAD_BPS = max(0.0, float(os.getenv("PAPER_BASE_SPREAD_BPS", "8.0")))
BASE_SLIPPAGE_BPS = max(0.0, float(os.getenv("PAPER_BASE_SLIPPAGE_BPS", "6.0")))
LATENCY_BPS = max(0.0, float(os.getenv("PAPER_LATENCY_BPS", "3.0")))


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def bist_tick_size(price):
    """Conservative BIST equity tick approximation, configurable later by feed.

    The function is isolated so an official instrument/tick table can replace
    it without changing signal or ledger code.
    """
    price = _num(price, 0.0) or 0.0
    if price < 20:
        return 0.01
    if price < 50:
        return 0.02
    if price < 100:
        return 0.05
    if price < 250:
        return 0.10
    if price < 500:
        return 0.25
    if price < 1000:
        return 0.50
    return 1.00


def _round_to_tick(price, side):
    tick = bist_tick_size(price)
    units = price / tick
    rounded = math.ceil(units) if side == "BUY" else math.floor(units)
    return round(max(tick, rounded * tick), 4)


def estimate_one_way_bps(signal, side="BUY"):
    """Estimate one-way friction without paid bid/ask or order-book data."""
    scope = str(signal.get("signal_scope") or signal.get("scope") or "POSITION")
    turnover = _num(signal.get("avg_daily_turnover_tl"), 0.0) or 0.0
    atr_pct = _num(signal.get("atr_pct"), 0.0) or 0.0
    age = _num(signal.get("data_age_minutes"), 0.0) or 0.0

    # Lower turnover and higher volatility imply wider effective spread and
    # greater slippage.  Caps prevent a bad field from creating absurd fills.
    if turnover >= 250_000_000:
        liquidity_bps = 0.0
    elif turnover >= 100_000_000:
        liquidity_bps = 2.0
    elif turnover >= 40_000_000:
        liquidity_bps = 5.0
    else:
        liquidity_bps = 10.0

    volatility_bps = min(12.0, max(0.0, atr_pct - 1.5) * 2.0)
    stale_bps = min(10.0, max(0.0, age - 5.0) * 0.5)
    horizon_bps = 2.0 if scope == "INTRADAY" else 0.0

    return round(
        COMMISSION_BPS_PER_SIDE
        + BASE_SPREAD_BPS / 2.0
        + BASE_SLIPPAGE_BPS
        + LATENCY_BPS
        + liquidity_bps
        + volatility_bps
        + stale_bps
        + horizon_bps,
        3,
    )


def modeled_fill(price, signal, side):
    price = _num(price)
    if price is None or price <= 0:
        raise ValueError("price must be positive")
    side = str(side).upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    bps = estimate_one_way_bps(signal, side=side)
    multiplier = 1.0 + bps / 10_000.0 if side == "BUY" else 1.0 - bps / 10_000.0
    fill = _round_to_tick(price * multiplier, side)
    return {
        "fill_price": fill,
        "cost_bps": bps,
        "model_version": MODEL_VERSION,
    }


def net_result_pct(entry_fill, exit_fill):
    entry = _num(entry_fill)
    exit_price = _num(exit_fill)
    if not entry or exit_price is None:
        return None
    return round((exit_price / entry - 1.0) * 100.0, 4)
