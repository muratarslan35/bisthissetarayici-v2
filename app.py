import os
import secrets
import time
import threading
import json
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
from functools import wraps
import random
import string
import shutil
from pathlib import Path
from urllib.parse import urlencode
import pandas as pd

from ultra_price_engine import start_engine
from ultra_price_engine import get_price
from volume_engine import load_volume_cache, save_volume_cache
from volume_engine import get_rvol
from volume_engine import update_tick


from dotenv import load_dotenv
from flask import (
    Flask, jsonify, render_template,
    request, session, redirect, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import init_db, get_connection

from fetch_bist import fetch_bist_data
from market_data_hub import fetch_market_snapshot, fetch_final_close_prices
from fast_market_lane import (
    FAST_POLL_SECONDS,
    fetch_fast_quotes,
    is_early_mover,
    overlay_quote,
)
from universe_manager import (
    get_active_universe,
    get_discovered_universe,
    get_fast_watchlist,
    get_symbol_metrics,
    refresh_universe,
    universe_status,
)
from strategy_v3 import (
    build_market_context,
    evaluate_position_signals,
    evaluate_intraday_signals,
    evaluate_fast_entry_signal,
    format_v3_signal_message,
)
from trade_ledger import (
    init_trade_ledger,
    record_signal,
    update_open_trades,
    get_open_trade_symbols,
    persist_position_close_prices,
    format_trade_event,
    build_v4_daily_report,
    build_v4_weekly_report,
)
from signal_routing import (
    BOT_SUBSCRIBERS,
    TELEGRAM_CHANNEL,
    enrich_routing,
    route_trade_event,
)
from dashboard_store import (
    init_dashboard_store,
    update_worker_heartbeat,
    persist_market_snapshot,
    get_latest_position_signal_payload,
)
from resource_guard import host_pressure_state
from signal_policy import select_publishable_candidates, policy_limits
from signal_freshness import prepare_fresh_candidates, validate_execution_quote
from telegram_market_verifier import gate_signal as external_validation_gate
from signal_state import (
    init_signal_state,
    assess_signal_transition,
    mark_signal_state_closed,
    get_signal_state,
)
from signal_card_v3 import build_signal_card
from signal_card_test_fixture import build_test_signal_card
from professional_technical_engine import analyze_position_structure
from signal_engine import (
    process_symbol_signals,
    update_success_targets,
    format_signal_message,
    build_daily_success_report,
    build_weekly_success_report,
    reset_daily_success_if_needed,
    reset_weekly_success_if_needed,
)

from dashboard import (
    dashboard_bp,
    push_signal,
    push_success_signal
)
from kap_monitor import check_kap, load_recent_kap_cache, kap_health
from kap_volume_signal import detect_kap_volume_momentum

from utils import FALLBACK_SYMBOLS, get_last_resistance
from utils import compute_sr
import dashboard
from bist_market_filters import get_brut_list
from brut_tracker import (
    detect_new_bruts,
    build_daily_message,
    build_new_message,
    safe_get_brut
)
from momentum_card import build_momentum_card
from candle_engine import get_15m_df, get_1h_df

ADMIN_SIGNAL_PREVIEW = Path("cards/admin/latest_position_signal.png")


def archive_admin_signal_preview(image_path):
    """Atomically preserve the exact PNG sent by the POSITION bot."""
    source = Path(image_path)
    if not source.exists():
        return None
    ADMIN_SIGNAL_PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    temporary = ADMIN_SIGNAL_PREVIEW.with_suffix(f".{uuid4().hex}.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, ADMIN_SIGNAL_PREVIEW)
    return str(ADMIN_SIGNAL_PREVIEW)

# ======================================================
# ENV
# ======================================================

load_dotenv()
ADMIN_PANEL_PATH = os.getenv("ADMIN_PANEL_PATH", "admin-hidden")

# ======================================================
# 🔥 ENGINE SYMBOLS (MERKEZİ)
# ======================================================
# Keep universe order deterministic across restarts.
ENGINE_SYMBOLS = list(dict.fromkeys(FALLBACK_SYMBOLS))

TRADING_V3_ENABLED = os.getenv("TRADING_V3_ENABLED", "1") == "1"
# Disabled by default in V3: automated TradingView polling is not required.
ENABLE_ULTRA_PRICE_ENGINE = os.getenv("ENABLE_ULTRA_PRICE_ENGINE", "0") == "1"

# ======================================================
# TIME
# ======================================================

TR_TZ = ZoneInfo("Europe/Istanbul")
BIST_OPEN = dtime(9, 40)
BIST_CLOSE = dtime(18, 10)
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "3"))

# ======================================================
# TELEGRAM
# ======================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
REPORT_CHAT_IDS = os.getenv("REPORT_CHAT_IDS", "")

REPORT_CHAT_IDS = [
    int(x.strip())
    for x in REPORT_CHAT_IDS.split(",")
    if x.strip()
]

# ======================================================
# FLASK
# ======================================================

app = Flask(__name__)
_app_env = os.getenv("APP_ENV", "development").lower()
_secret_key = os.getenv("SECRET_KEY")
if _app_env == "production" and not _secret_key:
    raise RuntimeError("SECRET_KEY is required in production")
app.secret_key = _secret_key or secrets.token_urlsafe(48)
app.register_blueprint(dashboard_bp)

init_db()
init_trade_ledger()
init_signal_state()
init_dashboard_store()

# ======================================================
# SHARED WORKER RUNTIME
# ======================================================

_RUNTIME_LOCK = threading.RLock()
_TRADE_UPDATE_LOCK = threading.RLock()
_SIGNAL_PUBLISH_LOCK = threading.RLock()
_LATEST_MARKET_BY_SYMBOL = {}
_LATEST_MARKET_CONTEXT = {}
_KAP_RUNTIME_CACHE = load_recent_kap_cache(minutes=1080)
_KAP_RUNTIME_VERSION = 0

def publish_market_runtime(market_data, context):
    global _LATEST_MARKET_BY_SYMBOL, _LATEST_MARKET_CONTEXT
    with _RUNTIME_LOCK:
        _LATEST_MARKET_BY_SYMBOL = {
            item.get("symbol"): item
            for item in (market_data or [])
            if item.get("symbol")
        }
        _LATEST_MARKET_CONTEXT = dict(context or {})

def market_runtime_snapshot():
    with _RUNTIME_LOCK:
        return dict(_LATEST_MARKET_BY_SYMBOL), dict(_LATEST_MARKET_CONTEXT)

def safe_update_open_trades(symbol, price):
    with _TRADE_UPDATE_LOCK:
        return update_open_trades(symbol, price)

def update_kap_runtime(new_events):
    global _KAP_RUNTIME_VERSION
    if not new_events:
        return _KAP_RUNTIME_VERSION
    with _RUNTIME_LOCK:
        _KAP_RUNTIME_CACHE.update(new_events)
        if len(_KAP_RUNTIME_CACHE) > 700:
            newest = list(_KAP_RUNTIME_CACHE.items())[-450:]
            _KAP_RUNTIME_CACHE.clear()
            _KAP_RUNTIME_CACHE.update(newest)
        _KAP_RUNTIME_VERSION += 1
        return _KAP_RUNTIME_VERSION

def kap_runtime_snapshot():
    with _RUNTIME_LOCK:
        return dict(_KAP_RUNTIME_CACHE), int(_KAP_RUNTIME_VERSION)

# ======================================================
# GLOBAL TRADE TRACK
# ======================================================
RVOL_CACHE = {}
ACTIVE_TRADES = {}
LAST_SEND_TIME = 0
# ======================================================
# 📊 MOMENTUM TRACKING
# ======================================================

MOMENTUM_TRADES = {}

# ======================================================
# 🔥 BRUT SAFE REFRESH (5 DK CACHE)
# ======================================================

LAST_BRUT_REFRESH = 0

def safe_refresh_brut():

    global LAST_BRUT_REFRESH

    now_ts = time.time()

    # 5 dakikada 1 güncelle
    if now_ts - LAST_BRUT_REFRESH > 300:

        try:
            refresh_brut_list()
            LAST_BRUT_REFRESH = now_ts
            print("✅ BRUT REFRESH OK")

        except Exception as e:
            print("❌ BRUT REFRESH ERROR:", e)
# ======================================================
# RVOL ENGINE
# ======================================================

def rvol_updater():

    while True:
        try:
            for symbol in ENGINE_SYMBOLS:

                r = get_rvol(symbol)

                if r is not None:
                    RVOL_CACHE[symbol] = r

                time.sleep(0.05)  # BAN yememek için kritik

        except Exception as e:
            print("RVOL ENGINE ERROR:", e)

        time.sleep(1)
# ======================================================
# HELPERS
# ======================================================

def now_tr():
    return datetime.now(TR_TZ)

def is_market_open(now=None):
    now = now or now_tr()
    if now.weekday() >= 5:
        return False
    return BIST_OPEN <= now.time() <= BIST_CLOSE

def subscription_valid(user_row):
    if not user_row or not user_row["subscription_end"]:
        return False
    try:
        end = datetime.strptime(user_row["subscription_end"], "%Y-%m-%d %H:%M:%S")
        return end > datetime.now()
    except:
        return False

# ======================================================
# HELPERS
# ======================================================

def send_user_telegram(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    import requests
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=5
        )
    except Exception as e:
        print("Telegram send error:", e)

# ======================================================
# 📸 TELEGRAM PHOTO
# ======================================================

def send_photo(chat_id, image_path, caption=None):
    import requests

    global LAST_SEND_TIME

    if not TELEGRAM_TOKEN or not chat_id or not image_path:
        return False

    try:
        now = time.time()
        if now - LAST_SEND_TIME < 0.4:
            time.sleep(0.4)

        LAST_SEND_TIME = time.time()
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

        with open(image_path, "rb") as f:
            response = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption or "",
                    "parse_mode": "HTML",
                },
                files={"photo": f},
                timeout=15,
            )
        data = response.json() if response.content else {}
        if not response.ok or not data.get("ok"):
            print("Photo send rejected:", response.status_code, data, flush=True)
            return False
        return True
    except Exception as e:
        print("Photo send error:", e, flush=True)
        return False

def send_report_to_admins(text):
    for cid in REPORT_CHAT_IDS:
        try:
            send_user_telegram(cid, text)
        except Exception as e:
            print(f"Report send error ({cid}):", e)

def send_to_channel(text):
    if not TELEGRAM_TOKEN or not CHANNEL_ID:
        return False
    import requests
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": CHANNEL_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=5
        )
        data = response.json() if response.content else {}
        return bool(response.ok and data.get("ok"))
    except Exception as e:
        print("Channel send error:", e, flush=True)
        return False

def broadcast_signal(msg):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT telegram_chat_id
        FROM users
        WHERE is_active = 1
        AND telegram_chat_id IS NOT NULL
    """)

    users = cur.fetchall()
    conn.close()

    for u in users:
        try:
            cid = int(u["telegram_chat_id"])

            send_user_telegram(cid, msg)

            time.sleep(0.05)  # 🚦 rate limit (çok kritik)

        except Exception as e:
            print(f"Broadcast error ({cid}):", e)

def broadcast_signal_photo(image_path, caption):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT telegram_chat_id
        FROM users
        WHERE is_active = 1
          AND telegram_chat_id IS NOT NULL
    """)
    users = cur.fetchall()
    conn.close()

    sent = 0
    for row in users:
        try:
            cid = int(row["telegram_chat_id"])
            if send_photo(cid, image_path, caption):
                sent += 1
            time.sleep(0.05)
        except Exception as exc:
            print(f"Broadcast photo error ({row['telegram_chat_id']}): {exc}", flush=True)
    return sent


def _upgrade_caption(signal, transition):
    symbol = str(signal.get("symbol") or "").replace(".IS", "")
    stage = transition.get("stage") or "GÜÇLÜ"
    prev = transition.get("previous_score")
    score = signal.get("score")
    price = signal.get("current_price") or signal.get("entry_price")
    return (
        f"🔥 <b>{symbol} · GÜÇLENEN SİNYAL</b>\n"
        f"Seviye: <b>{stage}</b> · Güç {prev} → <b>{score}/100</b>\n"
        f"Canlı fiyat: <b>{price}</b>\n"
        f"Mevcut işlem takibi korunuyor; yeni işlem açılmadı."
    )


def _refresh_intraday_delivery_quote(signal, stage):
    """Fail closed unless a fresh, executable quote still validates this entry."""
    started = time.monotonic()
    symbol = str(signal.get("symbol") or "")
    quotes = fetch_fast_quotes([symbol]) if symbol else {}
    quote = (quotes or {}).get(symbol) or {}
    checked_at = time.time()
    prepared, reason = validate_execution_quote(
        signal,
        quote,
        now=checked_at,
        max_quote_age_seconds=10.0,
    )
    quote_latency = round(max(0.0, time.monotonic() - started), 3)

    if prepared is None:
        try:
            trigger = float(signal.get("trigger_price") or signal.get("entry_price") or 0.0)
            current = float(quote.get("price") or 0.0)
            slippage = ((current / trigger) - 1.0) * 100.0 if trigger and current else None
        except (TypeError, ValueError, ZeroDivisionError):
            trigger, current, slippage = 0.0, 0.0, None
        observed_at = float(quote.get("observed_at") or 0.0)
        quote_age = max(0.0, checked_at - observed_at) if observed_at else None
        generated_at = signal.get("generated_at")
        decision_age = None
        if generated_at:
            try:
                decision_age = max(
                    0.0,
                    checked_at - datetime.fromisoformat(str(generated_at)).timestamp(),
                )
            except Exception:
                pass
        print(
            "SIGNAL_DELIVERY_REJECT "
            f"symbol={symbol} scope=INTRADAY stage={stage} reason={reason} "
            f"trigger_price={trigger or None} current_price={current or None} "
            f"slippage_pct={round(slippage, 3) if slippage is not None else None} "
            f"quote_age_seconds={round(quote_age, 2) if quote_age is not None else None} "
            f"decision_age_seconds={round(decision_age, 2) if decision_age is not None else None} "
            f"quote_fetch_seconds={quote_latency}",
            flush=True,
        )
        return None

    prepared["last_mile_quote_fetch_seconds"] = quote_latency
    prepared["last_mile_checked_at"] = checked_at
    return prepared


def publish_v5_signal(signal, item):
    gate_started = time.monotonic()
    signal = external_validation_gate(signal)
    gate_latency = round(max(0.0, time.monotonic() - gate_started), 3)
    if signal is None:
        return False

    signal = enrich_routing(signal)
    transition = assess_signal_transition(signal)
    action = transition.get("action")
    if action == "NONE":
        return False

    signal["signal_stage"] = transition.get("stage")
    state = get_signal_state(signal.get("symbol"), signal.get("signal_scope")) or transition
    is_intraday = signal.get("signal_scope") == "INTRADAY"

    # Refresh after external verification and immediately before rendering. This
    # catches a move which happened while the gate or the scanner was working.
    if is_intraday:
        signal = _refresh_intraday_delivery_quote(signal, "pre_card")
        if signal is None:
            return False

    card_item = dict(item or {})
    if is_intraday:
        card_item["current_price"] = signal.get("current_price")
        card_item["fast_quote"] = {
            "price": signal.get("current_price"),
            "observed_at": signal.get("execution_observed_at"),
            "source": signal.get("execution_source"),
        }

    image_path = None
    card_started = time.monotonic()
    try:
        image_path = build_signal_card(signal, card_item, state=state)
        if image_path and signal.get("signal_scope") == "POSITION":
            archive_admin_signal_preview(image_path)
    except Exception as exc:
        print("V5 CARD ERROR:", exc, flush=True)
    card_latency = round(max(0.0, time.monotonic() - card_started), 3)

    try:
        # Last-mile quote is intentionally after chart rendering and directly
        # before persistence/delivery. Never publish an entry already left behind.
        if is_intraday:
            signal = _refresh_intraday_delivery_quote(signal, "pre_send")
            if signal is None:
                return False

        caption = (
            format_v3_signal_message(signal)
            if action == "NEW"
            else _upgrade_caption(signal, transition)
        )
        signal["external_validation_latency_seconds"] = gate_latency
        signal["card_render_latency_seconds"] = card_latency
        signal["publish_latency_seconds"] = round(
            max(0.0, time.time() - float(signal.get("execution_observed_at") or time.time())),
            2,
        )

        # Do not let a rejected stale candidate appear as a published signal in
        # the ledger or dashboard. Persist only after the final price check.
        if action == "NEW":
            if not record_signal(signal):
                return False
            push_signal(signal)

        delivery_started = time.monotonic()
        delivered = False
        destination = signal.get("delivery_destination")
        if destination == BOT_SUBSCRIBERS:
            if image_path and os.path.exists(image_path):
                delivered = broadcast_signal_photo(image_path, caption) > 0
                if not delivered:
                    broadcast_signal(caption)
                    delivered = True
            else:
                broadcast_signal(caption)
                delivered = True
        elif destination == TELEGRAM_CHANNEL:
            if image_path and os.path.exists(image_path):
                delivered = send_photo(CHANNEL_ID, image_path, caption)
                if not delivered:
                    delivered = bool(send_to_channel(caption))
            else:
                delivered = bool(send_to_channel(caption))
        delivery_latency = round(max(0.0, time.monotonic() - delivery_started), 3)
        signal_to_delivery = None
        if is_intraday and signal.get("generated_at"):
            try:
                signal_to_delivery = round(
                    max(0.0, time.time() - datetime.fromisoformat(
                        str(signal["generated_at"])
                    ).timestamp()),
                    2,
                )
            except Exception:
                pass

        print(
            "V5_SIGNAL_DELIVERY "
            f"action={action} scope={signal.get('signal_scope')} "
            f"symbol={signal.get('symbol')} delivered={delivered} "
            f"trigger_price={signal.get('trigger_price') or signal.get('entry_price')} "
            f"checked_price={signal.get('current_price')} "
            f"slippage_pct={signal.get('execution_slippage_pct')} "
            f"quote_age_seconds={signal.get('execution_quote_age_seconds')} "
            f"decision_latency_seconds={signal.get('decision_latency_seconds')} "
            f"gate_seconds={gate_latency} card_seconds={card_latency} "
            f"quote_fetch_seconds={signal.get('last_mile_quote_fetch_seconds')} "
            f"delivery_seconds={delivery_latency} "
            f"signal_to_delivery_seconds={signal_to_delivery}",
            flush=True,
        )
        return delivered
    finally:
        if image_path:
            try:
                os.remove(image_path)
            except Exception:
                pass


# ======================================================
# 🚀 MOMENTUM SIGNAL (IMAGE + CHART)
# ======================================================

def send_momentum_signal(data):

    try:
        symbol = data.get("symbol")
        entry = data.get("entry_price")
        score = data.get("score")
        quality = data.get("quality")
        entry_type = data.get("entry_type")
        live_price = data.get("live_price")
        momentum = data.get("momentum_pct")
        vwap_dist = data.get("vwap_distance")

        # 🔥 GRAFİK DATA
        df15 = data.get("df15")

        # SAFE FORMAT
        entry = round(entry, 2) if entry is not None else 0
        live_price = round(live_price, 2) if live_price is not None else 0
        momentum = round(momentum, 2) if momentum is not None else 0
        vwap_dist = round(vwap_dist, 2) if vwap_dist is not None else 0

        # 🎯 CARD DATA
        card_data = {
            "symbol": symbol,
            "entry": entry,
            "live_price": live_price,
            "type": entry_type,
            "score": f"{score} ({quality})",
            "momentum": momentum,
            "vwap": vwap_dist,
            "df15": data.get("df15"),
            "df1h": data.get("df1h"),
            "support": data.get("support"),
            "resistance": data.get("resistance"),
            "trend": data.get("trend"),
            "tp1": entry * 1.01
        }

        # --------------------------------------------------
        # 🖼 KART OLUŞTUR
        # --------------------------------------------------

        img_path = build_momentum_card(card_data)

        # ❌ KART OLUŞMADI → FULL FALLBACK
        if not img_path or not os.path.exists(img_path):

            fallback_msg = f"""
🚀 MOMENTUM DETAY

📊 {symbol}
💰 Giriş: {entry}
📡 Anlık: {live_price}

⚡ Tür: {entry_type}
📊 Skor: {score} ({quality})

📈 Momentum: %{momentum}
📉 VWAP: %{vwap_dist}

🧠 Yorum:
- Momentum: {"Güçlü" if momentum > 1.2 else "Orta" if momentum > 0.7 else "Zayıf"}
- VWAP: {"Uzak" if vwap_dist > 1.5 else "Sağlıklı"}

🕒 {datetime.now().strftime('%H:%M:%S')}
"""

            send_to_channel(fallback_msg)
            return

        # --------------------------------------------------
        # 🚀 GÖNDER (GÖRSEL)
        # --------------------------------------------------

        caption = f"{symbol} | {entry_type} | {score}"
        send_photo(CHANNEL_ID, img_path, caption)

        # 🧹 TEMİZLE
        try:
            os.remove(img_path)
        except:
            pass

    except Exception as e:
        print("MOMENTUM CARD ERROR:", e)

        # 🔥 FULL SAFE FALLBACK (BURASI EN KRİTİK)
        try:
            symbol = data.get("symbol", "-")
            entry = data.get("entry_price", 0)
            entry_type = data.get("entry_type", "-")
            score = data.get("score", "-")
            quality = data.get("quality", "-")
            live_price = data.get("live_price", 0)
            momentum = data.get("momentum_pct", 0)
            vwap_dist = data.get("vwap_distance", 0)

            fallback_msg = f"""
🚀 MOMENTUM (HATA SONRASI)

📊 {symbol}
💰 Giriş: {entry}
📡 Anlık: {live_price}

⚡ Tür: {entry_type}
📊 Skor: {score} ({quality})

📈 Momentum: %{momentum}
📉 VWAP: %{vwap_dist}

🕒 {datetime.now().strftime('%H:%M:%S')}
"""

            send_to_channel(fallback_msg)

        except Exception as e2:
            print("FALLBACK ERROR:", e2)
# ======================================================
# BRUT TAKAS RAPORU (PRO)
# ======================================================

def build_brut_report():

    brut_map = get_brut_list()

    if not brut_map:
        return None

    lines = []

    lines.append("⚠️ BUGÜN BRÜT TAKAS OLAN HİSSELER")
    lines.append("")

    # 🔥 GÜNE GÖRE SIRALA (EN YAKIN BİTEN ÜSTTE)
    sorted_items = sorted(
        brut_map.items(),
        key=lambda x: (x[1].get("days_left") if x[1].get("days_left") is not None else 999)
    )

    for symbol, data in sorted_items:

        days = data.get("days_left")

        if days is None:
            continue

        sym = symbol.replace(".IS", "")

        # 🔥 PROFESYONEL GÖSTERİM
        if days == 0:
            lines.append(f"🔴 {sym} → SON GÜN")
        elif days == 1:
            lines.append(f"🟠 {sym} → 1 gün kaldı")
        else:
            lines.append(f"• {sym} → {days} gün kaldı")

    lines.append("")
    lines.append(f"🕒 {now_tr().strftime('%H:%M')}")

    return "\n".join(lines)
# ======================================================
# STARTUP MESSAGE
# ======================================================

def send_startup_message():

    if not ADMIN_CHAT_ID:
        return

    msg = (
        "🟢 <b> Bot Başlatıldı</b>\n"
        f"🕒 {now_tr().strftime('%H:%M:%S')} | {now_tr().strftime('%d.%m.%Y')}"
    )

    send_user_telegram(ADMIN_CHAT_ID, msg)

# ======================================================
# 📊 MOMENTUM DAILY REPORT
# ======================================================

def build_momentum_daily_report():

    if not MOMENTUM_TRADES:
        return None

    total = len(MOMENTUM_TRADES)
    success = 0
    fail = 0
    active = 0

    lines = []
    lines.append("🚀 MOMENTUM GÜN SONU RAPORU\n")

    for symbol, t in MOMENTUM_TRADES.items():

        status = t.get("status")

        if status == "success":
            success += 1
            emoji = "✅"
        elif status == "fail":
            fail += 1
            emoji = "❌"
        else:
            active += 1
            emoji = "⏳"

        entry = t.get("entry")
        exit_price = t.get("exit", "-")

        lines.append(f"{emoji} {symbol} | Giriş: {entry} → {exit_price}")

    lines.append("\n📊 ÖZET")
    lines.append(f"Toplam: {total}")
    lines.append(f"✅ Başarılı: {success}")
    lines.append(f"❌ Başarısız: {fail}")
    lines.append(f"⏳ Açık: {active}")

    success_rate = round((success / total) * 100, 2) if total > 0 else 0
    lines.append(f"\n📈 Başarı Oranı: %{success_rate}")

    lines.append(f"\n🕒 {now_tr().strftime('%H:%M')}")

    return "\n".join(lines)
# ======================================================
# 🔄 MOMENTUM RESET
# ======================================================

def reset_momentum_if_needed(last_reset_date, now):

    if last_reset_date != now.date() and now.time() > BIST_CLOSE:
        MOMENTUM_TRADES.clear()
        return now.date()

    return last_reset_date
# ======================================================
# INVITE CODE
# ======================================================

def validate_invite_code(code):
    if not code:
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, is_used, expires_at FROM invite_codes WHERE code=?", (code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    if row["is_used"] == 1:
        conn.close()
        return False
    if row["expires_at"]:
        expire_time = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if expire_time < datetime.now():
            conn.close()
            return False
    conn.close()
    return True

def mark_invite_code_used(code, username):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE invite_codes
        SET is_used=1, used_by=?
        WHERE code=?
    """, (username, code))
    conn.commit()
    conn.close()

# ======================================================
# SESSION
# ======================================================

def register_session(username):
    sid = str(uuid4())
    session["sid"] = sid
    session["user"] = username

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET active_session_id=?, last_login_at=? WHERE username=?",
        (sid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username)
    )
    conn.commit()
    conn.close()

def session_valid(username):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT active_session_id FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return row["active_session_id"] == session.get("sid")

def is_admin_user(username):
    if not username:
        return False
    if username == "admin":
        return True
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_admin FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["is_admin"])


# ======================================================
# SECURITY
# ======================================================

@app.before_request
def security():
    if request.path.startswith("/static"):
        return

    if request.path in ("/login", "/register"):
        return

    if request.path == "/health":
        return

    if request.path.startswith(f"/{ADMIN_PANEL_PATH}") or request.path.startswith("/admin"):
        if "user" not in session:
            return redirect("/login?" + urlencode({"next": request.full_path.rstrip("?")}))
        if not is_admin_user(session.get("user")):
            return "Unauthorized", 403
        return
    if "user" not in session:
        return redirect("/login")
    if not session_valid(session["user"]):
        session.clear()
        return redirect("/login")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (session["user"],))
    user = cur.fetchone()
    conn.close()
    if not user or not user["is_active"] or not subscription_valid(user):
        session.clear()
        return redirect("/login")

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin_user(session.get("user")):
            return "Unauthorized", 403
        return f(*args, **kwargs)
    return wrapper

# ======================================================
# AUTH
# ======================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    def safe_next(value):
        value = str(value or "")
        return value if value.startswith("/") and not value.startswith("//") else "/"

    if request.method == "GET":
        return render_template("login.html", next_url=safe_next(request.args.get("next")))
    username = request.form.get("username")
    password = request.form.get("password")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (username,))
    user = cur.fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        conn.close()
        return "Login failed", 401
    register_session(username)
    conn.close()
    return redirect(safe_next(request.form.get("next")))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
    username = request.form.get("username")
    password = request.form.get("password")
    invite_code = request.form.get("invite_code")
    if not validate_invite_code(invite_code):
        return "Invalid or expired invite code", 400
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, subscription_end, status) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), None, "pending")
        )
        conn.commit()
        mark_invite_code_used(invite_code, username)
    except:
        conn.close()
        return "Username exists", 400
    conn.close()
    return redirect("/login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ======================================================
# INDEX
# ======================================================

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/health")
def health():
    kap = kap_health()
    return jsonify({
        "market_open": is_market_open(),
        "server_time": now_tr().strftime("%H:%M:%S"),
        "kap_status": kap.get("overall"),
        "kap_verified_events_24h": kap.get("verified_events_24h", 0),
    })

# ======================================================
# ADMIN PANEL
# ======================================================

@app.route(f"/{ADMIN_PANEL_PATH}")
@app.route("/admin")
def admin_panel():
    return render_template("admin.html")


@app.route("/admin/test-signal-card")
@admin_required
def admin_test_signal_card():
    latest = get_latest_position_signal_payload()
    return render_template(
        "admin_signal_card_test.html",
        latest=latest,
        exact_preview_available=ADMIN_SIGNAL_PREVIEW.exists(),
    )


@app.route("/admin/test-signal-card/image")
@admin_required
def admin_test_signal_card_image():
    refresh = request.args.get("refresh") == "1"
    if ADMIN_SIGNAL_PREVIEW.exists() and not refresh:
        response = send_file(ADMIN_SIGNAL_PREVIEW, mimetype="image/png", max_age=0)
        response.headers["X-Signal-Preview-Source"] = "EXACT_BOT_PNG"
        return response

    signal = get_latest_position_signal_payload(request.args.get("symbol"))
    if not signal:
        image_path, _ = build_test_signal_card()
        archive_admin_signal_preview(image_path)
        response = send_file(ADMIN_SIGNAL_PREVIEW, mimetype="image/png", max_age=0)
        response.headers["X-Signal-Preview-Source"] = "ADMIN_TEST_FIXTURE"
        return response

    items = fetch_market_snapshot([signal.get("symbol")])
    if not items:
        image_path, _ = build_test_signal_card(signal)
        archive_admin_signal_preview(image_path)
        response = send_file(ADMIN_SIGNAL_PREVIEW, mimetype="image/png", max_age=0)
        response.headers["X-Signal-Preview-Source"] = "ADMIN_TEST_FIXTURE_RATE_LIMIT_FALLBACK"
        return response
    item = items[0]
    signal["current_price"] = item.get("current_price")
    tf = item.get("tf") or {}
    assessment = analyze_position_structure(
        (tf.get("1d") or {}).get("df"),
        (tf.get("4h") or {}).get("df"),
        (tf.get("1h") or {}).get("df"),
        item.get("current_price"),
    )
    signal["technical_structures"] = assessment.get("structures") or {}
    signal["technical_confirmations"] = assessment.get("confirmations") or []
    signal["technical_warnings"] = assessment.get("warnings") or []
    signal["market_structure_phase"] = assessment.get("phase")
    signal["rsi_4h"] = (tf.get("4h") or {}).get("rsi")
    image_path = build_signal_card(
        signal,
        item,
        state={"stage": signal.get("signal_stage") or "GÜÇLÜ", "history": []},
    )
    archive_admin_signal_preview(image_path)
    response = send_file(ADMIN_SIGNAL_PREVIEW, mimetype="image/png", max_age=0)
    response.headers["X-Signal-Preview-Source"] = "REAL_SIGNAL_LATEST_4H_RERENDER"
    return response


@app.route("/admin/test-signal-card/send", methods=["POST"])
@admin_required
def admin_send_test_signal_card():
    now = int(time.time())
    last_sent = int(session.get("last_test_signal_sent_at") or 0)
    if now - last_sent < 30:
        return jsonify({"error": "Yeni test için 30 saniye bekleyin"}), 429

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT telegram_chat_id FROM users WHERE username=? AND is_admin=1",
        (session.get("user"),),
    )
    row = cur.fetchone()
    conn.close()
    # Prefer the authenticated admin's linked chat; production ADMIN_CHAT_ID is
    # the explicit, operator-controlled fallback for delivery tests.
    chat_id = (row["telegram_chat_id"] if row else None) or ADMIN_CHAT_ID
    if not chat_id:
        return jsonify({"error": "Admin Telegram hedefi tanımlı değil"}), 400

    result, error, status = build_latest_real_admin_card()
    if error:
        return jsonify({"error": error}), status
    image_path, signal = result
    archive_admin_signal_preview(image_path)
    caption = admin_test_signal_caption(signal)
    if not send_photo(chat_id, str(ADMIN_SIGNAL_PREVIEW), caption):
        return jsonify({"error": "Telegram fotoğraf gönderimi başarısız"}), 502
    session["last_test_signal_sent_at"] = now
    return jsonify({"status": "sent", "recipient": "admin_telegram"})


def admin_test_signal_caption(signal):
    return (
        "🧪 <b>TEST SİNYALİ — İŞLEM AÇMAYIN</b>\n"
        f"📊 {str(signal.get('symbol') or 'TEST').replace('.IS', '')}\n"
        "Grafik gerçek 4H OHLCV, MOST, destek/direnç ve RSI verisinden üretildi. "
        "Trade defterine kaydedilmemiştir."
    )


def build_latest_real_admin_card():
    """Build a delivery-test card exclusively from the latest real signal/data."""
    latest = get_latest_position_signal_payload()
    visual_test_only = not latest or not latest.get("symbol")
    latest = dict(latest or {})
    symbol = (latest or {}).get("symbol") or os.getenv("ADMIN_TEST_SYMBOL", "THYAO.IS")
    items = fetch_market_snapshot([symbol])
    if not items:
        return None, "Gerçek 4H piyasa verisi alınamadı; simüle kart gönderilmedi", 503
    item = items[0]
    latest["current_price"] = item.get("current_price")
    tf = item.get("tf") or {}
    assessment = analyze_position_structure(
        (tf.get("1d") or {}).get("df"),
        (tf.get("4h") or {}).get("df"),
        (tf.get("1h") or {}).get("df"),
        item.get("current_price"),
    )
    if visual_test_only:
        d4 = (tf.get("4h") or {}).get("df")
        if d4 is None or len(d4) < 20:
            return None, "Gerçek 4H test verisi yetersiz; simüle kart gönderilmedi", 503
        price = float(item.get("current_price"))
        structures = assessment.get("structures") or {}
        sr4 = (structures.get("support_resistance") or {}).get("4H") or {}
        tr = pd.concat([
            d4["High"] - d4["Low"],
            (d4["High"] - d4["Close"].shift(1)).abs(),
            (d4["Low"] - d4["Close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        risk = max(atr * 1.5, price * 0.02)
        support = sr4.get("support")
        resistance = sr4.get("resistance")
        stop = float(support) - atr * 0.25 if support and float(support) < price else price - risk
        unit_risk = max(price - stop, price * 0.01)
        first_target = float(resistance) if resistance and float(resistance) > price else price + unit_risk * 1.5
        latest = {
            "symbol": symbol,
            "signal_scope": "POSITION",
            "main_algorithm": "REAL_DATA_VISUAL_TEST",
            "score": max(0, min(99, 60 + int(assessment.get("score_adjustment") or 0))),
            "entry_price": round(price, 2),
            "current_price": round(price, 2),
            "stop_loss": round(stop, 2),
            "tp1": round(first_target, 2),
            "tp2": round(price + unit_risk * 2.5, 2),
            "tp3": round(price + unit_risk * 4.0, 2),
            "risk_pct": round(unit_risk / price * 100, 2),
            "calibration_status": "GERÇEK VERİ TESTİ",
            "calibration_sample_size": 0,
        }
    latest["technical_structures"] = assessment.get("structures") or {}
    confirmations = assessment.get("confirmations") or []
    latest["technical_confirmations"] = (["Gerçek 4H OHLCV görsel testi"] + confirmations)[:5] if visual_test_only else confirmations
    warnings = assessment.get("warnings") or []
    latest["technical_warnings"] = (["Görsel testtir; gerçek trade sinyali değildir"] + warnings)[:5] if visual_test_only else warnings
    latest["market_structure_phase"] = assessment.get("phase")
    latest["rsi_4h"] = (tf.get("4h") or {}).get("rsi")
    image_path = build_signal_card(
        latest,
        item,
        state={"stage": "TEST" if visual_test_only else latest.get("signal_stage") or "GÜÇLÜ", "history": []},
    )
    return (image_path, latest), None, 200


def send_env_admin_real_test_signal():
    """Operations hook: explicit ADMIN_CHAT_ID only, never channel/subscribers."""
    if not ADMIN_CHAT_ID:
        return False, "ADMIN_CHAT_ID tanımlı değil"
    result, error, _ = build_latest_real_admin_card()
    if error:
        return False, error
    image_path, signal = result
    archive_admin_signal_preview(image_path)
    if not send_photo(ADMIN_CHAT_ID, str(ADMIN_SIGNAL_PREVIEW), admin_test_signal_caption(signal)):
        return False, "Telegram fotoğraf gönderimi başarısız"
    return True, str(signal.get("symbol") or "")

@app.route("/admin/users")
@admin_required
def admin_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, telegram_chat_id,
               subscription_end, status, is_active
        FROM users ORDER BY id DESC
    """)
    users = cur.fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route("/admin/delete-user", methods=["POST"])
@admin_required
def delete_user():
    data = request.json
    user_id = data.get("user_id")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})

@app.route("/admin/update-subscription", methods=["POST"])
@admin_required
def update_subscription():
    data = request.json
    user_id = data.get("user_id")
    days = int(data.get("days", 0))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_end FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    now = datetime.now()
    if row and row["subscription_end"]:
        current_end = datetime.strptime(row["subscription_end"], "%Y-%m-%d %H:%M:%S")
        if current_end > now:
            new_end = current_end + timedelta(days=days)
        else:
            new_end = now + timedelta(days=days)
    else:
        new_end = now + timedelta(days=days)
    cur.execute("""
        UPDATE users
        SET subscription_end=?, status='approved', is_active=1, expiry_warning_sent=0
        WHERE id=?
    """, (new_end.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

# ======================================================
# 🛠 MANUAL BRUT SYSTEM (AUTO CLEAN)
# ======================================================

MANUAL_BRUT_FILE = "data/manual_brut.json"


def load_manual_bruts():
    if not os.path.exists(MANUAL_BRUT_FILE):
        return {}
    try:
        with open(MANUAL_BRUT_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_manual_bruts(data):
    os.makedirs("data", exist_ok=True)
    with open(MANUAL_BRUT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def clean_bruts(data):
    today = datetime.now().date()
    cleaned = {}

    for sym, d in data.items():

        end = d.get("end_date")

        if not end or end == "-":
            d["days_left"] = None
            cleaned[sym] = d
            continue

        try:
            end_dt = datetime.strptime(end, "%d.%m.%Y").date()
            days_left = (end_dt - today).days

            if days_left >= 0:
                d["days_left"] = days_left
                cleaned[sym] = d

        except:
            cleaned[sym] = d

    return cleaned


# ------------------------------------------------------
# ADD
# ------------------------------------------------------
@app.route("/admin/add-brut", methods=["POST"])
@admin_required
def add_brut():

    data = request.json
    symbol = data.get("symbol")
    end_date = data.get("end_date")

    if not symbol:
        return jsonify({"status":"error"}),400

    symbol = symbol.upper().replace(".IS","") + ".IS"

    brut_data = load_manual_bruts()

    brut_data[symbol] = {
        "start_date": datetime.now().strftime("%d.%m.%Y"),
        "end_date": end_date or "-",
        "days_left": None,
        "type": "Manual (Admin)",
        "priority": 999
    }

    brut_data = clean_bruts(brut_data)
    save_manual_bruts(brut_data)

    return jsonify({"status":"ok"})


# ------------------------------------------------------
# GET (AUTO CLEAN)
# ------------------------------------------------------
@app.route("/admin/get-bruts")
@admin_required
def get_bruts():

    brut_data = load_manual_bruts()
    brut_data = clean_bruts(brut_data)
    save_manual_bruts(brut_data)

    return jsonify(brut_data)


# ------------------------------------------------------
# DELETE
# ------------------------------------------------------
@app.route("/admin/delete-brut", methods=["POST"])
@admin_required
def delete_brut():

    symbol = request.json.get("symbol")

    brut_data = load_manual_bruts()

    brut_data.pop(symbol, None)

    save_manual_bruts(brut_data)

    return jsonify({"ok":True})
    
@app.route("/admin/invite-codes")
@admin_required
def admin_invite_codes():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT code, is_used, used_by, created_at
        FROM invite_codes ORDER BY created_at DESC
    """)
    codes = cur.fetchall()
    conn.close()
    return jsonify([dict(c) for c in codes])

@app.route("/admin/delete-code", methods=["POST"])
@admin_required
def admin_delete_code():
    data = request.json
    code = data.get("code")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM invite_codes WHERE code=?", (code,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})

@app.route("/admin/generate-codes", methods=["POST"])
@admin_required
def generate_invite_codes():
    data = request.json or {}

    count = int(data.get("count", 5))
    expire_days = data.get("expire_days")

    try:
        expire_days = int(expire_days) if expire_days else None
    except:
        expire_days = None

    conn = get_connection()
    cur = conn.cursor()

    created_codes = []

    for _ in range(count):
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            cur.execute("SELECT id FROM invite_codes WHERE code=?", (code,))
            if not cur.fetchone():
                break

        created_at = datetime.now()
        expires_at = created_at + timedelta(days=expire_days) if expire_days else None

        cur.execute("""
            INSERT INTO invite_codes
            (code, is_used, created_at, expires_at)
            VALUES (?, 0, ?, ?)
        """, (
            code,
            created_at.strftime("%Y-%m-%d %H:%M:%S"),
            expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else None
        ))

        created_codes.append(code)

    conn.commit()
    conn.close()

    return jsonify({
        "status": "created",
        "count": len(created_codes),
        "codes": created_codes
    })


def _format_early_kap_notice(symbol, event):
    title = event.get("title") or "KAP bildirimi"
    score = event.get("score")
    link = event.get("link")
    ticker = str(symbol).replace(".IS", "")
    lines = [
        "📰 <b>KAP ERKEN UYARI</b>",
        f"📊 <b>{ticker}</b>",
        "",
        "📌 <b>Yeni bildirim:</b>",
        str(title),
    ]
    if score is not None:
        lines.append(f"🧠 Haber önem puanı: <b>{score}</b>")
    lines.extend([
        "",
        "💡 <b>Bu ne anlama geliyor?</b>",
        "KAP bildirimi resmi kaynaktan doğrulandı. Hisse hızlı izleme listesine alındı; "
        "henüz tek başına alım sinyali değildir.",
        "",
        "⚡ <b>Sıradaki kontrol:</b> fiyat ivmesi, hacim, VWAP, RSI ve trend teyidi birlikte aranacak. "
        "Teknik koşullar da güçlenirse ayrıca güçlü işlem sinyali gönderilecek.",
    ])
    if link:
        lines.extend(["", f"🔗 Resmî KAP bağlantısı: {link}"])
    return "\n".join(lines)


def kap_watch_loop():
    """
    Dedicated 7/24 official-KAP watcher.

    It is deliberately independent from the broad market-data download so a
    slow Yahoo cycle cannot delay disclosure discovery.
    """
    sleep_seconds = max(10, int(os.getenv("KAP_WATCH_LOOP_SECONDS", "10")))
    while True:
        try:
            symbols = get_discovered_universe()
            new_events = check_kap(symbols)
            if new_events:
                update_kap_runtime(new_events)
                # Raw KAP events are persisted and promoted to the fast
                # watchlist, but they are NOT Telegram signals. A public alert
                # is emitted only after price direction, liquidity, RVOL,
                # RSI/VWAP and extension filters confirm a tradable response.
                print(
                    f"KAP_WATCH=QUALIFYING_EVENTS count={len(new_events)} "
                    f"universe={len(symbols)} telegram_raw_alerts=disabled",
                    flush=True,
                )
        except Exception as exc:
            print(f"KAP WATCH ERROR: {exc}", flush=True)
        time.sleep(sleep_seconds)


def universe_watch_loop():
    # Force one discovery on worker start, then let the manager honor its TTL.
    try:
        refresh_universe(force=True, log=lambda msg: print(msg, flush=True))
    except Exception as exc:
        print(f"UNIVERSE STARTUP ERROR: {exc}", flush=True)

    while True:
        try:
            refresh_universe(force=False, log=lambda msg: print(msg, flush=True))
        except Exception as exc:
            print(f"UNIVERSE WATCH ERROR: {exc}", flush=True)
        time.sleep(60)


def fast_lane_loop():
    """
    10-30 second watchlist lane.

    One batched TradingView scanner request follows the strongest/liquid,
    slow-burn, open-trade and fresh-KAP symbols. Structural indicators remain
    sourced from the slower full snapshot to avoid turning noisy ticks into
    signals.
    """
    while True:
        try:
            if not is_market_open():
                time.sleep(min(30, FAST_POLL_SECONDS))
                continue

            market_by_symbol, context = market_runtime_snapshot()
            if not market_by_symbol or not context:
                time.sleep(5)
                continue

            kap_cache, _ = kap_runtime_snapshot()
            open_symbols = get_open_trade_symbols()
            priority = list(open_symbols) + list(kap_cache.keys())
            watchlist = get_fast_watchlist(priority)

            quotes = fetch_fast_quotes(watchlist)
            if not quotes:
                time.sleep(FAST_POLL_SECONDS)
                continue

            for symbol, quote in quotes.items():
                # Open paper positions are managed on the fast lane too, so
                # stops/TP milestones do not wait for the 5-minute broad cache.
                if symbol in open_symbols:
                    for event in safe_update_open_trades(symbol, quote.get("price")):
                        if event.get("type") == "CLOSE":
                            mark_signal_state_closed(
                                symbol, event.get("scope"), event.get("reason")
                            )
                        msg = format_trade_event(event)
                        if route_trade_event(event) == TELEGRAM_CHANNEL:
                            if event.get("type") != "CLOSE":
                                send_to_channel(msg)
                        else:
                            broadcast_signal(msg)

                has_fresh_kap = symbol in kap_cache
                if not has_fresh_kap and not is_early_mover(quote):
                    continue

                base_item = market_by_symbol.get(symbol)
                if not base_item:
                    continue

                item = overlay_quote(
                    base_item,
                    quote,
                    universe_metrics=get_symbol_metrics(symbol),
                )
                if not item:
                    continue

                fast_candidates = evaluate_fast_entry_signal(
                    item, context, kap_cache=kap_cache
                )
                with _SIGNAL_PUBLISH_LOCK:
                    selected_fast = select_publishable_candidates(
                        fast_candidates, "INTRADAY", cycle_cap=1
                    )
                    selected_fast = prepare_fresh_candidates(
                        selected_fast,
                        {symbol: quote},
                        log=lambda msg: print(msg, flush=True),
                    )
                    for sig in selected_fast:
                        publish_v5_signal(sig, item)

        except Exception as exc:
            print(f"FAST LANE ERROR: {exc}", flush=True)

        time.sleep(FAST_POLL_SECONDS)


def scanner_loop():

    send_startup_message()
    
    last_fetch_time = 0
    FETCH_INTERVAL = 60 if TRADING_V3_ENABLED else 5
    last_market_data = []
    kap_cache, kap_version = kap_runtime_snapshot()

    last_brut_report = None
    last_daily_report = None
    last_channel_report = None
    last_eod_close_fetch_time = 0.0
    last_weekly_report = None
    last_momentum_reset = None
    last_heartbeat = 0

    while True:

        now = now_tr()
        snapshot_updated = False
        kap_changed = False

        print(f"\n⏱ Döngü: {now.strftime('%H:%M:%S')}", flush=True)

        if not TRADING_V3_ENABLED:
            reset_daily_success_if_needed()
            reset_weekly_success_if_needed()

        dashboard.SYSTEM_ACTIVE = False
        if time.time() - last_heartbeat >= 30:
            update_worker_heartbeat(market_open=is_market_open(now))
            last_heartbeat = time.time()

        try:

            # --------------------------------------------------
            # KAP CACHE - populated by the independent 7/24 watcher thread.
            # --------------------------------------------------
            latest_kap, latest_kap_version = kap_runtime_snapshot()
            if latest_kap_version != kap_version:
                kap_cache.update(latest_kap)
                kap_version = latest_kap_version
                kap_changed = True

            # --------------------------------------------------
            # MARKET KAPALI
            # --------------------------------------------------

            if not is_market_open(now):

                print("⏹ Market kapalı", flush=True)

                dashboard.SYSTEM_ACTIVE = False

                if TRADING_V3_ENABLED:
                    # Both destinations wait for verified daily closes. Their
                    # report contents and recipients remain scope-specific.
                    if (
                        now.weekday() < 5
                        and now.time() >= dtime(18, 15)
                        and (last_channel_report != now.date() or last_daily_report != now.date())
                        and time.time() - last_eod_close_fetch_time >= 300
                    ):
                        position_symbols = get_open_trade_symbols(scope="POSITION")
                        intraday_symbols = get_open_trade_symbols(scope="INTRADAY")
                        close_data = fetch_final_close_prices(
                            sorted(position_symbols | intraday_symbols),
                            session_date=now.date(),
                        )
                        persisted_position = persist_position_close_prices(
                            close_data, session_date=now.date(), scope="POSITION"
                        )
                        persisted_intraday = persist_position_close_prices(
                            close_data, session_date=now.date(), scope="INTRADAY"
                        )
                        missing_position = position_symbols - persisted_position
                        missing_intraday = intraday_symbols - persisted_intraday
                        last_eod_close_fetch_time = time.time()
                        print(
                            "EOD_CLOSE_VERIFY "
                            f"date={now.date().isoformat()} "
                            f"position={len(persisted_position)}/{len(position_symbols)} "
                            f"channel={len(persisted_intraday)}/{len(intraday_symbols)} "
                            f"missing_position={sorted(missing_position)} "
                            f"missing_channel={sorted(missing_intraday)}",
                            flush=True,
                        )

                        # Retry delayed/missing daily bars every five minutes.
                        # At 19:00 missing quotes are explicitly marked unverified.
                        if (
                            last_channel_report != now.date()
                            and (not missing_intraday or now.time() >= dtime(19, 0))
                        ):
                            channel_report = build_v4_daily_report(
                                scope="INTRADAY",
                                verified_close_date=now.date(),
                            )
                            if channel_report:
                                send_to_channel(channel_report)
                            last_channel_report = now.date()

                        if (
                            last_daily_report != now.date()
                            and (not missing_position or now.time() >= dtime(19, 0))
                        ):
                            report = build_v4_daily_report(
                                scope="POSITION",
                                verified_close_date=now.date(),
                            )
                            if report:
                                send_report_to_admins(report)
                            last_daily_report = now.date()
                elif last_daily_report != now.date() and now.time() > BIST_CLOSE:
                    report = build_daily_success_report()
                    if report:
                        send_report_to_admins(report)
                    m_report = build_momentum_daily_report()
                    if m_report:
                        send_to_channel(m_report)
                    last_momentum_reset = reset_momentum_if_needed(
                        last_momentum_reset, now
                    )
                    last_daily_report = now.date()

                if now.weekday() == 4 and now.time() >= dtime(18, 10):

                    week_id = now.strftime("%Y-%W")

                    if last_weekly_report != week_id:

                        report = (
                            build_v4_weekly_report()
                            if TRADING_V3_ENABLED
                            else build_weekly_success_report()
                        )

                        if report:
                            send_report_to_admins(report)

                        last_weekly_report = week_id

                time.sleep(30)
                continue

            # --------------------------------------------------
            # MARKET AÇIK
            # --------------------------------------------------

            dashboard.SYSTEM_ACTIVE = True
            print("✅ MARKET AÇIK → TARAMA", flush=True)
            # 🔥 BRUT MAP HER ZAMAN GÜNCEL
            safe_refresh_brut()

            # --------------------------------------------------
            # 📢 GÜNLÜK BRUT TAKAS RAPORU (09:40)
            # --------------------------------------------------

            if (
                last_brut_report != now.date()
                and now.weekday() < 5
                and now.time() >= dtime(9, 40)
            ):

                try:

                    # 🔥 önce güncelle
                    safe_refresh_brut()

                    msg = build_brut_report()

                    if msg:
                        send_to_channel(msg)
                        print("✅ BRUT RAPOR GÖNDERİLDİ")

                    last_brut_report = now.date()

                except Exception as e:
                    print("BRUT REPORT ERROR:", e)

            # --------------------------------------------------
            # BRÜT TAKAS KONTROL
            # --------------------------------------------------
            try:
                new_bruts = detect_new_bruts()
                if new_bruts and len(new_bruts) > 0:
                    msg = build_new_message(new_bruts, now_tr())
                    if msg:
                        send_to_channel(msg)
            except Exception as e:
                print("BRUT ERROR:", e)

            # --------------------------------------------------
            # MARKET DATA
            # --------------------------------------------------

            # --------------------------------------------------
            # MARKET DATA (SHARED-HOST RESOURCE GUARD)
            # --------------------------------------------------
            # IMS is the latency-sensitive application on this host. Before
            # starting the expensive Yahoo/universe cycle, BIST cooperatively
            # yields when system memory or CPU pressure is high. KAP polling,
            # heartbeat and lightweight housekeeping above still continue.
            pressure = host_pressure_state()
            if pressure.should_yield:
                print(
                    "BIST RESOURCE YIELD "
                    f"reason={pressure.reason} "
                    f"available_mb={pressure.available_mb} "
                    f"available_ratio={pressure.available_ratio:.1%} "
                    f"load_per_cpu={pressure.load_per_cpu} "
                    f"backoff={pressure.backoff_seconds}s",
                    flush=True,
                )
                time.sleep(pressure.backoff_seconds)
                continue

            # --------------------------------------------------
            # MARKET DATA (NON-BLOCKING)
            # --------------------------------------------------

            if time.time() - last_fetch_time > FETCH_INTERVAL:
                new_data = (
                    fetch_market_snapshot(get_active_universe())
                    if TRADING_V3_ENABLED
                    else fetch_bist_data(ENGINE_SYMBOLS)
                )
                if isinstance(new_data, list):
                    if len(new_data) > 0:
                        last_market_data = new_data
                        snapshot_updated = True
                    else:
                        print("⚠ boş veri geldi, eski veri korunuyor")
                last_fetch_time = time.time()

            market_data = last_market_data or []

            if len(market_data) == 0:
                print("⚠ Veri alınamadı → sinyal durduruldu", flush=True)
                time.sleep(60)
                continue

            # V3 ranks the complete configured universe; do not silently drop symbols.

            valid_count = 0

            for x in market_data:
                try:
                    price = x.get("current_price")
                    symbol = x.get("symbol")

                    if symbol and isinstance(price, (int, float)) and price > 0:
                        valid_count += 1
                except:
                    continue

            threshold = max(20, int(len(market_data) * 0.35))
            if valid_count < threshold:
                print(
                    f"⚠ Sağlıksız veri → valid={valid_count} / total={len(market_data)} / threshold={threshold}",
                    flush=True
                )
                time.sleep(60)
                continue

            if TRADING_V3_ENABLED and not snapshot_updated and not kap_changed:
                # Reprocessing the same cached 279-symbol snapshot every few seconds
                # only creates duplicate CPU/SQLite load. Wait for new market data
                # or a fresh KAP event.
                time.sleep(SCAN_INTERVAL)
                continue

            # Build cross-sectional breadth/regime/ranking once per snapshot.
            market_context = (
                build_market_context(market_data)
                if TRADING_V3_ENABLED
                else None
            )

            if TRADING_V3_ENABLED and market_context:
                publish_market_runtime(market_data, market_context)

            if TRADING_V3_ENABLED and snapshot_updated:
                persist_market_snapshot(
                    market_data,
                    market_context,
                    market_open=True,
                )

            # Query open paper symbols once per market snapshot instead of
            # once per each of ~279 symbols.
            open_trade_symbols = get_open_trade_symbols() if TRADING_V3_ENABLED else set()

            # ==================================================
            # 🔁 MAIN LOOP
            # ==================================================

            cycle_position_candidates = []
            cycle_intraday_published = 0
            cycle_items = {
                item.get("symbol"): item
                for item in market_data
                if item.get("symbol")
            }

            for item in market_data:

                symbol = item.get("symbol")
                price = item.get("current_price")

                # V3 uses the centralized Yahoo batch snapshot by default.
                # Optional ultra-price cache can be enabled explicitly, but is never
                # required for strategy evaluation.
                if not TRADING_V3_ENABLED or ENABLE_ULTRA_PRICE_ENGINE:
                    if not price or price == 0:
                        price = get_price(symbol)

                    if not price and symbol:
                        clean_symbol = symbol.replace(".IS", "")
                        price = get_price(clean_symbol)

                    live_price = get_price(symbol)
                    if live_price is not None and live_price > 0:
                        price = live_price
                        item["current_price"] = price

                # ❌ hala yoksa skip
                if not price:
                    continue

                # 🔥 artık güvenli şekilde kullanabilirsin
                item["current_price"] = price

                # Legacy synthetic tick-RVOL is intentionally disabled in V3.
                # It counted application polling events instead of traded volume.
                if not TRADING_V3_ENABLED:
                    item["timestamp"] = time.time()
                    update_tick(symbol, price)
                    rvol = get_rvol(symbol) or 0
                    item["rvol"] = rvol

                if symbol and isinstance(price, (int, float)):
                    dashboard.LIVE_PRICES[symbol] = price

                if TRADING_V3_ENABLED:
                    try:
                        # Persist and update paper-trade lifecycle before evaluating
                        # fresh entries. Bot and channel positions are independent.
                        if symbol in open_trade_symbols:
                            for event in safe_update_open_trades(symbol, price):
                                event_msg = format_trade_event(event)
                                if route_trade_event(event) == TELEGRAM_CHANNEL:
                                    if event.get("type") != "CLOSE":
                                        send_to_channel(event_msg)
                                else:
                                    broadcast_signal(event_msg)

                        position_signals = evaluate_position_signals(
                            item, market_context, kap_cache=kap_cache
                        )
                        intraday_signals = evaluate_intraday_signals(
                            item, market_context, kap_cache=kap_cache
                        )

                        cycle_position_candidates.extend(position_signals)

                        # Publish a qualifying channel setup at the point it is
                        # discovered.  Waiting for the remaining market-wide
                        # OHLCV loop used to turn an early trigger into a late
                        # notification several minutes later.
                        if intraday_signals:
                            with _SIGNAL_PUBLISH_LOCK:
                                immediate = select_publishable_candidates(
                                    intraday_signals, "INTRADAY", cycle_cap=1
                                )
                                live_quote = fetch_fast_quotes([symbol])
                                immediate = prepare_fresh_candidates(
                                    immediate,
                                    live_quote,
                                    log=lambda msg: print(msg, flush=True),
                                )
                                for sig in immediate:
                                    if publish_v5_signal(sig, item):
                                        cycle_intraday_published += 1

                    except Exception as e:
                        print(f"⚠ V3 {symbol} hata: {e}", flush=True)

                    # V3 fully owns signal routing; never fall through to the
                    # legacy KOMBINE/SUPER/SCALPING OR-chain.
                    continue

                try:

                    # ==================================================
                    # 🎯 TARGET TRACKING
                    # ==================================================

                    if symbol in ACTIVE_TRADES:

                        trade = ACTIVE_TRADES[symbol]

                        entry = trade["entry"]
                        target = trade["target"]

                        if price >= target:

                            msg = f"""
🎯 HEDEF GERÇEKLEŞTİ

📊 {symbol}

💰 Giriş: {entry}
🏁 Çıkış: {price}

📈 Getiri: %{round((price-entry)/entry*100,2)}
"""

                            send_to_channel(msg)
                            del ACTIVE_TRADES[symbol]

                        elif price <= entry * 0.95:

                            msg = f"""
⛔ BAŞARISIZ

📊 {symbol}

💰 Giriş: {entry}
📉 Fiyat: {price}

Zarar: %{round((price-entry)/entry*100,2)}
"""

                            send_to_channel(msg)
                            del ACTIVE_TRADES[symbol]


                    # ==================================================
                    # 📊 MOMENTUM RESULT TRACK
                    # ==================================================

                    if symbol in MOMENTUM_TRADES:

                        m_trade = MOMENTUM_TRADES[symbol]
                        entry = m_trade["entry"]

                        if m_trade.get("status") == "active":

                            if price >= entry * 1.01:
                                m_trade["status"] = "success"
                                m_trade["exit"] = price

                            elif price <= entry * 0.95:
                                m_trade["status"] = "fail"
                                m_trade["exit"] = price

                    # --------------------------------------------------
                    # 🚀 KAP MOMENTUM
                    # --------------------------------------------------

                    kap_signal = detect_kap_volume_momentum(item, kap_cache)

                    if kap_signal and kap_signal.get("entry_price"):

                        try:                       

                            df = get_15m_df(symbol, live_price=price)

                            # ===============================
                            # 🔥 15M DATA (FULL SAFE)
                            # ===============================
                            if df is not None:

                                if not isinstance(df, pd.DataFrame):
                                    df = pd.DataFrame(df)

                                df = df.copy()
                                df.columns = [c.lower() for c in df.columns]
                                df = df.dropna()

                                if len(df) > 20 and all(c in df.columns for c in ["open","high","low","close"]):

                                    # 🔥 SON 120 BAR (PERF + STABILITY)
                                    df = df.tail(120)

                                    # 🔥 EMA (TRADINGVIEW FIX)
                                    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
                                    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

                                    kap_signal["df15"] = df

                                else:
                                    kap_signal["df15"] = None

                            else:
                                kap_signal["df15"] = None


                            # ===============================
                            # 🔥 1H DATA (YENİ SİSTEM)
                            # ===============================
                            df1h = get_1h_df(symbol, live_price=price)

                            if df1h is not None:

                                if not isinstance(df1h, pd.DataFrame):
                                    df1h = pd.DataFrame(df1h)

                                df1h = df1h.copy()
                                df1h.columns = [c.lower() for c in df1h.columns]
                                df1h = df1h.dropna(subset=["open","high","low","close"])

                                # 🔥 SON BAR FORCE (TAM BURAYA)
                                try:
                                    last_idx = df1h.index[-1]

                                    df1h.at[last_idx, "close"] = price
                                    df1h.at[last_idx, "high"] = max(df1h.at[last_idx, "high"], price)
                                    df1h.at[last_idx, "low"] = min(df1h.at[last_idx, "low"], price)
                                except:
                                    pass

                                if len(df1h) > 20:

                                    df1h = df1h.tail(120)

                                    df1h["ema20"] = df1h["close"].ewm(span=20, adjust=False).mean()
                                    df1h["ema50"] = df1h["close"].ewm(span=50, adjust=False).mean()

                                    kap_signal["df1h"] = df1h

                                else:
                                    kap_signal["df1h"] = None

                            else:
                                kap_signal["df1h"] = None

                            # ===============================
                            # 🔥 DİRENÇ (REAL SAFE)
                            # ===============================
                            try:
                                
                                r = None

                                if kap_signal.get("df1h") is not None:
                                    r = get_last_resistance(kap_signal["df1h"])

                                if not r and item.get("tf", {}).get("4h"):
                                    r = get_last_resistance(item["tf"]["4h"]["df"])

                                kap_signal["resistance"] = r

                            except Exception as e:
                                print("RES ERROR:", e)
                                kap_signal["resistance"] = None


                            # ===============================
                            # 🔥 PRO SR (FIBO + TF COMBO)
                            # ===============================
                            try:
                                entry_price = kap_signal.get("entry_price")

                                support, resistance, strength = compute_sr(
                                    entry_price,
                                    kap_signal.get("df15"),
                                    kap_signal.get("df1h")
                                )

                                if support:
                                    kap_signal["support"] = round(support, 2)

                                if resistance:
                                    kap_signal["resistance"] = round(resistance, 2)

                                kap_signal["sr_strength"] = strength

                            except Exception as e:
                                print("SR ERROR:", e)

                            # ===============================
                            # 🔥 TREND (SMART)
                            # ===============================
                            try:
                                trend = "YATAY"

                                tf15 = item.get("tf", {}).get("15m", {})

                                e20 = tf15.get("ema20_live") or tf15.get("ema20")
                                e50 = tf15.get("ema50_live") or tf15.get("ema50")

                                if e20 and e50:
                                    trend = "YUKARI" if e20 > e50 else "AŞAĞI"

                                elif kap_signal.get("df15") is not None:
                                    last = kap_signal["df15"].iloc[-1]
                                    if last["ema20"] > last["ema50"]:
                                        trend = "YUKARI"
                                    elif last["ema20"] < last["ema50"]:
                                        trend = "AŞAĞI"

                                kap_signal["trend"] = trend

                            except Exception as e:
                                print("TREND ERROR:", e)
                                kap_signal["trend"] = "YATAY"


                        except Exception as e:
                            print("DF BLOCK ERROR:", e)
                            kap_signal["df15"] = None
                            kap_signal["df1h"] = None
                            kap_signal["resistance"] = None
                            kap_signal["trend"] = "YATAY"


                        # ===============================
                        # 🔥 LIVE PRICE
                        # ===============================
                        kap_signal["live_price"] = float(price)

                        kap_symbol = kap_signal["symbol"]

                        # ===============================
                        # 🚀 SIGNAL FIRE
                        # ===============================
                        if not kap_cache.get(kap_symbol, {}).get("alert_sent"):

                            entry_price = kap_signal["entry_price"]
                            target_price = entry_price * 1.01

                            ACTIVE_TRADES[kap_symbol] = {
                                "entry": entry_price,
                                "target": target_price,
                                "time": now
                            }

                            send_momentum_signal(kap_signal)

                            MOMENTUM_TRADES[kap_symbol] = {
                                "entry": entry_price,
                                "time": now,
                                "status": "active"
                            }

                            kap_cache.setdefault(kap_symbol, {})["alert_sent"] = True
                                    
                    # --------------------------------------------------
                    # NORMAL ENGINE
                    # --------------------------------------------------

                    signals = process_symbol_signals(item)

                    success_hits = update_success_targets(symbol, price)

                    for s in success_hits:

                        push_success_signal(s)

                        msg = format_signal_message(s)

                        if s.get("main_algorithm") == "SCALPING":
                            send_to_channel(msg)
                        else:
                            broadcast_signal(msg)

                    for s in signals:

                        push_signal(s)

                        msg = format_signal_message(s)

                        if s.get("main_algorithm") == "SCALPING":
                            send_to_channel(msg)
                        else:
                            broadcast_signal(msg)

                except Exception as e:
                    print(f"⚠ {symbol} hata:", e, flush=True)

            if TRADING_V3_ENABLED:
                with _SIGNAL_PUBLISH_LOCK:
                    selected_positions = select_publishable_candidates(
                        cycle_position_candidates, "POSITION"
                    )
                    # Execution is a separate decision from signal discovery.
                    # Batch one current quote for every candidate and preserve
                    # the original trigger price. Late moves are recorded in
                    # logs but never advertised as executable entries.
                    execution_symbols = list(dict.fromkeys(
                        sig.get("symbol")
                        for sig in selected_positions
                        if sig.get("symbol")
                    ))
                    execution_quotes = fetch_fast_quotes(execution_symbols)
                    freshness_log = lambda msg: print(msg, flush=True)
                    selected_positions = prepare_fresh_candidates(
                        selected_positions,
                        execution_quotes,
                        log=freshness_log,
                    )
                    for sig in selected_positions:
                        publish_v5_signal(
                            sig, cycle_items.get(sig.get("symbol"), {})
                        )

                    if selected_positions or cycle_intraday_published:
                        print(
                            "SIGNAL_POLICY "
                            f"position={len(selected_positions)} "
                            f"intraday={cycle_intraday_published} "
                            f"limits={policy_limits()}",
                            flush=True,
                        )

        except Exception as e:
            print("🔥 Scanner genel hata:", e, flush=True)

        time.sleep(SCAN_INTERVAL)


# ======================================================
# START
# ======================================================

if __name__ == "__main__":

    # ==================================================
    # 🔥 ULTRA ENGINE START
    # ==================================================
    if ENABLE_ULTRA_PRICE_ENGINE:
        print("🚀 OPTIONAL ULTRA PRICE ENGINE STARTING...")
        start_engine(ENGINE_SYMBOLS)

    # Legacy tick-volume threads are not used by V3.
    if not TRADING_V3_ENABLED:
        print("📊 LEGACY VOLUME ENGINE STARTING...")
        load_volume_cache()
        threading.Thread(target=save_volume_cache, daemon=True).start()
        threading.Thread(target=rvol_updater, daemon=True).start()

    # ==================================================
    # 🔁 SCANNER START
    # ==================================================
    threading.Thread(target=scanner_loop, daemon=True).start()

    # ==================================================
    # 🌐 WEB
    # ==================================================
    app.run(host="0.0.0.0", port=5000, debug=False)
