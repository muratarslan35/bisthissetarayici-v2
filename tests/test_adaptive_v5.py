import os
import unittest
from pathlib import Path

import pandas as pd
from PIL import Image

from signal_policy import (
    POLICY_VERSION,
    MAX_NEW_ENTRY_DAY_CHANGE_PCT,
    select_publishable_candidates,
)
from signal_card_v3 import build_signal_card


class AdaptiveV5PolicyTests(unittest.TestCase):
    def test_no_market_wide_daily_cap(self):
        candidates = []
        for i in range(20):
            candidates.append({
                "symbol": f"T{i:02d}.IS",
                "signal_scope": "INTRADAY",
                "main_algorithm": "EARLY_IGNITION_V3",
                "score": 88,
                "relative_strength_percentile": 92,
                "session_rvol": 1.8,
                "day_change_pct": 3.0,
            })
        selected = select_publishable_candidates(candidates, "INTRADAY")
        self.assertEqual(len(selected), 20)
        self.assertTrue(all(x["policy_version"] == POLICY_VERSION for x in selected))

    def test_near_ceiling_is_not_new_entry(self):
        candidate = {
            "symbol": "CEIL.IS",
            "signal_scope": "INTRADAY",
            "main_algorithm": "EARLY_IGNITION_V3",
            "score": 95,
            "relative_strength_percentile": 99,
            "session_rvol": 3.0,
            "day_change_pct": MAX_NEW_ENTRY_DAY_CHANGE_PCT,
        }
        self.assertEqual(
            select_publishable_candidates([candidate], "INTRADAY"),
            [],
        )


class SignalCardTests(unittest.TestCase):
    def test_real_candle_card_is_rendered(self):
        idx = pd.date_range("2026-09-25 10:00", periods=50, freq="15min", tz="Europe/Istanbul")
        base = pd.Series(range(50), index=idx, dtype=float) * 0.05 + 30
        df = pd.DataFrame({
            "Open": base,
            "High": base + 0.15,
            "Low": base - 0.12,
            "Close": base + 0.08,
            "Volume": 1_000_000,
        }, index=idx)
        item = {"tf": {"15m": {"df": df}, "4h": {"df": df.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
        }).dropna()}}}
        signal = {
            "symbol": "TEST.IS",
            "signal_scope": "INTRADAY",
            "main_algorithm": "EARLY_IGNITION_V3",
            "score": 88,
            "entry_price": 31.50,
            "current_price": 31.70,
            "stop_loss": 30.90,
            "tp1": 32.20,
            "tp2": 32.90,
            "tp3": 33.80,
            "rsi_15m": 61.2,
            "rsi_4h": 56.1,
            "relative_strength_percentile": 91,
            "session_rvol": 1.7,
            "day_change_pct": 3.4,
            "reasons": ["Hacim güçlü", "Trend yukarı"],
            "time": "11:08",
        }
        path = build_signal_card(signal, item, state={"stage": "GÜÇLÜ", "history": []})
        try:
            self.assertTrue(Path(path).exists())
            self.assertGreater(Path(path).stat().st_size, 20_000)
            with Image.open(path) as card:
                self.assertEqual(card.size, (1440, 2420))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
