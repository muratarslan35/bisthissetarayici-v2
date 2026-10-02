import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import depth_image_parser as parser


class DepthImageParserTests(unittest.TestCase):
    def test_large_regions_use_original_scale_and_configured_timeout(self):
        image = Image.new("RGB", parser.CANVAS, "white")
        captured = {}

        def fake_run(command, **kwargs):
            with Image.open(command[1]) as crop:
                captured["size"] = crop.size
            captured["timeout"] = kwargs["timeout"]
            return subprocess.CompletedProcess(command, 0, "table", "")

        with patch.object(parser.subprocess, "run", side_effect=fake_run):
            self.assertEqual(parser._ocr(image, (0, 150, 765, 725), upscale=False), "table")

        self.assertEqual(captured["size"], (765, 575))
        self.assertEqual(captured["timeout"], parser.OCR_TIMEOUT_SECONDS)
        self.assertGreaterEqual(parser.OCR_TIMEOUT_SECONDS, 10)
        self.assertLessEqual(parser.OCR_TIMEOUT_SECONDS, 45)

    def test_tesseract_timeout_is_reported_with_region_details(self):
        image = Image.new("RGB", parser.CANVAS, "white")
        with patch.object(
            parser.subprocess, "run", side_effect=subprocess.TimeoutExpired("tesseract", 25)
        ):
            with self.assertRaisesRegex(RuntimeError, r"timeout after .*psm=6.*765x575"):
                parser._ocr(image, (0, 150, 765, 725), psm=6, upscale=False)

    def test_image_can_be_opened_from_a_temporary_path(self):
        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        try:
            Image.new("RGB", (300, 200), "white").save(path)
            with patch.object(parser.shutil, "which", return_value="/usr/bin/tesseract"), \
                 patch.object(parser, "_parse_header", return_value={"symbol": "TEST", "last_price": 10}), \
                 patch.object(parser, "_parse_depth", return_value=([], [], [], {}, "")), \
                 patch.object(parser, "_parse_history", return_value=([], "")), \
                 patch.object(parser, "_parse_theoretical", return_value={}):
                parsed = parser.parse_depth_image(path, "TEST")
            self.assertEqual(parsed["source"], "borsabilgibot_image")
            self.assertTrue(parsed["quality"]["symbol_match"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
