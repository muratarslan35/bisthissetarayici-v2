import time
import unittest

import fast_market_lane
import universe_manager


class DynamicUniverseTests(unittest.TestCase):
    def test_promotes_liquid_symbol_outside_static_fallback(self):
        metrics = {
            "NEWCO.IS": {
                "price": 25.0,
                "turnover": 60_000_000.0,
                "avg_turnover_10d": 55_000_000.0,
                "rvol": 1.6,
                "day_change_pct": 2.0,
                "three_day_return_pct": 4.0,
                "five_day_return_pct": 7.0,
                "positive_days_5d": 4,
            },
            "THINX.IS": {
                "price": 10.0,
                "turnover": 1_000_000.0,
                "avg_turnover_10d": 900_000.0,
                "rvol": 2.0,
                "day_change_pct": 4.0,
                "three_day_return_pct": 7.0,
                "five_day_return_pct": 10.0,
                "positive_days_5d": 4,
            },
        }

        promoted, fast = universe_manager._classify(metrics)

        self.assertIn("NEWCO.IS", promoted)
        self.assertIn("NEWCO.IS", fast)
        self.assertNotIn("THINX.IS", promoted)

    def test_slow_burn_can_pass_with_lower_but_real_liquidity(self):
        metrics = {
            "STEADY.IS": {
                "price": 18.0,
                "turnover": 10_000_000.0,
                "avg_turnover_10d": 11_000_000.0,
                "rvol": 0.9,
                "day_change_pct": 0.8,
                "three_day_return_pct": 2.5,
                "five_day_return_pct": 5.0,
                "positive_days_5d": 4,
            }
        }

        promoted, _ = universe_manager._classify(metrics)

        self.assertTrue(metrics["STEADY.IS"]["slow_burn"])
        self.assertIn("STEADY.IS", promoted)


class FastLaneTests(unittest.TestCase):
    def setUp(self):
        fast_market_lane._HISTORY.clear()

    def test_detects_short_horizon_liquid_momentum(self):
        now = time.time()
        fast_market_lane._HISTORY["TEST.IS"].append(
            {"ts": now - 65, "price": 100.0, "volume": 1_000_000.0}
        )
        change = fast_market_lane._history_change("TEST.IS", now, 100.5, 60)
        quote = {
            "symbol": "TEST.IS",
            "price": 100.5,
            "rvol": 1.5,
            "day_change_pct": 2.2,
            "change_15s_pct": 0.1,
            "change_30s_pct": 0.2,
            "change_60s_pct": change,
            "observed_at": now,
        }

        self.assertGreaterEqual(change, 0.49)
        self.assertTrue(fast_market_lane.is_early_mover(quote))

    def test_rejects_limit_chase(self):
        quote = {
            "symbol": "TEST.IS",
            "price": 109.8,
            "rvol": 3.0,
            "day_change_pct": 9.8,
            "change_15s_pct": 0.5,
            "change_30s_pct": 0.8,
            "change_60s_pct": 1.2,
            "observed_at": time.time(),
        }
        self.assertFalse(fast_market_lane.is_early_mover(quote))


if __name__ == "__main__":
    unittest.main()
