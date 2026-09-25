import hashlib
import json
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from database import get_connection

TR_TZ = ZoneInfo("Europe/Istanbul")


def _now():
    return datetime.now(TR_TZ)


def init_trade_ledger():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS strategy_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT UNIQUE NOT NULL,
        symbol TEXT NOT NULL,
        scope TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        score REAL,
        entry_price REAL NOT NULL,
        stop_loss REAL,
        tp1 REAL,
        tp2 REAL,
        tp3 REAL,
        market_regime TEXT,
        rs_percentile REAL,
        metadata_json TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT UNIQUE NOT NULL,
        symbol TEXT NOT NULL,
        scope TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        entry_price REAL NOT NULL,
        stop_loss REAL,
        tp1 REAL,
        tp2 REAL,
        tp3 REAL,
        trailing_stop REAL,
        tp1_hit INTEGER NOT NULL DEFAULT 0,
        tp2_hit INTEGER NOT NULL DEFAULT 0,
        max_price REAL,
        min_price REAL,
        mfe_pct REAL DEFAULT 0,
        mae_pct REAL DEFAULT 0,
        exit_price REAL,
        result_pct REAL,
        exit_reason TEXT,
        opened_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        closed_at TEXT,
        metadata_json TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_paper_trades_open_symbol
    ON paper_trades(status, symbol)
    """)

    conn.commit()
    conn.close()


def _fingerprint(signal):
    day = _now().date().isoformat()
    raw = "|".join([
        str(signal.get("symbol")),
        str(signal.get("signal_scope")),
        str(signal.get("main_algorithm")),
        day,
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _expiry(scope):
    now = _now()
    if scope == "INTRADAY":
        end = datetime.combine(now.date(), dtime(17, 50), tzinfo=TR_TZ)
        return end
    # 14 calendar days comfortably covers the requested 2-10 trading-day window.
    return now + timedelta(days=14)


def record_signal(signal):
    fingerprint = _fingerprint(signal)
    now = _now()
    scope = signal.get("signal_scope")
    metadata = json.dumps(signal, ensure_ascii=False, default=str)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO strategy_signals (
        fingerprint, symbol, scope, algorithm, score,
        entry_price, stop_loss, tp1, tp2, tp3,
        market_regime, rs_percentile, metadata_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fingerprint,
        signal.get("symbol"),
        scope,
        signal.get("main_algorithm"),
        signal.get("score"),
        signal.get("entry_price"),
        signal.get("stop_loss"),
        signal.get("tp1"),
        signal.get("tp2"),
        signal.get("tp3"),
        signal.get("market_regime"),
        signal.get("relative_strength_percentile"),
        metadata,
        now.isoformat(),
    ))

    inserted = cur.rowcount > 0

    if inserted:
        cur.execute("""
        INSERT OR IGNORE INTO paper_trades (
            fingerprint, symbol, scope, algorithm, status,
            entry_price, stop_loss, tp1, tp2, tp3, trailing_stop,
            max_price, min_price, opened_at, expires_at, metadata_json
        ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fingerprint,
            signal.get("symbol"),
            scope,
            signal.get("main_algorithm"),
            signal.get("entry_price"),
            signal.get("stop_loss"),
            signal.get("tp1"),
            signal.get("tp2"),
            signal.get("tp3"),
            signal.get("stop_loss"),
            signal.get("entry_price"),
            signal.get("entry_price"),
            now.isoformat(),
            _expiry(scope).isoformat(),
            metadata,
        ))

    conn.commit()
    conn.close()
    return inserted


def get_open_trade_symbols():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM paper_trades WHERE status = 'OPEN'")
    symbols = {row["symbol"] for row in cur.fetchall()}
    conn.close()
    return symbols


def _pct(price, entry):
    if not entry:
        return 0.0
    return round((price - entry) / entry * 100.0, 3)


def update_open_trades(symbol, price):
    """
    Update paper positions with actual observed prices.
    Returns lifecycle events for Telegram/dashboard reporting.
    """
    try:
        price = float(price)
    except Exception:
        return []

    now = _now()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM paper_trades
    WHERE status = 'OPEN' AND symbol = ?
    """, (symbol,))
    rows = cur.fetchall()

    events = []

    for row in rows:
        trade = dict(row)
        entry = float(trade["entry_price"])
        max_price = max(float(trade["max_price"] or entry), price)
        min_price = min(float(trade["min_price"] or entry), price)
        mfe = _pct(max_price, entry)
        mae = _pct(min_price, entry)

        trailing = float(trade["trailing_stop"] or trade["stop_loss"] or 0)
        tp1_hit = int(trade["tp1_hit"] or 0)
        tp2_hit = int(trade["tp2_hit"] or 0)

        # Profit milestones tighten risk rather than declaring a fake +1% "success".
        if not tp1_hit and trade["tp1"] is not None and price >= float(trade["tp1"]):
            tp1_hit = 1
            trailing = max(trailing, entry)
            events.append({
                "type": "TP1",
                "symbol": symbol,
                "scope": trade["scope"],
                "algorithm": trade["algorithm"],
                "entry": entry,
                "price": price,
                "gain_pct": _pct(price, entry),
            })

        if not tp2_hit and trade["tp2"] is not None and price >= float(trade["tp2"]):
            tp2_hit = 1
            if trade["tp1"] is not None:
                trailing = max(trailing, float(trade["tp1"]))
            events.append({
                "type": "TP2",
                "symbol": symbol,
                "scope": trade["scope"],
                "algorithm": trade["algorithm"],
                "entry": entry,
                "price": price,
                "gain_pct": _pct(price, entry),
            })

        exit_reason = None
        exit_price = None

        if trailing and price <= trailing:
            exit_reason = "TRAILING_STOP" if tp1_hit else "STOP"
            exit_price = price
        elif trade["tp3"] is not None and price >= float(trade["tp3"]):
            exit_reason = "TP3"
            exit_price = price
        else:
            try:
                expires_at = datetime.fromisoformat(trade["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=TR_TZ)
                if now >= expires_at:
                    exit_reason = "TIME_EXIT"
                    exit_price = price
            except Exception:
                pass

        if exit_reason:
            result_pct = _pct(exit_price, entry)
            cur.execute("""
            UPDATE paper_trades
            SET status = 'CLOSED',
                trailing_stop = ?,
                tp1_hit = ?,
                tp2_hit = ?,
                max_price = ?,
                min_price = ?,
                mfe_pct = ?,
                mae_pct = ?,
                exit_price = ?,
                result_pct = ?,
                exit_reason = ?,
                closed_at = ?
            WHERE id = ?
            """, (
                trailing, tp1_hit, tp2_hit, max_price, min_price, mfe, mae,
                exit_price, result_pct, exit_reason, now.isoformat(), trade["id"]
            ))

            events.append({
                "type": "CLOSE",
                "reason": exit_reason,
                "symbol": symbol,
                "scope": trade["scope"],
                "algorithm": trade["algorithm"],
                "entry": entry,
                "price": exit_price,
                "gain_pct": result_pct,
                "mfe_pct": mfe,
                "mae_pct": mae,
            })
        else:
            cur.execute("""
            UPDATE paper_trades
            SET trailing_stop = ?,
                tp1_hit = ?,
                tp2_hit = ?,
                max_price = ?,
                min_price = ?,
                mfe_pct = ?,
                mae_pct = ?
            WHERE id = ?
            """, (
                trailing, tp1_hit, tp2_hit, max_price, min_price, mfe, mae, trade["id"]
            ))

    conn.commit()
    conn.close()
    return events


def _algo_tr(algo):
    return {
        "KOMBINE_V3": "Kombine Trend Dönüşü",
        "SUPER_KOMBINE_V3": "Güçlü Trend Kırılımı",
        "KAP_POSITION_V3": "KAP Destekli Pozisyon",
        "MOMENTUM_IGNITION_V3": "Erken Momentum Başlangıcı",
        "INTRADAY_MOMENTUM_V3": "Gün İçi Momentum Devamı",
        "KAP_EVENT_INTRADAY_V3": "KAP Destekli Gün İçi Momentum",
        "EARLY_IGNITION_V3": "Erken Hareket Başlangıcı",
        "KAP_EARLY_IGNITION_V3": "KAP Destekli Erken Hareket",
    }.get(str(algo or ""), str(algo or "Strateji").replace("_", " ").title())


def _exit_reason_tr(reason):
    return {
        "STOP": "Koruyucu stop çalıştı",
        "TRAILING_STOP": "İz süren stop çalıştı",
        "TP3": "Ana hedefe ulaştı",
        "TIME_EXIT": "Takip süresi doldu",
    }.get(str(reason or ""), str(reason or "İşlem kapandı").replace("_", " ").title())


def format_trade_event(event):
    symbol = str(event.get("symbol") or "").replace(".IS", "")
    algo = _algo_tr(event.get("algorithm"))
    gain = event.get("gain_pct")

    if event.get("type") == "TP1":
        return (
            f"🎯 <b>1. HEDEF GERÇEKLEŞTİ</b>\n"
            f"📊 <b>{symbol}</b> | {algo}\n"
            f"💰 Başlangıç: {event.get('entry')} → Güncel: {round(event.get('price'), 2)}\n"
            f"📈 Fiyat değişimi: <b>%{gain}</b>\n\n"
            f"🛡 <b>Ne değişti?</b> İlk hedefe ulaşıldığı için sanal takipte stop seviyesi maliyete yükseltildi. "
            f"Bu aşamadan sonra amaç kazanımı koruyarak trendin devamını izlemek."
        )

    if event.get("type") == "TP2":
        return (
            f"🚀 <b>2. HEDEF GERÇEKLEŞTİ — TREND DEVAM EDİYOR</b>\n"
            f"📊 <b>{symbol}</b> | {algo}\n"
            f"📈 Başlangıçtan fiyat değişimi: <b>%{gain}</b>\n\n"
            f"🛡 <b>Takip durumu:</b> Stop daha yukarı taşındı ve işlem iz süren stop mantığıyla takip edilmeye devam ediyor."
        )

    return (
        f"🏁 <b>SANAL İŞLEM TAKİBİ KAPANDI</b>\n"
        f"📊 <b>{symbol}</b> | {algo}\n"
        f"📌 Kapanış nedeni: <b>{_exit_reason_tr(event.get('reason'))}</b>\n"
        f"📈 Net fiyat değişimi: <b>%{gain}</b>\n"
        f"⬆️ İşlem sırasında görülen en yüksek avantaj: %{event.get('mfe_pct')}\n"
        f"⬇️ İşlem sırasında görülen en yüksek ters hareket: %{event.get('mae_pct')}"
    )
