import os

TRADING_POLICY_VERSION = "ADAPTIVE_V5"
POLICY_VERSION = TRADING_POLICY_VERSION

POSITION_MIN_SCORE = float(os.getenv("POSITION_MIN_PUBLISH_SCORE", "77"))
INTRADAY_MIN_SCORE = float(os.getenv("INTRADAY_MIN_PUBLISH_SCORE", "82"))
MAX_NEW_ENTRY_DAY_CHANGE_PCT = float(os.getenv("MAX_NEW_ENTRY_DAY_CHANGE_PCT", "9.2"))


def _num(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _minimum_score(scope):
    return POSITION_MIN_SCORE if scope == "POSITION" else INTRADAY_MIN_SCORE


def _priority(signal):
    score = _num(signal.get("score"), 0.0) or 0.0
    rs = (_num(signal.get("relative_strength_percentile"), 0.0) or 0.0) / 100.0
    rvol = _num(signal.get("session_rvol"), 0.0) or 0.0
    day_change = _num(signal.get("day_change_pct"), 0.0) or 0.0
    rsi4 = _num(signal.get("rsi_4h"))

    value = score
    value += min(5.0, rs * 5.0)
    value += min(5.0, max(0.0, rvol - 1.0) * 3.0)

    if str(signal.get("main_algorithm") or "").startswith("KAP_"):
        value += 2.0

    # Strong movers remain eligible, but late entries are ranked below
    # equally strong earlier setups. Only near-ceiling moves are rejected.
    if day_change > 6.0:
        value -= min(6.0, (day_change - 6.0) * 1.5)
    if rsi4 is not None and rsi4 > 72:
        value -= min(5.0, (rsi4 - 72.0) * 0.8)

    return round(value, 4)


def select_publishable_candidates(candidates, scope, cycle_cap=None):
    """
    Quality gate without arbitrary daily/hourly market quotas.

    Every genuinely qualifying symbol can be published. Spam is controlled by
    per-symbol state transitions in signal_state.py, not by hiding later strong
    movers simply because an earlier daily quota was consumed.
    """
    prepared = []
    seen = set()
    minimum = _minimum_score(scope)

    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue

        signal = dict(raw)
        symbol = str(signal.get("symbol") or "")
        if not symbol or symbol in seen:
            continue
        if signal.get("signal_scope") != scope:
            continue

        score = _num(signal.get("score"), 0.0) or 0.0
        if score < minimum:
            continue

        day_change = _num(signal.get("day_change_pct"))
        if day_change is not None and day_change >= MAX_NEW_ENTRY_DAY_CHANGE_PCT:
            continue

        signal["policy_version"] = POLICY_VERSION
        signal["publish_priority"] = _priority(signal)
        prepared.append(signal)
        seen.add(symbol)

    prepared.sort(
        key=lambda x: (x.get("publish_priority", 0.0), x.get("score", 0.0)),
        reverse=True,
    )

    if cycle_cap is not None:
        try:
            limit = int(cycle_cap)
            if limit > 0:
                return prepared[:limit]
        except Exception:
            pass

    return prepared


def policy_limits():
    return {
        "policy_version": POLICY_VERSION,
        "mode": "adaptive_stateful",
        "position": {
            "min_score": POSITION_MIN_SCORE,
            "market_wide_daily_cap": None,
            "market_wide_hourly_cap": None,
        },
        "intraday": {
            "min_score": INTRADAY_MIN_SCORE,
            "market_wide_daily_cap": None,
            "market_wide_hourly_cap": None,
        },
        "duplicate_control": "per_symbol_state_transition",
        "max_new_entry_day_change_pct": MAX_NEW_ENTRY_DAY_CHANGE_PCT,
    }
