from pathlib import Path
import unittest

from scripts.install_telegram_api_secrets import install


class InstallTelegramApiSecretsTests(unittest.TestCase):
    def test_install_preserves_unrelated_values_and_disables_verifier(self):
        with self.subTest("atomic update"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                env_path = Path(directory) / "bist.env"
                env_path.write_text(
                    "ADMIN_CHAT_ID=123\nTELEGRAM_API_ID=1\n"
                    "EXTERNAL_VERIFY_ENABLED=1\n",
                    encoding="utf-8",
                )

                install(
                    "30147765",
                    "0123456789abcdef0123456789abcdef",
                    env_path,
                )

                content = env_path.read_text(encoding="utf-8")
                self.assertIn("ADMIN_CHAT_ID=123", content)
                self.assertIn("TELEGRAM_API_ID=30147765", content)
                self.assertIn(
                    "TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef",
                    content,
                )
                self.assertIn("EXTERNAL_VERIFY_ENABLED=0", content)
                self.assertIn("EXTERNAL_VERIFY_DAILY_LIMIT=10", content)
                self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

    def test_install_rejects_invalid_credentials(self):
        from tempfile import TemporaryDirectory

        cases = (
            ("not-a-number", "0123456789abcdef0123456789abcdef"),
            ("30147765", "too-short"),
        )
        with TemporaryDirectory() as directory:
            for index, (api_id, api_hash) in enumerate(cases):
                with self.subTest(api_id=api_id, api_hash=api_hash):
                    with self.assertRaises(ValueError):
                        install(api_id, api_hash, Path(directory) / f"{index}.env")


if __name__ == "__main__":
    unittest.main()
