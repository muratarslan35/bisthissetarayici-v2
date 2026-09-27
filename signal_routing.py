"""Single source of truth for bot/channel signal separation."""

POSITION_SCOPE = "POSITION"
INTRADAY_SCOPE = "INTRADAY"

BOT_SUBSCRIBERS = "BOT_SUBSCRIBERS"
TELEGRAM_CHANNEL = "TELEGRAM_CHANNEL"

ROUTING_CONTRACT_VERSION = "ROUTING_V1"


def destination_for_scope(scope):
    scope = str(scope or "").upper()
    if scope == POSITION_SCOPE:
        return BOT_SUBSCRIBERS
    if scope == INTRADAY_SCOPE:
        return TELEGRAM_CHANNEL
    raise ValueError(f"unsupported signal scope: {scope!r}")


def enrich_routing(signal):
    routed = dict(signal or {})
    scope = str(routed.get("signal_scope") or "").upper()
    routed["signal_scope"] = scope
    routed["delivery_destination"] = destination_for_scope(scope)
    routed["routing_contract_version"] = ROUTING_CONTRACT_VERSION
    routed["holding_horizon"] = (
        "2-10 işlem günü" if scope == POSITION_SCOPE else "aynı işlem günü"
    )
    return routed


def route_trade_event(event):
    return destination_for_scope(event.get("scope"))
