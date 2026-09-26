import unittest

import pandas as pd

from strategy_v3 import _rsi_wilder, format_v3_signal_message


class RsiSignalContextTests(unittest.TestCase):
    def test_wilder_rsi_rising_series(self):
        series = pd.Series([float(i) for i in range(1, 40)])
        self.assertEqual(_rsi_wilder(series, 14), 100.0)

    def test_wilder_rsi_flat_series(self):
        series = pd.Series([10.0] * 40)
        self.assertEqual(_rsi_wilder(series, 14), 50.0)

    def test_intraday_message_shows_15m_and_4h_rsi(self):
        signal = {
            "signal_scope": "INTRADAY",
            "symbol": "TEST.IS",
            "main_algorithm": "EARLY_IGNITION_V3",
            "score": 82,
            "quality": "A",
            "entry_price": 100.0,
            "stop_loss": 98.0,
            "tp1": 102.4,
            "tp2": 105.0,
            "tp3": 108.0,
            "risk_pct": 2.0,
            "market_regime": "RISK_ON",
            "relative_strength_percentile": 90.0,
            "rsi_15m": 61.25,
            "rsi_4h": 57.80,
            "reasons": [],
        }
        message = format_v3_signal_message(signal)
        self.assertIn("RSI15 61.25", message)
        self.assertIn("RSI4s 57.8", message)
        self.assertIn("82/100", message)
        self.assertIn("Erken Hareket Başlangıcı", message)
        self.assertNotIn("RISK_ON", message)

    def test_position_message_shows_4h_and_daily_rsi(self):
        signal = {
            "signal_scope": "POSITION",
            "symbol": "TEST.IS",
            "main_algorithm": "SUPER_KOMBINE_V3",
            "score": 86,
            "quality": "A+",
            "entry_price": 100.0,
            "stop_loss": 96.0,
            "tp1": 106.0,
            "tp2": 112.0,
            "tp3": 120.0,
            "risk_pct": 4.0,
            "market_regime": "NEUTRAL",
            "relative_strength_percentile": 92.0,
            "rsi_4h": 58.4,
            "rsi_1d": 63.7,
            "reasons": [],
        }
        message = format_v3_signal_message(signal)
        self.assertIn("RSI4s 58.4", message)
        self.assertIn("RSI1g 63.7", message)


if __name__ == "__main__":
    unittest.main()
