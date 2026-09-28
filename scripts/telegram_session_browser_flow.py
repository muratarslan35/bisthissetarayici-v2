"""Two-stage Telegram user login for GitHub Actions.

The start stage sends a login code and stores the pending MTProto state on the
server. The finish stage consumes the one-time code, validates the account and
atomically installs the StringSession without printing it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path


ENV_PATH = Path(os.environ.get("BIST_ENV_PATH", "/etc/bist-trading.env"))
STATE_PATH = Path(
    os.environ.get("BIST_TELEGRAM_LOGIN_STATE", "/var/lib/bist-trading/telegram-login.json")
)


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".telegram-login.", dir=path.parent, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def update_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
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


def credentials() -> tuple[int, str]:
    values = read_env()
    api_id = values.get("TELEGRAM_API_ID", "")
    api_hash = values.get("TELEGRAM_API_HASH", "")
    if not api_id.isdigit() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise SystemExit("Telegram API credentials are not configured")
    return int(api_id), api_hash


async def start(phone: str) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise SystemExit("TELEGRAM_PHONE must use E.164 format")
    api_id, api_hash = credentials()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        atomic_json(
            STATE_PATH,
            {
                "phone": phone,
                "phone_code_hash": sent.phone_code_hash,
                "session": client.session.save(),
            },
        )
        print("TELEGRAM_LOGIN_CODE_SENT=1")
        print("PENDING_STATE_MODE=600")
    finally:
        await client.disconnect()


async def finish(code: str) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    code = re.sub(r"\s+", "", code)
    if not re.fullmatch(r"[0-9]{4,8}", code):
        raise SystemExit("Invalid Telegram login code")
    if not STATE_PATH.exists():
        raise SystemExit("Pending Telegram login state not found; run start first")

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    api_id, api_hash = credentials()
    client = TelegramClient(StringSession(state["session"]), api_id, api_hash)
    await client.connect()
    try:
        await client.sign_in(
            phone=state["phone"],
            code=code,
            phone_code_hash=state["phone_code_hash"],
        )
        me = await client.get_me()
        if me is None or not getattr(me, "id", None):
            raise SystemExit("Telegram account validation failed")
        target = read_env().get("EXTERNAL_VERIFY_TARGET", "borsabilgibot")
        await client.get_entity(target)
        update_env(
            {
                "TELEGRAM_USER_SESSION": client.session.save(),
                "TELEGRAM_EXPECTED_USER_ID": str(me.id),
                "EXTERNAL_VERIFY_ENABLED": "1",
            }
        )
        STATE_PATH.unlink(missing_ok=True)
        print("TELEGRAM_USER_SESSION_INSTALLED=1")
        print(f"TELEGRAM_EXPECTED_USER_ID={me.id}")
        print("EXTERNAL_VERIFY_ENABLED=1")
        print("TARGET_RESOLUTION=PASS")
    finally:
        await client.disconnect()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"start", "finish"}:
        raise SystemExit("usage: telegram_session_browser_flow.py start|finish")
    secret = sys.stdin.readline().strip()
    asyncio.run(start(secret) if sys.argv[1] == "start" else finish(secret))


if __name__ == "__main__":
    main()
