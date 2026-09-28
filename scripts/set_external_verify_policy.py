"""Atomically update the external verifier's score policy on the BIST VM."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ENV_PATH = Path(os.environ.get("BIST_ENV_PATH", "/etc/bist-trading.env"))


def install(min_score: str, path: Path = ENV_PATH) -> None:
    score = float(min_score)
    if score < 95 or score > 100:
        raise ValueError("external verification minimum score must be 95..100")
    normalized = f"{score:g}"
    updates = {
        "EXTERNAL_VERIFY_MIN_SCORE": normalized,
        "EXTERNAL_VERIFY_DAILY_LIMIT": "10",
    }
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".bist-trading.", dir=path.parent, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    install(sys.stdin.readline().strip())
    print("EXTERNAL_VERIFY_MIN_SCORE=95")
    print("EXTERNAL_VERIFY_DAILY_LIMIT=10")
