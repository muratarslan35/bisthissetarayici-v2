import os
import unittest
from pathlib import Path

from PIL import Image

from signal_card_test_fixture import build_test_signal_card


class SignalCardTestFixtureTests(unittest.TestCase):
    def test_fixture_uses_production_card_dimensions(self):
        path, signal = build_test_signal_card()
        try:
            self.assertEqual(signal["signal_scope"], "POSITION")
            self.assertEqual(signal["calibration_status"], "TEST")
            with Image.open(path) as image:
                self.assertEqual(image.size, (1440, 2420))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
