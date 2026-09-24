from kap_service import (
    get_kap_health,
    init_kap_store,
    kap_score,
    poll_kap,
    recent_kap_cache,
)
from kap_watchlist_engine import add_to_watchlist


init_kap_store()


def check_kap(fallback_symbols):
    """
    Compatibility adapter used by app.py.

    Only verified official KAP events are returned.
    Third-party/Nitter feeds are intentionally excluded from the trade path.
    """
    new_events = poll_kap(fallback_symbols)
    results = {}

    for event in new_events:
        symbol = event["symbol"]
        add_to_watchlist(symbol)

        event_time = event["event_time"]
        results[symbol] = {
            "kap_id": event["kap_id"],
            "title": event["title"],
            "summary": event.get("summary"),
            "link": event["link"],
            # Legacy consumers expect a local naive datetime.
            "time": event_time.replace(tzinfo=None),
            "published_at": event_time.isoformat(),
            "score": event.get("score", 0),
            "verified": True,
            "source": "KAP_OFFICIAL",
            "alert_sent": False,
        }

    return results


def load_recent_kap_cache(minutes=30):
    return recent_kap_cache(minutes=minutes)


def kap_health():
    return get_kap_health()
