import json
import os
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

import pandas as pd

import market_data_hub
import trade_ledger


class FinalCloseFetchTests(unittest.TestCase):
    def test_fetches_exact_session_daily_close_and_preserves_symbol_key(self):
        symbol = "LMKDC.IS"
        frame = pd.DataFrame(
            {"Close": [24.20, 24.56, 24.80]},
            index=pd.to_datetime([
                "2026-09-30T21:00:00Z",
                "2026-10-01T21:00:00Z",
                "2026-10-02T21:00:00Z",
            ]),
        )
        with patch.object(
            market_data_hub, "_download_batch", return_value={symbol: frame}
        ) as download:
            result = market_data_hub.fetch_final_close_prices(
                [symbol], session_date="2026-10-02"
            )

        download.assert_called_once_with([symbol], "1d", "10d")
        self.assertEqual(result[symbol]["price"], 24.56)
        self.assertEqual(result[symbol]["session_date"], "2026-10-02")
        self.assertEqual(result[symbol]["data_source"], "YAHOO_DAILY_CLOSE")

    def test_refuses_to_substitute_a_previous_session_close(self):
        symbol = "HUNER.IS"
        frame = pd.DataFrame(
            {"Close": [4.60]},
            index=pd.to_datetime(["2026-10-01T21:00:00Z"]),
        )
        with patch.object(
            market_data_hub, "_download_batch", return_value={symbol: frame}
        ):
            result = market_data_hub.fetch_final_close_prices(
                [symbol], session_date="2026-10-03"
            )
        self.assertEqual(result, {})


class PositionClosePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "trades.sqlite3")
        self._create_db()
        self.connection_patch = patch(
            "trade_ledger.get_connection", side_effect=self._connect
        )
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_db(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE paper_trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                scope TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_price REAL,
                exit_price REAL,
                result_pct REAL,
                net_result_pct REAL,
                opened_at TEXT,
                closed_at TEXT,
                tracking_day_count INTEGER DEFAULT 0,
                tracking_days_json TEXT DEFAULT '[]',
                tracking_path_json TEXT DEFAULT '[]',
                policy_version TEXT
            )
        """)
        old_path = json.dumps([
            {"date": "2026-10-01", "first": 23.50, "last": 24.20,
             "high": 24.70, "low": 23.20, "return_pct": 2.98}
        ])
        opened = (trade_ledger._now() - timedelta(days=1)).isoformat()
        conn.execute("""
            INSERT INTO paper_trades (
                id, symbol, scope, status, entry_price, opened_at,
                tracking_day_count, tracking_days_json, tracking_path_json,
                policy_version
            ) VALUES (1, 'LMKDC.IS', 'POSITION', 'OPEN', 23.50, ?, 1,
                      '["2026-10-01"]', ?, ?)
        """, (opened, old_path, trade_ledger.POLICY_VERSION))
        conn.commit()
        conn.close()

    def test_verified_close_is_persisted_and_used_by_report(self):
        day = trade_ledger._now().date().isoformat()
        saved = trade_ledger.persist_position_close_prices(
            {
                "LMKDC.IS": {
                    "price": 24.56,
                    "session_date": day,
                    "source_bar_time": day + "T18:00:00+03:00",
                    "fetched_at": trade_ledger._now().isoformat(),
                }
            },
            day,
        )
        report = trade_ledger.build_v4_daily_report(
            scope="POSITION", verified_close_date=day
        )
        self.assertEqual(saved, {"LMKDC.IS"})
        self.assertIn("→ 24.56 |", report)
        self.assertIn("YAHOO günlük OHLCV", report)

    def test_verified_report_never_falls_back_to_yesterdays_path_price(self):
        day = trade_ledger._now().date().isoformat()
        report = trade_ledger.build_v4_daily_report(
            scope="POSITION", verified_close_date=day
        )
        self.assertIn("kapanış doğrulanamadı", report)
        self.assertNotIn("→ 24.2 |", report)


if __name__ == "__main__":
    unittest.main()
