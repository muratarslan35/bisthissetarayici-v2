import tempfile
import unittest
from pathlib import Path

from scripts.set_external_verify_policy import install


class ExternalVerifyPolicyTests(unittest.TestCase):
    def test_updates_score_and_preserves_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            path.write_text(
                "TELEGRAM_USER_SESSION=secret-session\n"
                "EXTERNAL_VERIFY_MIN_SCORE=88\n"
                "EXTERNAL_VERIFY_DAILY_LIMIT=10\n",
                encoding="utf-8",
            )
            install("95", path)
            value = path.read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_USER_SESSION=secret-session", value)
            self.assertIn("EXTERNAL_VERIFY_MIN_SCORE=95", value)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_less_selective_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                install("94", Path(directory) / "env")


if __name__ == "__main__":
    unittest.main()
