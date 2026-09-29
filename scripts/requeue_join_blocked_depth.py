"""One-shot recovery for depth requests blocked by the source bot's join gate.

This updates existing rows only. It never inserts a symbol and therefore does
not consume or reset the persisted daily request quota.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


TR_TZ = ZoneInfo("Europe/Istanbul")
DB_PATH = Path(__file__).resolve().parents[1] / "system.db"


def requeue(apply=False):
    today = datetime.now(TR_TZ).date().isoformat()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, symbol, depth_status
                 FROM external_market_verifications
                WHERE trade_date=?
                  AND status='NOT_REQUESTED'
                  AND depth_status='INSUFFICIENT_DATA'
                ORDER BY id""",
            (today,),
        ).fetchall()
        symbols = [row["symbol"] for row in rows]
        if apply and rows:
            now = datetime.now(TR_TZ).isoformat()
            conn.executemany(
                """UPDATE external_market_verifications
                      SET depth_status='PENDING', depth_requested_at=?,
                          depth_response_at=NULL, depth_response_text=NULL,
                          depth_parsed_json=NULL, depth_error=NULL
                    WHERE id=? AND depth_status='INSUFFICIENT_DATA'""",
                [(now, row["id"]) for row in rows],
            )
            conn.commit()
        return {"date": today, "count": len(rows), "symbols": symbols, "applied": apply}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(requeue(apply=args.apply))
