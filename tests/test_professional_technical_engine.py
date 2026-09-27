import unittest

import numpy as np
import pandas as pd

from professional_technical_engine import (
    ENGINE_VERSION,
    analyze_position_structure,
    demark_sequential,
    most,
    smart_money_zones,
    support_resistance,
    volume_price_evidence,
)


def frame(close, volume=None, spread=1.2):
    close = np.asarray(close, dtype=float)
    volume = np.asarray(volume if volume is not None else np.full(len(close), 1_000_000), dtype=float)
    idx = pd.date_range("2025-01-02", periods=len(close), freq="h", tz="UTC")
    return pd.DataFrame({
        "Open": close - 0.15,
        "High": close + spread / 2,
        "Low": close - spread / 2,
        "Close": close,
        "Volume": volume,
    }, index=idx)


class ProfessionalTechnicalEngineTests(unittest.TestCase):
    def test_most_has_persistent_stop_and_detects_uptrend(self):
        result = most(frame(np.linspace(80, 120, 80)))
        self.assertEqual(result["trend"], "UP")
        self.assertLess(result["level"], 120)

    def test_demark_counts_completed_sell_setup(self):
        result = demark_sequential(frame(np.arange(1.0, 45.0)))
        self.assertEqual(result["sell_setup"], 9)
        self.assertTrue(result["exhaustion_risk"])

    def test_volume_price_layer_marks_proxy_not_order_book(self):
        close = np.linspace(90, 105, 50)
        volume = np.linspace(800_000, 1_800_000, 50)
        result = volume_price_evidence(frame(close, volume), float(close[-1]))
        self.assertEqual(result["data_class"], "OHLCV_PROXY_NOT_ORDER_BOOK")
        self.assertIn("cmf20", result)
        self.assertIn("volume_profile_poc", result)
        self.assertTrue(result["above_anchored_vwap"])

    def test_order_block_layer_is_explicitly_candle_derived(self):
        close = np.linspace(100, 103, 45)
        close[20] = 99.0
        close[21:24] = [101.0, 103.0, 105.0]
        result = smart_money_zones(frame(close), float(close[-1]))
        self.assertEqual(result["data_class"], "CANDLE_DERIVED_NOT_L2")

    def test_support_resistance_returns_structured_levels(self):
        x = np.arange(100)
        close = 100 + x * 0.08 + np.sin(x / 3) * 2.0
        result = support_resistance(frame(close), float(close[-1]))
        self.assertIn("support", result)
        self.assertIn("resistance", result)
        self.assertIn("overhead_atr", result)

    def test_combined_assessment_contains_all_professional_layers(self):
        daily = frame(80 + np.arange(260) * 0.12 + np.sin(np.arange(260) / 5))
        four = frame(95 + np.arange(100) * 0.09 + np.sin(np.arange(100) / 4))
        hour = frame(100 + np.arange(120) * 0.04 + np.sin(np.arange(120) / 3))
        result = analyze_position_structure(daily, four, hour, float(hour["Close"].iloc[-1]))
        self.assertEqual(result["version"], ENGINE_VERSION)
        self.assertIn(result["phase"], {"STARTING", "EARLY_TREND", "EXTENDED", "UNCONFIRMED"})
        structures = result["structures"]
        for key in ("support_resistance", "channels", "most", "demark", "patterns", "volume_price", "candle_zones", "market_structure"):
            self.assertIn(key, structures)
        self.assertIn("No L2/order-book", structures["data_limit"])


if __name__ == "__main__":
    unittest.main()
