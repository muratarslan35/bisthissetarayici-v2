"""Create the MTProto user session used by external market verification.

Run this interactively on the BIST server.  Telegram sends the login code to
the account owner; the code and optional 2FA password are entered directly in
the terminal and are never stored by this project.
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


async def main():
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        raise SystemExit("Önce bağımlılıkları kurun: pip install -r requirements.txt")

    api_id = os.getenv("TELEGRAM_API_ID") or input("Telegram API ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH") or getpass.getpass("Telegram API hash: ").strip()
    phone = input("Telegram telefon numarası (+90...): ").strip()
    if not api_id or not api_hash or not phone:
        raise SystemExit("API ID, API hash ve telefon numarası zorunludur.")

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.connect()
    try:
        await client.send_code_request(phone)
        code = getpass.getpass("Telegram doğrulama kodu: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except Exception as exc:
            if "password" not in type(exc).__name__.lower():
                raise
            password = getpass.getpass("Telegram iki aşamalı doğrulama parolası: ")
            await client.sign_in(password=password)

        me = await client.get_me()
        session = client.session.save()
        print(f"\nYetkilendirilen kullanıcı: {me.first_name} (id={me.id})")
        print("Aşağıdaki değeri sunucunun gizli ortam değişkenine ekleyin:")
        print(f"TELEGRAM_USER_SESSION={session}")
        print("Bu değer hesabınız adına oturum açabilir; GitHub'a veya mesaja göndermeyin.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
