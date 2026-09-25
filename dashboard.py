from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify

from dashboard_store import get_dashboard_data
from universe_manager import universe_status

dashboard_bp = Blueprint("dashboard", __name__)
TR_TZ = ZoneInfo("Europe/Istanbul")

# Legacy compatibility only. V3 dashboard reads durable SQLite state so
# gunicorn and the scanner worker do not need to share process memory.
LIVE_PRICES = {}
SIGNALS = []
SCALPING_SIGNALS = []
SUCCESS_SIGNALS = []

MAX_SIGNALS = 200
MAX_SCALPING_SIGNALS = 200
MAX_SUCCESS_SIGNALS = 50


def push_signal(signal):
    """
    Backward-compatible hook for legacy mode.
    V3 signals are persisted by trade_ledger.record_signal and are read from DB.
    """
    target = (
        SCALPING_SIGNALS
        if signal.get("signal_scope") == "INTRADAY"
        or signal.get("main_algorithm") == "SCALPING"
        else SIGNALS
    )
    limit = MAX_SCALPING_SIGNALS if target is SCALPING_SIGNALS else MAX_SIGNALS

    target.insert(0, dict(signal))
    del target[limit:]

    symbol = signal.get("symbol")
    price = signal.get("price")
    if symbol and isinstance(price, (int, float)):
        LIVE_PRICES[symbol] = price


def push_success_signal(signal):
    SUCCESS_SIGNALS.insert(0, dict(signal))
    del SUCCESS_SIGNALS[MAX_SUCCESS_SIGNALS:]


@dashboard_bp.route("/api/dashboard")
def dashboard_api():
    """
    Durable dashboard API.

    Authentication is enforced by app.before_request. The endpoint no longer
    depends on Python globals and therefore works with a separate worker process.
    """
    payload = get_dashboard_data()
    payload["server_time"] = datetime.now(TR_TZ).strftime("%H:%M:%S")
    try:
        payload["universe"] = universe_status()
    except Exception:
        payload["universe"] = {}
    return jsonify(payload)
