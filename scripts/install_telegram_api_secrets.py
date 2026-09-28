"""Atomically install Telegram API credentials into the BIST service env.

The API id and hash are read from stdin so neither value appears in the
process list or command line. This script is intended to run as root on the
dedicated BIST VM.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path


ENV_PATH = Path(os.environ.get("BIST_ENV_PATH", "/etc/bist-trading.env"))


def install(api_id: str, api_hash: str, path: Path = ENV_PATH) -> None:
    if not re.fullmatch(r"[0-9]+", api_id):
        raise ValueError("invalid TELEGRAM_API_ID")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise ValueError("invalid TELEGRAM_API_HASH")

    updates = {
        "TELEGRAM_API_ID": api_id,
        "TELEGRAM_API_HASH": api_hash,
        # Activation is a separate interactive user-session step.
        "EXTERNAL_VERIFY_ENABLED": "0",
        "EXTERNAL_VERIFY_DAILY_LIMIT": "10",
        "EXTERNAL_VERIFY_MIN_SCORE": "95",
        "EXTERNAL_VERIFY_TARGET": "borsabilgibot",
    }

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".bist-trading.", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    api_id = sys.stdin.readline().strip()
    api_hash = sys.stdin.readline().strip()
    install(api_id, api_hash)
    print("TELEGRAM_API_INSTALL=PASS")
    print("EXTERNAL_VERIFY_ENABLED=0")


if __name__ == "__main__":
    main()
