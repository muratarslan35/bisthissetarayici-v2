#!/usr/bin/env python3
import json
import sys

from kap_service import get_kap_health, init_kap_store, poll_kap
from utils import FALLBACK_SYMBOLS


def main():
    init_kap_store()
    new_events = poll_kap(FALLBACK_SYMBOLS, force=True)
    health = get_kap_health()

    print(json.dumps({
        "new_trade_relevant_events": len(new_events),
        "overall": health.get("overall"),
        "verified_events_24h": health.get("verified_events_24h"),
        "sources": health.get("sources"),
        "recent_count": len(health.get("recent") or []),
    }, ensure_ascii=False, indent=2, default=str))

    source_ok = any(
        s.get("source") in {"KAP_RSS", "KAP_HTML"}
        and s.get("status") in {"healthy", "reachable_no_rows"}
        for s in health.get("sources") or []
    )

    if not source_ok:
        print("No official KAP source validated.", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
