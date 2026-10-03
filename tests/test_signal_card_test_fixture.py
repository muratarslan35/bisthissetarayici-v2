import os
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import numpy as np
import pandas as pd

import signal_card_v3
from professional_technical_engine import rsi_wilder_series
from signal_card_test_fixture import build_test_signal_card
from strategy_v3 import _rsi_wilder


class SignalCardTestFixtureTests(unittest.TestCase):
    def test_fixture_uses_production_card_dimensions(self):
        path, signal = build_test_signal_card()
        try:
            self.assertEqual(signal["signal_scope"], "POSITION")
            self.assertEqual(signal["calibration_status"], "TEST")
            with Image.open(path) as image:
                self.assertEqual(image.size, (1920, 2200))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_trade_levels_are_not_repeated_inside_charts_or_lower_summary(self):
        with patch.object(signal_card_v3, "_text", wraps=signal_card_v3._text) as draw_text:
            path, _ = build_test_signal_card()
        try:
            labels = [str(call.args[2]) for call in draw_text.call_args_list if len(call.args) > 2]
            self.assertNotIn("Mum verisi yok", labels)
            self.assertNotIn("TRADE PLANI", labels)
            self.assertNotIn("Giriş", labels)
            self.assertNotIn("Stop", labels)
            self.assertNotIn("ANLIK DURUM", labels)
            self.assertNotIn("CANLI FİYAT", labels)
            self.assertNotIn("RSI · 1G", labels)
            self.assertNotIn("NEDEN SİNYAL?", labels)
            self.assertIn("SİNYAL TEYİTLERİ", labels)
            self.assertEqual(labels.count("SİNYAL GÜCÜ"), 1)
            self.assertFalse(any(label.startswith(("4H DESTEK", "4H DİRENÇ")) for label in labels))
            self.assertFalse(any(label.startswith(("GİRİŞ ", "STOP ", "H1 ")) for label in labels))
            self.assertIn("YARDIMCI BOT TEYİDİ", labels)
            self.assertTrue(any(label.startswith("Bot katkısı yok") for label in labels))
            self.assertFalse(any("BOT TEYİTLİ" in label for label in labels))
            self.assertTrue(any(label.startswith("MOST 4H ·") and "YUKARI" in label for label in labels))
            self.assertTrue(any(label.startswith("MOST 1H ·") and "YUKARI" in label for label in labels))
            self.assertTrue(any(label.startswith("KANAL ·") for label in labels))
            self.assertFalse(any(label.startswith("KANAL · KANAL") for label in labels))
            self.assertFalse(any("EMA20" in label or "EMA50" in label for label in labels))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_bot_depth_support_has_separate_readable_evidence_section(self):
        support = {
            "source": "@borsabilgibot", "evidence": "DERINLIK",
            "depth_price": 61.42, "market_volume": 12_450_000,
            "buy_sell_ratio": 1.87, "buy_pressure_pct": 30.8,
            "valid_depth_levels": 9, "buy_quantity": 1_230_000,
            "buy_orders": 412, "sell_quantity": 650_000,
            "sell_orders": 238, "trade_rows": 18,
            "top_net_buyer": "Vakif", "top_net_buyer_quantity": 245_000,
            "top_net_seller": "QNB", "top_net_seller_quantity": 91_000,
        }
        with patch.object(signal_card_v3, "_text", wraps=signal_card_v3._text) as draw_text, \
                patch.object(signal_card_v3, "_draw_bot_kpi_cards", wraps=signal_card_v3._draw_bot_kpi_cards) as bot_cards:
            path, _ = build_test_signal_card({"symbol": "HRKET.IS", "bot_support": support})
        try:
            labels = [str(call.args[2]) for call in draw_text.call_args_list if len(call.args) > 2]
            self.assertIn("YARDIMCI BOT TEYİDİ", labels)
            self.assertNotIn("DERİNLİK · @borsabilgibot", labels)
            kpis = signal_card_v3._bot_kpis(support)
            self.assertEqual([kpi[0] for kpi in kpis], [
                "TOPLAM ALIŞ", "TOPLAM SATIŞ", "ALIŞ BASKISI", "BASKIN ALICI", "BASKIN SATICI",
            ])
            self.assertEqual([kpi[1] for kpi in kpis], [
                "1.230.000 lot", "650.000 lot", "%30,8", "Vakif", "QNB",
            ])
            self.assertEqual(kpis[3][2], "Net alış +245.000 lot")
            self.assertEqual(kpis[4][2], "Net satış −91.000 lot")
            self.assertEqual(bot_cards.call_count, 1)
            self.assertEqual(bot_cards.call_args.args[1:], (44, 1866, 1832, support))
            self.assertFalse(any(label.startswith("Bot destekli:") for label in labels))
            self.assertFalse(any("BOT TEYİTLİ" in label for label in labels))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_theoretical_bot_support_shows_only_present_fields(self):
        support = {
            "source": "@borsabilgibot", "evidence": "TEORIK",
            "theoretical_price": 61.42, "theoretical_quantity": 125_000,
            "unmatched_side": "BUY", "theoretical_difference_pct": 0.36,
        }
        with patch.object(signal_card_v3, "_text", wraps=signal_card_v3._text) as draw_text:
            path, _ = build_test_signal_card({"bot_support": support})
        try:
            labels = [str(call.args[2]) for call in draw_text.call_args_list if len(call.args) > 2]
            self.assertNotIn("TEORİK EŞLEŞME · @borsabilgibot", labels)
            kpis = signal_card_v3._bot_kpis(support)
            self.assertEqual([kpi[:2] for kpi in kpis], [
                ("TEORİK FİYAT", "61,42 TL"),
                ("EŞLEŞEBİLİR", "125.000 lot"),
                ("KALAN TARAF", "Alış"),
                ("FİYAT FARKI", "%0,36"),
            ])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_rsi_kpi_matches_the_rsi_plotted_from_chart_candles(self):
        with patch.object(signal_card_v3, "_metric", wraps=signal_card_v3._metric) as metrics, \
                patch.object(signal_card_v3, "_text", wraps=signal_card_v3._text) as draw_text:
            path, _ = build_test_signal_card()
        try:
            metric = next(call for call in metrics.call_args_list if call.args[4] == "4H RSI")
            kpi_value = float(metric.args[5])
            labels = [str(call.args[2]) for call in draw_text.call_args_list if len(call.args) > 2]
            self.assertTrue(any(label.startswith(f"4H RSI(14) {kpi_value:.1f} ·") for label in labels))
            self.assertTrue(any(label.startswith("1H RSI(14) ") for label in labels))
            # Fixture metadata intentionally says 57.8; the KPI must follow its
            # chart's actual close series rather than that stale snapshot value.
            self.assertNotEqual(kpi_value, 57.8)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_rsi_series_uses_the_same_seeded_wilder_calculation_as_signals(self):
        x = np.arange(80, dtype=float)
        close = pd.Series(40 + x * 0.09 + np.sin(x / 2.7) * 1.8)
        series_value = float(rsi_wilder_series(close, 14).dropna().iloc[-1])
        signal_value = _rsi_wilder(close, 14)
        self.assertAlmostEqual(series_value, signal_value, places=2)

    def test_position_charts_are_stacked_full_width(self):
        with patch.object(signal_card_v3, "_draw_candle_chart", wraps=signal_card_v3._draw_candle_chart) as chart:
            path, _ = build_test_signal_card()
        try:
            chart_boxes = [call.args[2] for call in chart.call_args_list]
            self.assertEqual(len(chart_boxes), 2)
            self.assertLess(chart_boxes[0][1], chart_boxes[1][1])
            self.assertEqual(chart_boxes[0][0], chart_boxes[1][0])
            self.assertEqual(chart_boxes[0][2], chart_boxes[1][2])
            self.assertTrue(all(call.kwargs.get("show_rsi") for call in chart.call_args_list))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_channel_position_and_triangle_detection_are_data_driven(self):
        channel = {"lower": [90, 90], "middle": [100, 100], "upper": [110, 110]}
        self.assertIn("ALT BANT", signal_card_v3._channel_position(91, channel)[0])
        self.assertIn("ORTA BANT", signal_card_v3._channel_position(100, channel)[0])
        self.assertIn("ÜST BANT", signal_card_v3._channel_position(109, channel)[0])
        converging = {"trend_lines": {"upper": [110, 104], "lower": [90, 96]}}
        self.assertEqual(signal_card_v3._triangle_shape(converging), "SİMETRİK ÜÇGEN")
        diverging = {"trend_lines": {"upper": [110, 115], "lower": [90, 84]}}
        self.assertIsNone(signal_card_v3._triangle_shape(diverging))

    def test_converging_triangle_is_drawn_on_both_timeframes(self):
        geometry = {
            "channel": {"lower": [90, 92], "middle": [100, 102], "upper": [110, 112]},
            "trend_lines": {"upper": [110, 104], "lower": [90, 96]},
            "pattern": {},
        }
        with patch.object(signal_card_v3, "chart_geometry", return_value=geometry), \
                patch.object(signal_card_v3, "_draw_price_path", wraps=signal_card_v3._draw_price_path) as paths, \
                patch.object(signal_card_v3, "_text", wraps=signal_card_v3._text) as draw_text:
            path, _ = build_test_signal_card()
        try:
            labels = [str(call.args[2]) for call in draw_text.call_args_list if len(call.args) > 2]
            self.assertEqual(labels.count("SİMETRİK ÜÇGEN"), 2)
            self.assertIn("ÜÇGEN / TREND SIKIŞMASI", labels)
            self.assertEqual(paths.call_count, 14)  # 3 channel bands + 2 triangle rails + EMA/MOST per chart
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_most_trailing_stop_is_drawn_on_both_price_charts(self):
        with patch.object(signal_card_v3, "_draw_price_path", wraps=signal_card_v3._draw_price_path) as paths:
            path, _ = build_test_signal_card()
        try:
            ema_calls = [call for call in paths.call_args_list if call.args[2] == signal_card_v3.MOST_EMA_COLOR]
            stop_calls = [call for call in paths.call_args_list if call.args[2] == signal_card_v3.MOST_STOP_COLOR]
            self.assertEqual(len(ema_calls), 2)
            self.assertEqual(len(stop_calls), 2)
            self.assertTrue(all(len(call.args[1]) >= 70 for call in ema_calls + stop_calls))
            self.assertTrue(all(call.kwargs.get("width") == 4 for call in ema_calls))
            self.assertTrue(all(call.kwargs.get("width") == 3 for call in stop_calls))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_most_pair_matches_six_period_one_percent_tradingview_settings(self):
        close = pd.Series(40 + np.arange(48, dtype=float) * 0.15 + np.sin(np.arange(48) / 3))
        frame = pd.DataFrame({
            "Open": close - 0.1,
            "High": close + 0.4,
            "Low": close - 0.4,
            "Close": close,
            "Volume": np.full(len(close), 100_000.0),
        })
        lines = signal_card_v3.most_components(frame, period=6, percent=1.0)
        expected_ema = close.ewm(span=6, adjust=False).mean()
        expected_stop = signal_card_v3.most(frame, period=6, percent=1.0)["level"]
        self.assertTrue(np.allclose(lines["ema"].to_numpy(), expected_ema.to_numpy()))
        self.assertAlmostEqual(float(lines["most"].iloc[-1]), expected_stop, places=4)


if __name__ == "__main__":
    unittest.main()
