import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_connection

TR_TZ = ZoneInfo("Europe/Istanbul")

POLICY_VERSION = "SELECTIVE_V4"

POSITION_MIN_SCORE = float(os.getenv("POSITION_MIN_PUBLISH_SCORE", "80"))
INTRADAY_MIN_SCORE = float(os.getenv("INTRADAY_MIN_PUBLISH_SCORE", "84"))

POSITION_DAILY_CAP = max(1, int(os.getenv("POSITION_DAILY_SIGNAL_CAP", "3")))
INTRADAY_DAILY_CAP = max(1, int(os.getenv("INTRADAY_DAILY_SIGNAL_CAP", "6")))

POSITION_HOURLY_CAP = max(1, int(os.getenv("POSITION_HOURLY_SIGNAL_CAP", "1")))
INTRADAY_HOURLY_CAP = max(1, int(os.getenv("INTRADAY_HOURLY_SIGNAL_CAP", "2")))

POSITION_CYCLE_CAP = max(1, int(os.getenv("POSITION_CYCLE_SIGNAL_CAP", "1")))
INTRADAY_CYCLE_CAP = max(1, int(os.getenv("INTRADAY_CYCLE_SIGNAL_CAP", "2")))

MAX_NEW_ENTRY_DAY_CHANGE_PCT = float(os.getenv("MAX_NEW_ENTRY_DAY_CHANGE_PCT", "8.0"))


def _now():
    return datetime.now(TR_TZ)


def _num(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _limits(scope):
    if scope == "POSITION":
        return {
            "min_score": POSITION_MIN_SCORE,
            "daily": POSITION_DAILY_CAP,
            "hourly": POSITION_HOURLY_CAP,
            "cycle": POSITION_CYCLE_CAP,
        }
    return {
        "min_score": INTRADAY_MIN_SCORE,
        "daily": INTRADAY_DAILY_CAP,
        "hourly": INTRADAY_HOURLY_CAP,
        "cycle": INTRADAY_CYCLE_CAP,
    }


def _priority(signal):
    score = _num(signal.get("score"), 0.0) or 0.0
    rs = (_num(signal.get("relative_strength_percentile"), 0.0) or 0.0) / 100.0
    rvol = _num(signal.get("session_rvol"), 0.0) or 0.0
    day_change = _num(signal.get("day_change_pct"), 0.0) or 0.0

    value = score
    value += min(4.0, rs * 4.0)
    value += min(4.0, max(0.0, rvol - 1.0) * 3.0)

    algo = str(signal.get("main_algorithm") or "")
    if algo.startswith("KAP_"):
        value += 2.0

    # Prefer an early, tradable move to an already extended late chase.
    if day_change > 5.0:
        value -= min(8.0, (day_change - 5.0) * 2.0)

    return round(value, 4)


def _today_bounds():
    now = _now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    return start.isoformat(), hour_start.isoformat()


def select_publishable_candidates(candidates, scope, cycle_cap=None):
    """
    Market-level publication gate.

    Strategy evaluators may find many technically valid setups. This gate is
    intentionally stricter: one symbol per scope/day, hard daily/hourly caps,
    minimum quality score and a no-chase ceiling filter. Candidates are ranked
    before publication so the first symbol in the universe cannot consume the
    quota ahead of stronger setups.
    """
    limits = _limits(scope)
    cycle_limit = int(cycle_cap or limits["cycle"])

    prepared = []
    seen_input = set()
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        signal = dict(raw)
        symbol = str(signal.get("symbol") or "")
        if not symbol or symbol in seen_input:
            continue
        if signal.get("signal_scope") != scope:
            continue

        score = _num(signal.get("score"), 0.0) or 0.0
        if score < limits["min_score"]:
            continue

        day_change = _num(signal.get("day_change_pct"))
        if day_change is not None and day_change >= MAX_NEW_ENTRY_DAY_CHANGE_PCT:
            continue

        signal["policy_version"] = POLICY_VERSION
        signal["publish_priority"] = _priority(signal)
        prepared.append(signal)
        seen_input.add(symbol)

    if not prepared:
        return []

    prepared.sort(
        key=lambda x: (x.get("publish_priority", 0.0), x.get("score", 0.0)),
        reverse=True,
    )

    day_start, hour_start = _today_bounds()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM strategy_signals
        WHERE scope=? AND policy_version=? AND created_at>=?
        """,
        (scope, POLICY_VERSION, day_start),
    )
    daily_count = int(cur.fetchone()["c"] or 0)

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM strategy_signals
        WHERE scope=? AND policy_version=? AND created_at>=?
        """,
        (scope, POLICY_VERSION, hour_start),
    )
    hourly_count = int(cur.fetchone()["c"] or 0)

    cur.execute(
        """
        SELECT DISTINCT symbol
        FROM strategy_signals
        WHERE scope=? AND policy_version=? AND created_at>=?
        """,
        (scope, POLICY_VERSION, day_start),
    )
    already_today = {row["symbol"] for row in cur.fetchall()}
    conn.close()

    daily_left = max(0, limits["daily"] - daily_count)
    hourly_left = max(0, limits["hourly"] - hourly_count)
    allowed = min(cycle_limit, daily_left, hourly_left)
    if allowed <= 0:
        return []

    selected = []
    for signal in prepared:
        if signal["symbol"] in already_today:
            continue
        selected.append(signal)
        already_today.add(signal["symbol"])
        if len(selected) >= allowed:
            break

    return selected


def policy_limits():
    return {
        "policy_version": POLICY_VERSION,
        "position": {
            "min_score": POSITION_MIN_SCORE,
            "daily_cap": POSITION_DAILY_CAP,
            "hourly_cap": POSITION_HOURLY_CAP,
            "cycle_cap": POSITION_CYCLE_CAP,
        },
        "intraday": {
            "min_score": INTRADAY_MIN_SCORE,
            "daily_cap": INTRADAY_DAILY_CAP,
            "hourly_cap": INTRADAY_HOURLY_CAP,
            "cycle_cap": INTRADAY_CYCLE_CAP,
        },
        "max_new_entry_day_change_pct": MAX_NEW_ENTRY_DAY_CHANGE_PCT,
    }
