import unittest
from datetime import datetime, timezone

from signal_freshness import prepare_fresh_candidates, validate_execution_quote


class SignalFreshnessTests(unittest.TestCase):
    def base(self, scope="INTRADAY"):
        return {
            "symbol": "TEST.IS",
            "signal_scope": scope,
            "entry_price": 100.0,
            "stop_loss": 98.0 if scope == "INTRADAY" else 94.0,
            "tp1": 104.0 if scope == "INTRADAY" else 109.0,
            "atr_pct": 1.0,
        }

    def quote(self, price, age=5):
        return {
            "price": price,
            "observed_at": 1_000.0 - age,
            "source": "TRADINGVIEW_FAST",
        }

    def test_keeps_trigger_and_records_current_execution_quote(self):
        result, reason = validate_execution_quote(
            self.base(), self.quote(100.3), now=1_000.0
        )
        self.assertEqual(reason, "accepted")
        self.assertEqual(result["entry_price"], 100.0)
        self.assertEqual(result["current_price"], 100.3)
        self.assertEqual(result["trigger_price"], 100.0)

    def test_rejects_intraday_move_already_left_behind(self):
        result, reason = validate_execution_quote(
            self.base(), self.quote(100.9), now=1_000.0
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "late_entry_price_extension")

    def test_last_mile_check_blocks_42_20_to_42_60_move(self):
        signal = self.base()
        signal["entry_price"] = 42.20
        signal["stop_loss"] = 41.0
        signal["tp1"] = 45.0
        result, reason = validate_execution_quote(
            signal,
            self.quote(42.60),
            now=1_000.0,
            max_quote_age_seconds=10.0,
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "late_entry_price_extension")

    def test_last_mile_check_rejects_quote_older_than_ten_seconds(self):
        result, reason = validate_execution_quote(
            self.base(),
            self.quote(100.1, age=11),
            now=1_000.0,
            max_quote_age_seconds=10.0,
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "delivery_quote_expired")

    def test_last_mile_check_accepts_fresh_quote_inside_entry_window(self):
        result, reason = validate_execution_quote(
            self.base(),
            self.quote(100.25, age=2),
            now=1_000.0,
            max_quote_age_seconds=10.0,
        )
        self.assertEqual(reason, "accepted")
        self.assertEqual(result["entry_price"], 100.0)
        self.assertEqual(result["current_price"], 100.25)

    def test_rejects_stale_quote(self):
        result, reason = validate_execution_quote(
            self.base(), self.quote(100.1, age=60), now=1_000.0
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "stale_execution_quote")

    def test_rejects_intraday_decision_that_waited_before_publish(self):
        signal = self.base()
        signal["generated_at"] = datetime.fromtimestamp(
            960.0, tz=timezone.utc
        ).isoformat()
        result, reason = validate_execution_quote(
            signal, self.quote(100.1), now=1_000.0
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "late_decision_latency")

    def test_position_has_looser_but_bounded_execution_window(self):
        accepted, _ = validate_execution_quote(
            self.base("POSITION"), self.quote(100.55), now=1_000.0
        )
        rejected, reason = validate_execution_quote(
            self.base("POSITION"), self.quote(101.3), now=1_000.0
        )
        self.assertIsNotNone(accepted)
        self.assertIsNone(rejected)
        self.assertEqual(reason, "late_entry_price_extension")

    def test_batch_logs_rejections(self):
        logs = []
        out = prepare_fresh_candidates(
            [self.base()],
            {"TEST.IS": self.quote(101.0)},
            now=1_000.0,
            log=logs.append,
        )
        self.assertEqual(out, [])
        self.assertIn("late_entry_price_extension", logs[0])


if __name__ == "__main__":
    unittest.main()
