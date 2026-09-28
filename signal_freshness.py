"""Execution-time freshness and late-entry guards for Telegram signals."""

import math
import time


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def validate_execution_quote(signal, quote, now=None):
    """
    Keep the structural trigger price, but require a fresh executable quote.

    A late quote never becomes a replacement entry. The signal is rejected when
    the original trigger has already been left behind or risk/reward collapsed.
    """
    signal = dict(signal or {})
    quote = dict(quote or {})
    now = time.time() if now is None else float(now)

    scope = str(signal.get("signal_scope") or "").upper()
    trigger = _num(signal.get("entry_price") or signal.get("price"))
    current = _num(quote.get("price"))
    observed_at = _num(quote.get("observed_at"), 0.0) or 0.0
    quote_age = max(0.0, now - observed_at) if observed_at else 9999.0

    if scope not in {"POSITION", "INTRADAY"} or not trigger or not current:
        return None, "missing_execution_quote"
    if quote_age > 45.0:
        return None, "stale_execution_quote"

    slippage_pct = (current / trigger - 1.0) * 100.0
    atr_pct = max(0.0, _num(signal.get("atr_pct"), 0.0) or 0.0)

    if scope == "INTRADAY":
        max_up = min(0.65, max(0.35, atr_pct * 0.75))
        max_down = 0.45
        min_remaining_rr = 0.80
    else:
        max_up = min(1.20, max(0.60, atr_pct * 0.50))
        max_down = 1.00
        min_remaining_rr = 1.00

    if slippage_pct > max_up:
        return None, "late_entry_price_extension"
    if slippage_pct < -max_down:
        return None, "trigger_failed_before_publish"

    stop = _num(signal.get("stop_loss"))
    tp1 = _num(signal.get("tp1"))
    if stop is not None and tp1 is not None:
        remaining_risk = current - stop
        remaining_reward = tp1 - current
        if remaining_risk <= 0 or remaining_reward <= 0:
            return None, "invalid_execution_room"
        if remaining_reward / remaining_risk < min_remaining_rr:
            return None, "execution_risk_reward_collapsed"

    signal["trigger_price"] = round(trigger, 4)
    signal["current_price"] = round(current, 4)
    signal["execution_quote_age_seconds"] = round(quote_age, 2)
    signal["execution_slippage_pct"] = round(slippage_pct, 3)
    signal["execution_source"] = quote.get("source") or "FAST_QUOTE"
    signal["execution_observed_at"] = observed_at
    signal["entry_price"] = round(trigger, 2)
    signal["price"] = round(trigger, 2)
    return signal, "accepted"


def prepare_fresh_candidates(candidates, quotes, now=None, log=None):
    accepted = []
    for signal in candidates or []:
        symbol = str(signal.get("symbol") or "")
        prepared, reason = validate_execution_quote(
            signal,
            (quotes or {}).get(symbol),
            now=now,
        )
        if prepared is not None:
            accepted.append(prepared)
        elif log:
            log(
                "SIGNAL_FRESHNESS_REJECT "
                f"symbol={symbol} scope={signal.get('signal_scope')} reason={reason}"
            )
    return accepted
