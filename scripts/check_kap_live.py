#!/usr/bin/env python3
import json
import sys
from pathlib import Path

# Running "python scripts/check_kap_live.py" sets sys.path[0] to scripts/.
# Add the repository root explicitly so the smoke test exercises production modules.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
        s.get("source") == "KAP_API_IGS"
        and s.get("status") == "healthy"
        for s in health.get("sources") or []
    )

    if not source_ok:
        print("KAP public company-disclosure API did not validate.", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
