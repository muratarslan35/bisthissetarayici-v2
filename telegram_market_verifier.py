"""Rate-safe optional verification through an authorised Telegram user session.

This module never uses the project's Telegram bot token to talk to another bot.
Telegram's Bot API does not deliver bot-to-bot messages.  When explicitly
enabled, an authorised MTProto user session is used. At most ten symbols may
enter the pool per Istanbul trading day. Each can receive one opening-auction
query and, only when that is insufficient, one post-open depth query.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_connection


TR_TZ = ZoneInfo("Europe/Istanbul")
SOURCE = "borsabilgibot"
DAILY_HARD_LIMIT = min(10, max(1, int(os.getenv("EXTERNAL_VERIFY_DAILY_LIMIT", "10"))))
MIN_SCORE = float(os.getenv("EXTERNAL_VERIFY_MIN_SCORE", "88"))
ENABLED = os.getenv("EXTERNAL_VERIFY_ENABLED", "0") == "1"
TARGET = os.getenv("EXTERNAL_VERIFY_TARGET", "borsabilgibot").lstrip("@")
EXPECTED_USER_ID = os.getenv("TELEGRAM_EXPECTED_USER_ID", "").strip()
THEORETICAL_COMMAND = os.getenv("EXTERNAL_VERIFY_THEORETICAL_COMMAND", "/teorik {symbol}")
DEPTH_COMMAND = os.getenv("EXTERNAL_VERIFY_DEPTH_COMMAND", "/derinlik {symbol}")
RESPONSE_TIMEOUT = min(45, max(5, int(os.getenv("EXTERNAL_VERIFY_TIMEOUT", "20"))))

_worker_started = False
_worker_lock = threading.Lock()


def _now():
    return datetime.now(TR_TZ)


def _symbol(value):
    return str(value or "").upper().replace("BIST:", "").replace(".IS", "").strip()


def eligible(signal, now=None):
    """Only final, high-conviction candidates may consume the scarce quota."""
    now = now or _now()
    symbol = _symbol(signal.get("symbol"))
    score = float(signal.get("score") or 0)
    scope = str(signal.get("signal_scope") or "")
    if not symbol or score < MIN_SCORE or scope not in {"INTRADAY", "POSITION"}:
        return False
    # Do not spend a query on a candidate without a complete executable plan.
    entry = signal.get("entry_price") or signal.get("price")
    stop = signal.get("stop_loss")
    target = signal.get("tp1")
    risk_pct = float(signal.get("risk_pct") or 999)
    confidence = float(signal.get("data_confidence") or 0)
    rs_percentile = float(signal.get("relative_strength_percentile") or 0)
    if not entry or not stop or not target or confidence < 70 or rs_percentile < 82:
        return False
    if risk_pct <= 0 or risk_pct > (3.5 if scope == "INTRADAY" else 6.5):
        return False
    # Intraday validation is reserved for candidates with actual volume proof.
    if scope == "INTRADAY" and float(signal.get("session_rvol") or 0) < 1.10:
        return False
    minute = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 40 <= minute <= 18 * 60


def reserve(signal, now=None):
    """Atomically reserve one of ten daily symbol slots.

    A failed/timeout request still consumes its symbol slot. New symbols are
    admitted only during the 09:40-10:00 opening-auction window.
    """
    now = now or _now()
    trade_date = now.date().isoformat()
    symbol = _symbol(signal.get("symbol"))
    minute = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 + 40 <= minute < 10 * 60):
        return None, False
    command = THEORETICAL_COMMAND.format(symbol=symbol)
    depth_command = DEPTH_COMMAND.format(symbol=symbol)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM external_market_verifications WHERE trade_date=? AND symbol=?",
            (trade_date, symbol),
        ).fetchone()
        if existing:
            conn.commit()
            return dict(existing), False
        used = conn.execute(
            "SELECT COUNT(*) FROM external_market_verifications WHERE trade_date=?",
            (trade_date,),
        ).fetchone()[0]
        if used >= DAILY_HARD_LIMIT:
            conn.commit()
            return None, False
        cur = conn.execute(
            """INSERT INTO external_market_verifications
               (trade_date, symbol, source, command, status, requested_at, depth_command)
               VALUES (?, ?, ?, ?, 'PENDING', ?, ?)""",
            (trade_date, symbol, SOURCE, command, now.isoformat(), depth_command),
        )
        row = conn.execute(
            "SELECT * FROM external_market_verifications WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        conn.commit()
        return dict(row), True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_result(symbol, now=None):
    now = now or _now()
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM external_market_verifications
               WHERE trade_date=? AND symbol=?""",
            (now.date().isoformat(), _symbol(symbol)),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("parsed_json"):
            result["parsed"] = json.loads(result["parsed_json"])
        if result.get("depth_parsed_json"):
            result["depth_parsed"] = json.loads(result["depth_parsed_json"])
        return result
    finally:
        conn.close()


def quota_status(now=None):
    now = now or _now()
    conn = get_connection()
    try:
        used = conn.execute(
            "SELECT COUNT(*) FROM external_market_verifications WHERE trade_date=?",
            (now.date().isoformat(),),
        ).fetchone()[0]
        theoretical = conn.execute(
            """SELECT COUNT(*) FROM external_market_verifications
               WHERE trade_date=? AND status IS NOT NULL""",
            (now.date().isoformat(),),
        ).fetchone()[0]
        depth = conn.execute(
            """SELECT COUNT(*) FROM external_market_verifications
               WHERE trade_date=? AND depth_status IS NOT NULL""",
            (now.date().isoformat(),),
        ).fetchone()[0]
        return {
            "date": now.date().isoformat(), "symbols": used,
            "symbol_limit": DAILY_HARD_LIMIT, "theoretical_queries": theoretical,
            "depth_queries": depth, "maximum_total_queries": DAILY_HARD_LIMIT * 2,
            "maximum_telegram_actions": DAILY_HARD_LIMIT * 3,
        }
    finally:
        conn.close()


def parse_response(text):
    """Conservative parser: absent fields stay absent; nothing is invented."""
    raw = str(text or "").strip()
    parsed = {"raw_text": raw, "source": SOURCE}
    patterns = {
        "theoretical_price": r"(?:teorik(?:\s+eşleşme)?\s+fiyat[ıi]?)[^0-9]{0,15}([0-9]+(?:[.,][0-9]+)?)",
        "theoretical_quantity": r"(?:eşleş(?:ebilir|me)\s+(?:miktar[ıi]?|adet))[^0-9]{0,15}([0-9][0-9.,]*)",
        "unmatched_quantity": r"(?:kalan\s+(?:emir|miktar))[^0-9]{0,15}([0-9][0-9.,]*)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, raw, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).replace(".", "").replace(",", ".")
        try:
            parsed[key] = float(value)
        except ValueError:
            pass
    lower = raw.lower()
    if "alış fazlas" in lower:
        parsed["unmatched_side"] = "BUY"
    elif "satış fazlas" in lower:
        parsed["unmatched_side"] = "SELL"
    # Full confirmation requires numerical auction evidence. A menu, image link
    # or generic acknowledgement can never be treated as a trading confirmation.
    parsed["confirmation"] = bool(
        parsed.get("theoretical_price") is not None
        and parsed.get("theoretical_quantity") is not None
    )
    return parsed


def parse_depth_response(text):
    """Parse text depth evidence. Images/menus intentionally remain unverified."""
    raw = str(text or "").strip()
    parsed = {"raw_text": raw, "source": SOURCE}
    patterns = {
        "buy_total": r"(?:alış|al[ıi]ş|bid)(?:\s+(?:toplam|miktar[ıi]?))?[^0-9]{0,15}([0-9][0-9.,]*)",
        "sell_total": r"(?:satış|sat[ıi]ş|ask)(?:\s+(?:toplam|miktar[ıi]?))?[^0-9]{0,15}([0-9][0-9.,]*)",
        "buy_sell_ratio": r"(?:alış.?satış|al[ıi]ş.?sat[ıi]ş|a.?s)\s+(?:oran[ıi]?)?[^0-9]{0,15}([0-9]+(?:[.,][0-9]+)?)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, raw, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).replace(".", "").replace(",", ".")
        try:
            parsed[key] = float(value)
        except ValueError:
            pass
    if parsed.get("buy_sell_ratio") is None and parsed.get("sell_total"):
        parsed["buy_sell_ratio"] = parsed.get("buy_total", 0) / parsed["sell_total"]
    parsed["confirmation"] = bool(
        parsed.get("buy_sell_ratio") is not None
        and parsed["buy_sell_ratio"] >= 1.20
        and parsed.get("buy_total", 1) > 0
    )
    return parsed


def _theoretical_supports(signal, parsed):
    price = float(parsed.get("theoretical_price") or 0)
    quantity = float(parsed.get("theoretical_quantity") or 0)
    entry = float(signal.get("entry_price") or signal.get("price") or 0)
    side = parsed.get("unmatched_side")
    return bool(price and quantity > 0 and entry and price >= entry * 0.995 and side != "SELL")


def reserve_depth(symbol, now=None):
    """Reserve the sole conditional depth query for an existing pool symbol."""
    now = now or _now()
    if now.weekday() >= 5 or now.hour < 10 or now.hour >= 18:
        return None, False
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM external_market_verifications
               WHERE trade_date=? AND symbol=?""",
            (now.date().isoformat(), _symbol(symbol)),
        ).fetchone()
        if not row:
            conn.commit()
            return None, False
        if row["depth_status"]:
            conn.commit()
            return dict(row), False
        conn.execute(
            """UPDATE external_market_verifications
               SET depth_status='PENDING', depth_requested_at=? WHERE id=?""",
            (now.isoformat(), row["id"]),
        )
        updated = conn.execute(
            "SELECT * FROM external_market_verifications WHERE id=?", (row["id"],)
        ).fetchone()
        conn.commit()
        return dict(updated), True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _claim_pending():
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM external_market_verifications
               WHERE status='PENDING' ORDER BY id LIMIT 1"""
        ).fetchone()
        kind = "theoretical"
        if not row and _now().hour >= 10:
            row = conn.execute(
                """SELECT * FROM external_market_verifications
                   WHERE depth_status='PENDING' ORDER BY id LIMIT 1"""
            ).fetchone()
            kind = "depth"
        if not row:
            conn.commit()
            return None
        column = "status" if kind == "theoretical" else "depth_status"
        conn.execute(
            f"UPDATE external_market_verifications SET {column}='SENDING' WHERE id=?",
            (row["id"],),
        )
        conn.commit()
        claimed = dict(row)
        claimed["query_kind"] = kind
        return claimed
    finally:
        conn.close()


def _finish(row_id, query_kind, status, response_text=None, parsed=None, error=None):
    conn = get_connection()
    try:
        if query_kind == "depth":
            conn.execute(
                """UPDATE external_market_verifications
                   SET depth_status=?, depth_response_at=?, depth_response_text=?,
                       depth_parsed_json=?, depth_error=? WHERE id=?""",
                (status, _now().isoformat(), response_text,
                 json.dumps(parsed, ensure_ascii=False) if parsed else None, error, row_id),
            )
        else:
            conn.execute(
                """UPDATE external_market_verifications
                   SET status=?, response_at=?, response_text=?, parsed_json=?, error=?
                   WHERE id=?""",
                (status, _now().isoformat(), response_text,
                 json.dumps(parsed, ensure_ascii=False) if parsed else None, error, row_id),
            )
        conn.commit()
    finally:
        conn.close()


async def _send_one(row):
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("telethon is not installed") from exc

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session = os.environ["TELEGRAM_USER_SESSION"]
    client = TelegramClient(StringSession(session), api_id, api_hash)
    async with client:
        me = await client.get_me()
        if EXPECTED_USER_ID and str(me.id) != EXPECTED_USER_ID:
            raise RuntimeError("authorised Telegram account does not match TELEGRAM_EXPECTED_USER_ID")
        async with client.conversation(TARGET, timeout=RESPONSE_TIMEOUT) as conversation:
            command = row["depth_command"] if row.get("query_kind") == "depth" else row["command"]
            await conversation.send_message(command)
            response = await conversation.get_response()
            if row.get("query_kind") != "depth":
                return {"text": response.message or "", "image": None}

            selected = None
            for row_index, button_row in enumerate(response.buttons or []):
                for column_index, button in enumerate(button_row):
                    label = str(getattr(button, "text", "") or "").lower()
                    if "görüntü" in label or "goruntu" in label or "resim" in label:
                        selected = (row_index, column_index)
                        break
                if selected:
                    break
            if selected is None:
                return {"text": response.message or "", "image": None}
            await response.click(*selected)
            image_message = await conversation.get_response()
            image_bytes = None
            if image_message.media:
                image_bytes = await client.download_media(image_message, file=bytes)
            return {"text": image_message.message or response.message or "", "image": image_bytes}


def _worker():
    while True:
        row = _claim_pending()
        if not row:
            threading.Event().wait(2.0)
            continue
        try:
            payload = asyncio.run(_send_one(row))
            text = payload.get("text") or ""
            if row["query_kind"] == "depth":
                image_bytes = payload.get("image")
                if image_bytes:
                    from depth_image_parser import parse_depth_image
                    parsed = parse_depth_image(image_bytes, row.get("symbol"))
                    parsed["image_sha256"] = hashlib.sha256(image_bytes).hexdigest()
                else:
                    parsed = parse_depth_response(text)
            else:
                parsed = parse_response(text)
            status = "DATA_READY" if parsed.get("confirmation") else "INSUFFICIENT_DATA"
            _finish(row["id"], row["query_kind"], status, response_text=text, parsed=parsed)
        except Exception as exc:
            # No automatic retry: each stage is allowed exactly one outbound request.
            _finish(row["id"], row["query_kind"], "FAILED", error=str(exc)[:500])


def start_worker():
    global _worker_started
    if not ENABLED:
        return False
    required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_USER_SESSION")
    if any(not os.getenv(name) for name in required):
        print("EXTERNAL_VERIFY disabled: Telegram user credentials missing", flush=True)
        return False
    with _worker_lock:
        if _worker_started:
            return True
        threading.Thread(target=_worker, daemon=True, name="external-market-verify").start()
        _worker_started = True
    return True


def gate_signal(signal):
    """Hold pool candidates briefly; label only evidence that affects the decision."""
    if not ENABLED or not eligible(signal):
        return signal
    now = _now()
    result = get_result(signal.get("symbol"))
    if not result and now.hour < 10:
        result, _ = reserve(signal, now)
    if not result:
        # Not in the ten-symbol bot pool: this remains a pure system signal.
        return signal
    status = result.get("status")
    theoretical = result.get("parsed") or {}
    if status == "DATA_READY" and _theoretical_supports(signal, theoretical):
        signal["bot_support"] = {
            "source": "@borsabilgibot", "evidence": "TEORIK",
            "theoretical_price": theoretical.get("theoretical_price"),
            "theoretical_quantity": theoretical.get("theoretical_quantity"),
        }
        signal.setdefault("reasons", []).insert(0, "Bot destekli: teorik eşleşme teyidi")
        return signal
    if status in {"PENDING", "SENDING"}:
        return None
    if now.hour < 10:
        return None

    depth_status = result.get("depth_status")
    if not depth_status:
        result, _ = reserve_depth(signal.get("symbol"), now)
        depth_status = result.get("depth_status") if result else None
    if depth_status in {"PENDING", "SENDING"}:
        return None
    depth = (get_result(signal.get("symbol"), now) or {}).get("depth_parsed") or {}
    if depth_status == "DATA_READY" and depth.get("confirmation"):
        ratio = depth.get("buy_sell_ratio")
        if ratio is None:
            ratio = (depth.get("depth_totals") or {}).get("buy_sell_ratio")
        signal["bot_support"] = {
            "source": "@borsabilgibot", "evidence": "DERINLIK",
            "buy_sell_ratio": ratio,
        }
        signal.setdefault("reasons", []).insert(0, "Bot destekli: derinlik teyidi")
        return signal
    # Bot did not contribute usable evidence. Publish as a normal system signal
    # and deliberately omit every bot-related field/message.
    signal.pop("bot_support", None)
    return signal
