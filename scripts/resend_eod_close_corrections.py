#!/usr/bin/env python3
"""Send idempotent, verified-close corrections for a completed BIST report day."""
import argparse
import json
from pathlib import Path
import requests

from app import CHANNEL_ID, REPORT_CHAT_IDS, TELEGRAM_TOKEN
from market_data_hub import fetch_final_close_prices
from trade_ledger import (
    build_v4_daily_report, get_trade_symbols_open_as_of,
    persist_position_close_prices,
)


def send_once(scope, report_date, destination, report):
    marker_dir = Path("data/eod-report-corrections") / report_date
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"{scope}.json"
    try:
        sent = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    except Exception:
        sent = {}
    key = str(destination)
    if sent.get(key):
        print(f"CORRECTION_ALREADY_SENT scope={scope} destination={key}")
        return
    text = (
        f"🛠 <b>KAPANIŞ FİYATI DÜZELTMESİ · {report_date}</b>\\n"
        "Bu rapor, teyitli günlük kapanış verileriyle önceki raporun yerine geçer.\\n\\n"
        + report
    )
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": destination, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    data = response.json() if response.content else {}
    if not response.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram rejected {scope} for {key}: HTTP {response.status_code} {data}")
    sent[key] = True
    marker.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
    print(f"CORRECTION_SENT scope={scope} destination={key}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-date", required=True)
    report_date = parser.parse_args().report_date
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    scopes = {
        "POSITION": REPORT_CHAT_IDS,
        "INTRADAY": [CHANNEL_ID] if CHANNEL_ID else [],
    }
    symbols = set()
    for scope in scopes:
        symbols |= get_trade_symbols_open_as_of(report_date, scope)
    closes = fetch_final_close_prices(sorted(symbols), session_date=report_date)
    print(f"EOD_CORRECTION_CLOSES date={report_date} symbols={len(symbols)} "
          f"verified={len(closes)} missing={sorted(symbols - set(closes))}")
    for scope, destinations in scopes.items():
        persist_position_close_prices(closes, report_date, scope=scope)
        report = build_v4_daily_report(
            scope=scope, verified_close_date=report_date, report_date=report_date
        )
        if not report:
            print(f"CORRECTION_EMPTY scope={scope}")
            continue
        for destination in destinations:
            send_once(scope, report_date, destination, report)


if __name__ == "__main__":
    main()
