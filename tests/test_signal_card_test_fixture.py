import os
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import signal_card_v3
from signal_card_test_fixture import build_test_signal_card


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
            self.assertEqual(labels.count("SİNYAL GÜCÜ"), 1)
            self.assertFalse(any(label.startswith(("4H DESTEK", "4H DİRENÇ")) for label in labels))
            self.assertFalse(any(label.startswith(("GİRİŞ ", "STOP ", "H1 ")) for label in labels))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_position_charts_are_stacked_full_width(self):
        with patch.object(signal_card_v3, "_draw_candle_chart", wraps=signal_card_v3._draw_candle_chart) as chart:
            path, _ = build_test_signal_card()
        try:
            chart_boxes = [call.args[2] for call in chart.call_args_list]
            self.assertEqual(len(chart_boxes), 2)
            self.assertLess(chart_boxes[0][1], chart_boxes[1][1])
            self.assertEqual(chart_boxes[0][0], chart_boxes[1][0])
            self.assertEqual(chart_boxes[0][2], chart_boxes[1][2])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
