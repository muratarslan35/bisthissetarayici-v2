import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import telegram_market_verifier as verifier
from depth_image_parser import _institution_flow
from strategy_v3 import format_v3_signal_message


class TelegramMarketVerifierTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TABLE external_market_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL, source TEXT NOT NULL, command TEXT NOT NULL,
            status TEXT NOT NULL, requested_at TEXT NOT NULL, response_at TEXT,
            response_text TEXT, parsed_json TEXT, error TEXT,
            depth_command TEXT, depth_status TEXT, depth_requested_at TEXT,
            depth_response_at TEXT, depth_response_text TEXT,
            depth_parsed_json TEXT, depth_error TEXT,
            UNIQUE(trade_date, symbol))""")
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.path)

    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_hard_limit_is_ten_unique_symbols_and_duplicates_do_not_consume(self):
        now = datetime(2026, 9, 28, 9, 50, tzinfo=ZoneInfo("Europe/Istanbul"))
        with patch.object(verifier, "get_connection", self.connection):
            first, created = verifier.reserve({"symbol": "AAA.IS"}, now)
            self.assertTrue(created)
            duplicate, created = verifier.reserve({"symbol": "AAA.IS"}, now)
            self.assertFalse(created)
            self.assertEqual(first["id"], duplicate["id"])
            for index in range(2, 11):
                _, created = verifier.reserve({"symbol": f"S{index}.IS"}, now)
                self.assertTrue(created)
            row, created = verifier.reserve({"symbol": "ELEVEN.IS"}, now)
            self.assertIsNone(row)
            self.assertFalse(created)
            self.assertEqual(verifier.quota_status(now)["symbols"], 10)
            self.assertEqual(verifier.quota_status(now)["maximum_total_queries"], 10)
            self.assertEqual(verifier.quota_status(now)["remaining_queries"], 0)

    def test_parser_never_confirms_menu_only_response(self):
        parsed = verifier.parse_response("Canlı derinlik\nDerinlik görüntü al\nLink yenile")
        self.assertFalse(parsed["confirmation"])

    def test_depth_is_only_reserved_after_ten_for_existing_symbol(self):
        morning = datetime(2026, 9, 28, 9, 50, tzinfo=ZoneInfo("Europe/Istanbul"))
        after_open = datetime(2026, 9, 28, 10, 1, tzinfo=ZoneInfo("Europe/Istanbul"))
        with patch.object(verifier, "get_connection", self.connection):
            verifier.reserve({"symbol": "THYAO.IS"}, morning)
            row, created = verifier.reserve_depth("THYAO.IS", morning)
            self.assertIsNone(row)
            self.assertFalse(created)
            row, created = verifier.reserve_depth("THYAO.IS", after_open)
            self.assertTrue(created)
            self.assertEqual(row["depth_status"], "PENDING")
            _, created = verifier.reserve_depth("THYAO.IS", after_open)
            self.assertFalse(created)
            row, created = verifier.reserve_depth("NOTINPOOL.IS", after_open)
            self.assertIsNone(row)
            self.assertFalse(created)

    def test_depth_parser_requires_buy_side_advantage(self):
        parsed = verifier.parse_depth_response(
            "Alış toplam: 1.500.000\nSatış toplam: 750.000\nAlış/Satış oranı: 2,00"
        )
        self.assertTrue(parsed["confirmation"])

    def test_request_gap_is_rate_safe(self):
        self.assertGreaterEqual(verifier.REQUEST_GAP_SECONDS, 5)
        self.assertLessEqual(verifier.REQUEST_GAP_SECONDS, 30)

    def test_parser_confirms_numerical_theoretical_evidence(self):
        parsed = verifier.parse_response(
            "Teorik eşleşme fiyatı: 21,84\nEşleşme miktarı: 145.000\nAlış fazlası"
        )
        self.assertTrue(parsed["confirmation"])
        self.assertEqual(parsed["unmatched_side"], "BUY")

    def test_only_trade_ready_candidate_is_eligible(self):
        now = datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
        ready = {
            "symbol": "THYAO.IS", "score": 96, "signal_scope": "INTRADAY",
            "entry_price": 300, "stop_loss": 292, "tp1": 310,
            "risk_pct": 2.67, "data_confidence": 95,
            "relative_strength_percentile": 93, "session_rvol": 1.6,
        }
        self.assertTrue(verifier.eligible(ready, now))
        self.assertFalse(verifier.eligible(dict(ready, score=94), now))
        not_ready = dict(ready, session_rvol=0.8)
        self.assertFalse(verifier.eligible(not_ready, now))

    def test_position_eligibility_uses_structural_confidence(self):
        now = datetime(2026, 9, 28, 10, 15, tzinfo=ZoneInfo("Europe/Istanbul"))
        ready = {
            "symbol": "THYAO.IS", "score": 98, "signal_scope": "POSITION",
            "entry_price": 300, "stop_loss": 282, "tp1": 325,
            "risk_pct": 6.0, "data_confidence": 35,
            "structural_data_confidence": 92,
            "relative_strength_percentile": 93,
        }
        self.assertTrue(verifier.eligible(ready, now))
        self.assertFalse(verifier.eligible(dict(ready, structural_data_confidence=60), now))

    def test_new_daytime_candidate_reserves_depth_without_theoretical(self):
        now = datetime(2026, 9, 28, 10, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
        signal = {"symbol": "THYAO.IS"}
        with patch.object(verifier, "get_connection", self.connection):
            row, created = verifier.reserve_daytime_depth(signal, now)
            self.assertTrue(created)
            self.assertEqual(row["status"], "NOT_REQUESTED")
            self.assertEqual(row["depth_status"], "PENDING")
            quota = verifier.quota_status(now)
            self.assertEqual(quota["theoretical_queries"], 0)
            self.assertEqual(quota["depth_queries"], 1)
            self.assertEqual(quota["total_queries"], 1)
            self.assertEqual(quota["remaining_queries"], 9)

    def test_total_theoretical_and_depth_requests_never_exceed_ten(self):
        morning = datetime(2026, 9, 28, 9, 50, tzinfo=ZoneInfo("Europe/Istanbul"))
        daytime = datetime(2026, 9, 28, 10, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
        with patch.object(verifier, "get_connection", self.connection):
            for index in range(6):
                verifier.reserve({"symbol": f"OPEN{index}.IS"}, morning)
            for index in range(4):
                row, created = verifier.reserve_daytime_depth(
                    {"symbol": f"LIVE{index}.IS"}, daytime
                )
                self.assertTrue(created)
                self.assertEqual(row["depth_status"], "PENDING")
            row, created = verifier.reserve_daytime_depth(
                {"symbol": "ELEVEN.IS"}, daytime
            )
            self.assertIsNone(row)
            self.assertFalse(created)
            self.assertEqual(verifier.quota_status(daytime)["total_queries"], 10)

    def test_depth_analysis_requires_volume_pressure_quality_and_fresh_price(self):
        signal = {"current_price": 100.0, "entry_price": 99.5}
        parsed = {
            "header": {"last_price": 100.5, "volume": 2_500_000},
            "depth_totals": {
                "buy_quantity": 1_500_000,
                "sell_quantity": 750_000,
                "buy_sell_ratio": 2.0,
            },
            "quality": {"complete": True, "valid_depth_levels": 9, "trade_rows": 12},
        }
        result = verifier._depth_analysis(signal, parsed)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["market_volume"], 2_500_000)
        self.assertEqual(result["buy_pressure_pct"], 33.3)
        self.assertFalse(verifier._depth_analysis(
            signal, {**parsed, "header": {"last_price": 104.0, "volume": 2_500_000}}
        )["confirmed"])

    def test_non_pool_signal_has_no_bot_label(self):
        now = datetime(2026, 9, 28, 10, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
        signal = {
            "symbol": "THYAO.IS", "score": 96, "signal_scope": "INTRADAY",
            "entry_price": 300, "stop_loss": 292, "tp1": 310, "tp2": 315, "tp3": 320,
            "risk_pct": 2.67, "data_confidence": 95,
            "relative_strength_percentile": 93, "session_rvol": 1.6,
        }
        with patch.object(verifier, "ENABLED", True), \
             patch.object(verifier, "_now", return_value=now), \
             patch.object(verifier, "get_result", return_value=None), \
             patch.object(verifier, "reserve_daytime_depth", return_value=(None, False)):
            output = verifier.gate_signal(signal)
        self.assertIs(output, signal)
        self.assertNotIn("bot_support", output)
        self.assertNotIn("BOT DESTEKLİ", format_v3_signal_message(output))

    def test_theoretical_contribution_is_visibly_labeled(self):
        now = datetime(2026, 9, 28, 9, 55, tzinfo=ZoneInfo("Europe/Istanbul"))
        signal = {
            "symbol": "THYAO.IS", "score": 96, "signal_scope": "INTRADAY",
            "entry_price": 300, "stop_loss": 292, "tp1": 310, "tp2": 315, "tp3": 320,
            "risk_pct": 2.67, "data_confidence": 95,
            "relative_strength_percentile": 93, "session_rvol": 1.6,
        }
        result = {"status": "DATA_READY", "parsed": {
            "theoretical_price": 302, "theoretical_quantity": 150000,
            "unmatched_side": "BUY",
        }}
        with patch.object(verifier, "ENABLED", True), \
             patch.object(verifier, "_now", return_value=now), \
             patch.object(verifier, "get_result", return_value=result):
            output = verifier.gate_signal(signal)
        self.assertEqual(output["bot_support"]["evidence"], "TEORIK")
        self.assertIn("BOT DESTEKLİ TEYİT", format_v3_signal_message(output))

    def test_buyer_seller_rows_are_aggregated_without_losing_both_sides(self):
        flow = _institution_flow([
            {"buyer": "Vakif", "seller": "Halk", "quantity": 1000},
            {"buyer": "Vakif", "seller": "QNB", "quantity": 500},
            {"buyer": "QNB", "seller": "Vakif", "quantity": 200},
        ])
        by_name = {row["institution"]: row for row in flow}
        self.assertEqual(by_name["Vakif"]["net_quantity"], 1300)
        self.assertEqual(by_name["QNB"]["net_quantity"], -300)


if __name__ == "__main__":
    unittest.main()
