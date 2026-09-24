import tempfile
import unittest
from pathlib import Path

import database
import dashboard_store
import trade_ledger


class DashboardStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "dashboard-test.db"

        database.init_db()
        trade_ledger.init_trade_ledger()
        dashboard_store.init_dashboard_store()

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_persistent_runtime_and_signal_payload(self):
        market = [{
            "symbol": "TEST.IS",
            "current_price": 104.0,
            "data_source": "YAHOO_BATCH",
            "source_bar_time": "2026-09-24T11:00:00+00:00",
            "data_age_minutes": 18.0,
            "data_confidence": 92,
        }]
        ctx = {
            "regime": "RISK_ON",
            "regime_score": 2,
            "breadth_intraday": 0.68,
            "breadth_daily": 0.61,
            "universe_size": 279,
        }
        dashboard_store.persist_market_snapshot(market, ctx, market_open=True)

        signal = {
            "symbol": "TEST.IS",
            "signal_scope": "POSITION",
            "main_algorithm": "SUPER_KOMBINE_V3",
            "score": 88,
            "quality": "A+",
            "entry_price": 100.0,
            "stop_loss": 97.0,
            "tp1": 104.5,
            "tp2": 108.0,
            "tp3": 112.0,
            "market_regime": "RISK_ON",
            "relative_strength_percentile": 96.0,
            "reasons": ["breakout", "relative strength"],
        }
        self.assertTrue(trade_ledger.record_signal(signal))

        data = dashboard_store.get_dashboard_data()

        self.assertTrue(data["runtime"]["worker_active"])
        self.assertTrue(data["runtime"]["market_open"])
        self.assertEqual(data["runtime"]["market_regime"], "RISK_ON")
        self.assertEqual(data["runtime"]["universe_size"], 279)

        self.assertEqual(len(data["position_signals"]), 1)
        row = data["position_signals"][0]
        self.assertEqual(row["symbol"], "TEST.IS")
        self.assertEqual(row["current_price"], 104.0)
        self.assertEqual(row["data_confidence"], 92.0)
        self.assertEqual(row["quality"], "A+")
        self.assertAlmostEqual(row["live_gain_pct"], 4.0)

        self.assertEqual(
            data["performance"]["open_summary"]["POSITION"]["open_trades"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
