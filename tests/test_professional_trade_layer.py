import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import trade_ledger
import dashboard_store
from execution_model import bist_tick_size, modeled_fill, net_result_pct
from quant_validation import calibrated_probability, summarize_closed_trades
from signal_routing import (
    BOT_SUBSCRIBERS,
    TELEGRAM_CHANNEL,
    destination_for_scope,
    enrich_routing,
)


class ExecutionModelTests(unittest.TestCase):
    def test_buy_is_worse_and_sell_is_worse_than_quote(self):
        signal = {
            "signal_scope": "INTRADAY",
            "avg_daily_turnover_tl": 25_000_000,
            "atr_pct": 3.0,
            "data_age_minutes": 2,
        }
        buy = modeled_fill(100.0, signal, "BUY")
        sell = modeled_fill(100.0, signal, "SELL")
        self.assertGreater(buy["fill_price"], 100.0)
        self.assertLess(sell["fill_price"], 100.0)
        self.assertLess(net_result_pct(buy["fill_price"], sell["fill_price"]), 0)

    def test_tick_size_is_price_sensitive(self):
        self.assertEqual(bist_tick_size(10), 0.01)
        self.assertEqual(bist_tick_size(100), 0.10)
        self.assertEqual(bist_tick_size(1200), 1.00)


class SignalRoutingTests(unittest.TestCase):
    def test_position_goes_only_to_bot_subscribers(self):
        signal = enrich_routing({"signal_scope": "POSITION"})
        self.assertEqual(signal["delivery_destination"], BOT_SUBSCRIBERS)
        self.assertEqual(signal["holding_horizon"], "2-10 işlem günü")

    def test_intraday_goes_only_to_channel(self):
        signal = enrich_routing({"signal_scope": "INTRADAY"})
        self.assertEqual(signal["delivery_destination"], TELEGRAM_CHANNEL)
        self.assertEqual(signal["holding_horizon"], "aynı işlem günü")

    def test_unknown_scope_fails_closed(self):
        with self.assertRaises(ValueError):
            destination_for_scope("SCALP_UNKNOWN")


class QuantValidationTests(unittest.TestCase):
    def test_metrics_use_net_results(self):
        rows = [
            {"result_pct": 2.0, "net_result_pct": 1.5},
            {"result_pct": -1.0, "net_result_pct": -1.4},
            {"result_pct": 3.0, "net_result_pct": 2.2},
        ]
        result = summarize_closed_trades(rows)
        self.assertEqual(result["sample_size"], 3)
        self.assertEqual(result["status"], "LEARNING")
        self.assertAlmostEqual(result["expectancy_pct"], (1.5 - 1.4 + 2.2) / 3, places=4)

    def test_small_sample_never_claims_certainty(self):
        rows = [{"score": 82, "net_result_pct": 1.0} for _ in range(4)]
        result = calibrated_probability(82, rows)
        self.assertEqual(result["status"], "LEARNING")
        self.assertLess(result["probability"], 1.0)


class LedgerMigrationTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = handle.name
        handle.close()

        def connection():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

        self.connection_patch = patch.object(trade_ledger, "get_connection", connection)
        self.dashboard_connection_patch = patch.object(
            dashboard_store, "get_connection", connection
        )
        self.connection_patch.start()
        self.dashboard_connection_patch.start()
        trade_ledger.init_trade_ledger()

    def tearDown(self):
        self.connection_patch.stop()
        self.dashboard_connection_patch.stop()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_new_signal_persists_execution_and_calibration_fields(self):
        signal = {
            "symbol": "TEST.IS",
            "signal_scope": "POSITION",
            "main_algorithm": "TREND_START_V3",
            "score": 82,
            "entry_price": 100.0,
            "stop_loss": 96.0,
            "tp1": 106.0,
            "tp2": 112.0,
            "tp3": 120.0,
            "market_regime": "RISK_ON",
            "relative_strength_percentile": 90,
            "avg_daily_turnover_tl": 150_000_000,
            "atr_pct": 2.5,
            "data_age_minutes": 1,
        }
        self.assertTrue(trade_ledger.record_signal(signal))
        conn = trade_ledger.get_connection()
        row = conn.execute("SELECT * FROM paper_trades").fetchone()
        conn.close()
        self.assertGreater(row["modeled_entry_price"], row["entry_price"])
        self.assertGreater(row["estimated_entry_cost_bps"], 0)
        self.assertIsNotNone(row["execution_model_version"])

        events = trade_ledger.update_open_trades("TEST.IS", 121.0)
        self.assertFalse([event for event in events if event.get("type") == "CLOSE"])
        conn = trade_ledger.get_connection()
        days = [f"2026-09-{day:02d}" for day in range(1, 10)]
        path = [
            {"date": day, "first": 100, "last": 100, "high": 100, "low": 100}
            for day in days
        ]
        conn.execute(
            "UPDATE paper_trades SET tracking_days_json=?, tracking_path_json=?, tracking_day_count=9",
            (json.dumps(days), json.dumps(path)),
        )
        conn.commit()
        conn.close()
        tenth_day = datetime(2026, 9, 10, 12, 0, tzinfo=trade_ledger.TR_TZ)
        with patch.object(trade_ledger, "_now", return_value=tenth_day):
            events = trade_ledger.update_open_trades("TEST.IS", 121.0)
        close = [event for event in events if event.get("type") == "CLOSE"]
        self.assertEqual(close[0]["reason"], "TRACKING_10D_COMPLETE")
        self.assertLess(close[0]["gain_pct"], close[0]["gross_gain_pct"])

        dashboard_store.init_dashboard_store()
        payload = dashboard_store.get_dashboard_data()
        self.assertIn("validation", payload)
        self.assertTrue(payload["validation"]["uses_net_execution_results"])

    def test_position_stop_breach_is_recorded_without_closing_tracking(self):
        signal = {
            "symbol": "LIMIT.IS", "signal_scope": "POSITION",
            "main_algorithm": "TREND_START_V3", "score": 92,
            "entry_price": 100.0, "stop_loss": 95.0,
            "tp1": 106.0, "tp2": 112.0, "tp3": 120.0,
        }
        self.assertTrue(trade_ledger.record_signal(signal))
        events = trade_ledger.update_open_trades("LIMIT.IS", 90.0)
        self.assertTrue(any(event["type"] == "STOP_BREACH" for event in events))
        self.assertFalse(any(event["type"] == "CLOSE" for event in events))
        conn = trade_ledger.get_connection()
        row = conn.execute("SELECT status, stop_breached FROM paper_trades").fetchone()
        conn.close()
        self.assertEqual(row["status"], "OPEN")
        self.assertEqual(row["stop_breached"], 1)

    def test_daily_report_is_one_list_with_open_win_and_loss(self):
        now = datetime(2026, 9, 28, 18, 8, tzinfo=trade_ledger.TR_TZ)
        base = {
            "signal_scope": "INTRADAY", "main_algorithm": "INTRADAY_MOMENTUM_V3",
            "score": 92, "stop_loss": 95.0, "tp1": 105.0,
            "tp2": 110.0, "tp3": 115.0,
        }
        with patch.object(trade_ledger, "_now", return_value=now):
            for symbol, entry in (("OPEN.IS", 100.0), ("WIN.IS", 200.0), ("LOSS.IS", 300.0)):
                self.assertTrue(trade_ledger.record_signal({
                    **base, "symbol": symbol, "entry_price": entry,
                }))
            conn = trade_ledger.get_connection()
            conn.execute(
                "UPDATE paper_trades SET status='CLOSED', exit_price=204, net_result_pct=2.0 WHERE symbol='WIN.IS'"
            )
            conn.execute(
                "UPDATE paper_trades SET status='CLOSED', exit_price=294, net_result_pct=-2.0 WHERE symbol='LOSS.IS'"
            )
            conn.commit()
            conn.close()
            report = trade_ledger.build_v4_daily_report(scope="INTRADAY")
        self.assertIn("⏳ OPEN | 100 → -", report)
        self.assertIn("✅ WIN | 200 → 204 | +2.00%", report)
        self.assertIn("❌ LOSS | 300 → 294 | -2.00%", report)
        self.assertIn("Toplam: 3", report)
        self.assertIn("Başarı Oranı: %33.3", report)

    def test_tp1_hit_is_success_for_bot_and_channel_even_after_negative_close(self):
        now = datetime(2026, 9, 28, 18, 8, tzinfo=trade_ledger.TR_TZ)
        base = {
            "main_algorithm": "INTRADAY_MOMENTUM_V3",
            "score": 92, "stop_loss": 97.0,
            "tp1": 101.0, "tp2": 102.0, "tp3": 103.0,
        }
        with patch.object(trade_ledger, "_now", return_value=now):
            for symbol, scope in (("BOTWIN.IS", "POSITION"), ("CHANNELWIN.IS", "INTRADAY")):
                self.assertTrue(trade_ledger.record_signal({
                    **base, "symbol": symbol, "signal_scope": scope,
                    "entry_price": 100.0,
                }))
            conn = trade_ledger.get_connection()
            conn.execute(
                """
                UPDATE paper_trades
                SET status='CLOSED', exit_price=99.0, result_pct=-1.0,
                    net_result_pct=-1.0, tp1_hit=1, closed_at=?
                """,
                ((now + timedelta(days=1)).replace(hour=0, minute=0).isoformat(),),
            )
            conn.commit()
            conn.close()
            bot_report = trade_ledger.build_v4_daily_report(scope="POSITION")
            channel_report = trade_ledger.build_v4_daily_report(scope="INTRADAY")

        self.assertIn("✅ BOTWIN | 100 → 99 | -1.00% · 1. hedef gün içinde görüldü", bot_report)
        self.assertIn("✅ CHANNELWIN | 100 → 99 | -1.00% · 1. hedef gün içinde görüldü", channel_report)
        self.assertIn("Başarılı: 1", bot_report)
        self.assertIn("Başarısız: 0", bot_report)
        self.assertIn("Başarılı: 1", channel_report)
        self.assertIn("Başarısız: 0", channel_report)

    def test_position_report_keeps_prior_open_trade_and_tracking_progress(self):
        opened = datetime(2026, 9, 24, 10, 0, tzinfo=trade_ledger.TR_TZ)
        report_day = datetime(2026, 10, 1, 18, 8, tzinfo=trade_ledger.TR_TZ)
        signal = {
            "symbol": "HOLD.IS", "signal_scope": "POSITION",
            "main_algorithm": "TREND_START_V3", "score": 96,
            "entry_price": 100.0, "stop_loss": 94.0,
            "tp1": 108.0, "tp2": 116.0, "tp3": 125.0,
        }
        with patch.object(trade_ledger, "_now", return_value=opened):
            self.assertTrue(trade_ledger.record_signal(signal))
        conn = trade_ledger.get_connection()
        path = [{"date": "2026-10-01", "last": 104.0}]
        conn.execute(
            "UPDATE paper_trades SET tracking_path_json=?, tracking_day_count=6",
            (json.dumps(path),),
        )
        conn.commit()
        conn.close()
        with patch.object(trade_ledger, "_now", return_value=report_day):
            report = trade_ledger.build_v4_daily_report(scope="POSITION")
        self.assertIn("BOT POZİSYON TAKİP RAPORU", report)
        self.assertIn("⏳ HOLD | 100 → 104 | +4.00% | Takip 6/10", report)


if __name__ == "__main__":
    unittest.main()
