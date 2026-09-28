from pathlib import Path

import pytest

from scripts.install_telegram_api_secrets import install


def test_install_preserves_unrelated_values_and_disables_verifier(tmp_path: Path):
    env_path = tmp_path / "bist.env"
    env_path.write_text(
        "ADMIN_CHAT_ID=123\nTELEGRAM_API_ID=1\nEXTERNAL_VERIFY_ENABLED=1\n",
        encoding="utf-8",
    )

    install("30147765", "0123456789abcdef0123456789abcdef", env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "ADMIN_CHAT_ID=123" in content
    assert "TELEGRAM_API_ID=30147765" in content
    assert "TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef" in content
    assert "EXTERNAL_VERIFY_ENABLED=0" in content
    assert "EXTERNAL_VERIFY_DAILY_LIMIT=10" in content
    assert env_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("api_id", "api_hash"),
    [
        ("not-a-number", "0123456789abcdef0123456789abcdef"),
        ("30147765", "too-short"),
    ],
)
def test_install_rejects_invalid_credentials(tmp_path: Path, api_id: str, api_hash: str):
    with pytest.raises(ValueError):
        install(api_id, api_hash, tmp_path / "bist.env")
