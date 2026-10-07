import hashlib
import json
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from database import get_connection
from execution_model import modeled_fill, net_result_pct
from quant_validation import calibrated_probability, summarize_closed_trades
from signal_policy import POLICY_VERSION

TR_TZ = ZoneInfo("Europe/Istanbul")


def _now():
    return datetime.now(TR_TZ)


def _ensure_column(cur, table, column, ddl):
    cur.execute(f"PRAGMA table_info({table})")
    names = {row[1] for row in cur.fetchall()}
    if column not in names:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


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
        metadata_json TEXT,
        tracking_days_json TEXT NOT NULL DEFAULT '[]',
        tracking_path_json TEXT NOT NULL DEFAULT '[]',
        tracking_day_count INTEGER NOT NULL DEFAULT 0,
        stop_breached INTEGER NOT NULL DEFAULT 0,
        stop_breached_at TEXT,
        stop_breach_price REAL,
        tp3_hit INTEGER NOT NULL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_paper_trades_open_symbol
    ON paper_trades(status, symbol)
    """)

    _ensure_column(
        cur,
        "strategy_signals",
        "policy_version",
        "TEXT NOT NULL DEFAULT 'PRE_SELECTIVE_V4'",
    )
    for table in ("strategy_signals", "paper_trades"):
        _ensure_column(cur, table, "execution_model_version", "TEXT")
        _ensure_column(cur, table, "estimated_entry_cost_bps", "REAL")
        _ensure_column(cur, table, "modeled_entry_price", "REAL")
    _ensure_column(cur, "paper_trades", "estimated_exit_cost_bps", "REAL")
    _ensure_column(cur, "paper_trades", "modeled_exit_price", "REAL")
    _ensure_column(cur, "paper_trades", "gross_result_pct", "REAL")
    _ensure_column(cur, "paper_trades", "net_result_pct", "REAL")
    _ensure_column(cur, "strategy_signals", "calibrated_probability", "REAL")
    _ensure_column(cur, "strategy_signals", "calibration_status", "TEXT")
    _ensure_column(cur, "strategy_signals", "calibration_sample_size", "INTEGER")
    _ensure_column(cur, "strategy_signals", "shadow_variant", "TEXT")
    _ensure_column(cur, "paper_trades", "shadow_variant", "TEXT")
    _ensure_column(cur, "paper_trades", "tracking_days_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(cur, "paper_trades", "tracking_path_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(cur, "paper_trades", "tracking_day_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "paper_trades", "stop_breached", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "paper_trades", "stop_breached_at", "TEXT")
    _ensure_column(cur, "paper_trades", "stop_breach_price", "REAL")
    _ensure_column(cur, "paper_trades", "tp3_hit", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "paper_trades", "pre_tracking_exit_reason", "TEXT")
    _ensure_column(cur, "paper_trades", "pre_tracking_closed_at", "TEXT")
    _ensure_column(
        cur,
        "paper_trades",
        "policy_version",
        "TEXT NOT NULL DEFAULT 'PRE_SELECTIVE_V4'",
    )

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_strategy_signals_policy_scope_time
    ON strategy_signals(policy_version, scope, created_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_paper_trades_policy_scope_time
    ON paper_trades(policy_version, scope, opened_at)
    """)

    # Preserve recent bot/position candidates that the legacy lifecycle closed
    # early at a stop or target. Their old close reason remains auditable while
    # the record resumes the requested ten-session observation window.
    cutoff = (_now() - timedelta(days=16)).isoformat()
    cur.execute("""
    SELECT id, opened_at, entry_price, exit_reason, closed_at
    FROM paper_trades
    WHERE scope='POSITION' AND status='CLOSED' AND opened_at>=?
      AND exit_reason IN ('STOP', 'TRAILING_STOP', 'TP3', 'TIME_EXIT')
      AND pre_tracking_exit_reason IS NULL
    """, (cutoff,))
    for legacy in cur.fetchall():
        opened_day = str(legacy["opened_at"] or "")[:10]
        entry = float(legacy["entry_price"] or 0)
        path = [{
            "date": opened_day, "first": entry, "last": entry,
            "high": entry, "low": entry, "return_pct": 0.0,
        }] if opened_day else []
        breached = 1 if legacy["exit_reason"] in {"STOP", "TRAILING_STOP"} else 0
        cur.execute("""
        UPDATE paper_trades SET status='OPEN', exit_price=NULL, result_pct=NULL,
            exit_reason=NULL, closed_at=NULL, estimated_exit_cost_bps=NULL,
            modeled_exit_price=NULL, gross_result_pct=NULL, net_result_pct=NULL,
            tracking_days_json=?, tracking_path_json=?, tracking_day_count=?,
            stop_breached=?, stop_breached_at=?, pre_tracking_exit_reason=?,
            pre_tracking_closed_at=? WHERE id=?
        """, (
            json.dumps([opened_day] if opened_day else []), json.dumps(path),
            1 if opened_day else 0, breached,
            legacy["closed_at"] if breached else None,
            legacy["exit_reason"], legacy["closed_at"], legacy["id"],
        ))

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
    entry_execution = modeled_fill(signal.get("entry_price"), signal, "BUY")
    calibration = _calibration_for_signal(signal)
    signal["modeled_entry_price"] = entry_execution["fill_price"]
    signal["estimated_entry_cost_bps"] = entry_execution["cost_bps"]
    signal["execution_model_version"] = entry_execution["model_version"]
    signal["estimated_success_probability"] = calibration["probability"]
    signal["calibration_status"] = calibration["status"]
    signal["calibration_sample_size"] = calibration["sample_size"]
    metadata = json.dumps(signal, ensure_ascii=False, default=str)
    policy_version = str(signal.get("policy_version") or POLICY_VERSION)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO strategy_signals (
        fingerprint, symbol, scope, algorithm, score,
        entry_price, stop_loss, tp1, tp2, tp3,
        market_regime, rs_percentile, metadata_json, created_at, policy_version,
        execution_model_version, estimated_entry_cost_bps, modeled_entry_price,
        calibrated_probability, calibration_status, calibration_sample_size
        , shadow_variant
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        policy_version,
        entry_execution["model_version"], entry_execution["cost_bps"],
        entry_execution["fill_price"], calibration["probability"],
        calibration["status"], calibration["sample_size"],
        signal.get("shadow_variant"),
    ))

    inserted = cur.rowcount > 0

    if inserted:
        cur.execute("""
        INSERT OR IGNORE INTO paper_trades (
            fingerprint, symbol, scope, algorithm, status,
            entry_price, stop_loss, tp1, tp2, tp3, trailing_stop,
            max_price, min_price, opened_at, expires_at, metadata_json,
            policy_version, execution_model_version, estimated_entry_cost_bps,
            modeled_entry_price, shadow_variant, tracking_days_json,
            tracking_path_json, tracking_day_count
        ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            policy_version, entry_execution["model_version"],
            entry_execution["cost_bps"], entry_execution["fill_price"],
            signal.get("shadow_variant"),
            json.dumps([now.date().isoformat()]),
            json.dumps([{
                "date": now.date().isoformat(), "first": signal.get("entry_price"),
                "last": signal.get("entry_price"), "high": signal.get("entry_price"),
                "low": signal.get("entry_price"), "return_pct": 0.0,
            }]),
            1,
        ))

    conn.commit()
    conn.close()
    return inserted


def _calibration_for_signal(signal):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.score, p.net_result_pct, p.result_pct
        FROM strategy_signals s
        JOIN paper_trades p ON p.fingerprint=s.fingerprint
        WHERE s.scope=? AND s.algorithm=? AND p.status='CLOSED'
        ORDER BY p.closed_at DESC
        LIMIT 500
        """,
        (signal.get("signal_scope"), signal.get("main_algorithm")),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return calibrated_probability(signal.get("score"), rows)


def get_open_trade_symbols(scope=None):
    conn = get_connection()
    cur = conn.cursor()
    sql = "SELECT DISTINCT symbol FROM paper_trades WHERE status = 'OPEN'"
    params = ()
    if scope:
        sql += " AND scope=?"
        params = (scope,)
    cur.execute(sql, params)
    symbols = {row["symbol"] for row in cur.fetchall()}
    conn.close()
    return symbols


def get_trade_symbols_open_as_of(session_date, scope):
    """Return symbols that had a trade open at the requested session close."""
    target_day = str(session_date)[:10]
    start = datetime.fromisoformat(target_day).replace(tzinfo=TR_TZ)
    end = start + timedelta(days=1)
    cutoff = (start - timedelta(days=16)).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    if scope == "POSITION":
        cur.execute("""
            SELECT DISTINCT symbol FROM paper_trades
            WHERE scope=? AND opened_at>=? AND opened_at<?
              AND (status='OPEN' OR closed_at>=?)
        """, (scope, cutoff, end.isoformat(), end.isoformat()))
    else:
        cur.execute("""
            SELECT DISTINCT symbol FROM paper_trades
            WHERE scope=? AND opened_at>=? AND opened_at<?
              AND (status='OPEN' OR closed_at>=?)
        """, (scope, start.isoformat(), end.isoformat(), end.isoformat()))
    symbols = {row["symbol"] for row in cur.fetchall()}
    conn.close()
    return symbols


def persist_position_close_prices(close_prices, session_date, scope="POSITION"):
    """Persist verified daily closes for trades open at the target session close."""
    target_day = str(session_date)[:10]
    fetched_at = _now().isoformat()
    if not close_prices:
        return set()

    target_end = (
        datetime.fromisoformat(target_day).replace(tzinfo=TR_TZ) + timedelta(days=1)
    ).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, symbol, entry_price, tracking_path_json
        FROM paper_trades
        WHERE scope=? AND (
            status='OPEN' OR (status='CLOSED' AND closed_at>=?)
        )
    """, (scope, target_end))
    rows = [dict(row) for row in cur.fetchall()]
    updated = set()

    for trade in rows:
        quote = close_prices.get(trade["symbol"])
        if not quote or str(quote.get("session_date") or target_day)[:10] != target_day:
            continue
        try:
            price = float(quote.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        try:
            path = json.loads(trade.get("tracking_path_json") or "[]")
        except Exception:
            path = []
        if not isinstance(path, list):
            path = []

        daily = next((item for item in path if item.get("date") == target_day), None)
        if daily is None:
            daily = {"date": target_day, "first": price, "last": price, "high": price, "low": price}
            path.append(daily)

        daily["last"] = price
        daily["high"] = max(float(daily.get("high") or price), price)
        daily["low"] = min(float(daily.get("low") or price), price)
        daily["return_pct"] = _pct(price, float(trade.get("entry_price") or 0))
        daily["close_source"] = "YAHOO_DAILY_CLOSE"
        daily["source_bar_time"] = quote.get("source_bar_time")
        daily["close_fetched_at"] = quote.get("fetched_at") or fetched_at
        path = sorted(path, key=lambda item: item.get("date", ""))[-10:]
        days = sorted({item.get("date") for item in path if item.get("date")})

        cur.execute("""
            UPDATE paper_trades
            SET tracking_path_json=?, tracking_days_json=?, tracking_day_count=?
            WHERE id=?
        """, (json.dumps(path), json.dumps(days), len(days), trade["id"]))
        updated.add(trade["symbol"])

    conn.commit()
    conn.close()
    return updated

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
        modeled_entry = float(trade["modeled_entry_price"] or entry)
        max_price = max(float(trade["max_price"] or entry), price)
        min_price = min(float(trade["min_price"] or entry), price)
        mfe, mae = _pct(max_price, entry), _pct(min_price, entry)
        trailing = float(trade["trailing_stop"] or trade["stop_loss"] or 0)
        tp1_hit = int(trade["tp1_hit"] or 0)
        tp2_hit = int(trade["tp2_hit"] or 0)
        tp3_hit = int(trade.get("tp3_hit") or 0)
        stop_breached = int(trade.get("stop_breached") or 0)
        stop_breached_at = trade.get("stop_breached_at")
        stop_breach_price = trade.get("stop_breach_price")

        try:
            tracking_path = json.loads(trade.get("tracking_path_json") or "[]")
        except Exception:
            tracking_path = []
        today = now.date().isoformat()
        if trade["scope"] == "POSITION":
            daily = next((item for item in tracking_path if item.get("date") == today), None)
            if daily is None:
                daily = {"date": today, "first": price, "last": price, "high": price, "low": price}
                tracking_path.append(daily)
            daily["last"] = price
            daily["high"] = max(float(daily.get("high") or price), price)
            daily["low"] = min(float(daily.get("low") or price), price)
            daily["return_pct"] = _pct(price, entry)
            tracking_path = sorted(tracking_path, key=lambda item: item.get("date", ""))[-10:]
        tracking_days = [item.get("date") for item in tracking_path if item.get("date")]
        tracking_day_count = len(set(tracking_days))

        for field, event_type in (("tp1", "TP1"), ("tp2", "TP2"), ("tp3", "TP3")):
            hit = {"tp1": tp1_hit, "tp2": tp2_hit, "tp3": tp3_hit}[field]
            target = trade.get(field)
            if not hit and target is not None and price >= float(target):
                if field == "tp1":
                    tp1_hit, trailing = 1, max(trailing, entry)
                elif field == "tp2":
                    tp2_hit = 1
                    if trade["tp1"] is not None:
                        trailing = max(trailing, float(trade["tp1"]))
                else:
                    tp3_hit = 1
                events.append({
                    "type": event_type, "symbol": symbol, "scope": trade["scope"],
                    "algorithm": trade["algorithm"], "entry": entry, "price": price,
                    "gain_pct": _pct(price, entry),
                })

        original_stop = float(trade["stop_loss"] or 0)
        if trade["scope"] == "POSITION" and not stop_breached and original_stop and price <= original_stop:
            stop_breached = 1
            stop_breached_at, stop_breach_price = now.isoformat(), price
            events.append({
                "type": "STOP_BREACH", "symbol": symbol, "scope": trade["scope"],
                "algorithm": trade["algorithm"], "entry": entry, "price": price,
                "gain_pct": _pct(price, entry),
            })

        exit_reason = None
        exit_price = None
        if trade["scope"] == "POSITION":
            if tracking_day_count >= 10:
                exit_reason, exit_price = "TRACKING_10D_COMPLETE", price
        elif trailing and price <= trailing:
            exit_reason, exit_price = ("TRAILING_STOP" if tp1_hit else "STOP"), price
        elif trade["tp3"] is not None and price >= float(trade["tp3"]):
            exit_reason, exit_price = "TP3", price
        else:
            try:
                expires_at = datetime.fromisoformat(trade["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=TR_TZ)
                if now >= expires_at:
                    exit_reason, exit_price = "TIME_EXIT", price
            except Exception:
                pass

        common = (
            trailing, tp1_hit, tp2_hit, tp3_hit, max_price, min_price, mfe, mae,
            json.dumps(tracking_days), json.dumps(tracking_path), tracking_day_count,
            stop_breached, stop_breached_at, stop_breach_price,
        )
        if exit_reason:
            result_pct = _pct(exit_price, entry)
            try:
                signal_meta = json.loads(trade.get("metadata_json") or "{}")
            except Exception:
                signal_meta = {"signal_scope": trade.get("scope")}
            exit_execution = modeled_fill(exit_price, signal_meta, "SELL")
            modeled_exit = exit_execution["fill_price"]
            net_result = net_result_pct(modeled_entry, modeled_exit)
            cur.execute("""
            UPDATE paper_trades SET status='CLOSED', trailing_stop=?, tp1_hit=?,
                tp2_hit=?, tp3_hit=?, max_price=?, min_price=?, mfe_pct=?, mae_pct=?,
                tracking_days_json=?, tracking_path_json=?, tracking_day_count=?,
                stop_breached=?, stop_breached_at=?, stop_breach_price=?,
                exit_price=?, result_pct=?, exit_reason=?, closed_at=?,
                estimated_exit_cost_bps=?, modeled_exit_price=?, gross_result_pct=?,
                net_result_pct=? WHERE id=?
            """, common + (
                exit_price, result_pct, exit_reason, now.isoformat(),
                exit_execution["cost_bps"], modeled_exit, result_pct, net_result, trade["id"],
            ))
            events.append({
                "type": "CLOSE", "reason": exit_reason, "symbol": symbol,
                "scope": trade["scope"], "algorithm": trade["algorithm"],
                "entry": entry, "price": exit_price, "gain_pct": net_result,
                "gross_gain_pct": result_pct, "modeled_exit_price": modeled_exit,
                "execution_model_version": exit_execution["model_version"],
                "mfe_pct": mfe, "mae_pct": mae,
            })
        else:
            cur.execute("""
            UPDATE paper_trades SET trailing_stop=?, tp1_hit=?, tp2_hit=?, tp3_hit=?,
                max_price=?, min_price=?, mfe_pct=?, mae_pct=?, tracking_days_json=?,
                tracking_path_json=?, tracking_day_count=?, stop_breached=?,
                stop_breached_at=?, stop_breach_price=? WHERE id=?
            """, common + (trade["id"],))

    conn.commit()
    conn.close()
    return events


def _algo_tr(algo):
    return {
        "KOMBINE_V3": "Kombine Trend Dönüşü",
        "SUPER_KOMBINE_V3": "Güçlü Trend Kırılımı",
        "KAP_POSITION_V3": "KAP Destekli Pozisyon",
        "TREND_START_V3": "Yeni Trend Başlangıcı",
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
            f"🎯 <b>{symbol} · 1. HEDEF</b>\n"
            f"{algo}\n"
            f"📈 Sonuç: <b>%{gain}</b> · stop maliyete taşındı."
        )

    if event.get("type") == "TP2":
        return (
            f"🚀 <b>{symbol} · 2. HEDEF</b>\n"
            f"{algo}\n"
            f"📈 Sonuç: <b>%{gain}</b> · iz süren stop aktif."
        )

    return (
        f"🏁 <b>{symbol} · TAKİP KAPANDI</b>\n"
        f"{algo}\n"
        f"📌 {_exit_reason_tr(event.get('reason'))}\n"
        f"📈 Net: <b>%{gain}</b> · En iyi: %{event.get('mfe_pct')} · En ters: %{event.get('mae_pct')}"
    )

def _period_report(start_at, title):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT scope, status, exit_reason, COUNT(*) AS c,
               AVG(result_pct) AS avg_result
        FROM paper_trades
        WHERE policy_version=? AND opened_at>=?
        GROUP BY scope, status, exit_reason
        ORDER BY scope, status
        """,
        (POLICY_VERSION, start_at.isoformat()),
    )
    rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM strategy_signals
        WHERE policy_version=? AND created_at>=?
        """,
        (POLICY_VERSION, start_at.isoformat()),
    )
    total_signals = int(cur.fetchone()["c"] or 0)
    conn.close()

    if total_signals == 0:
        return None

    open_count = sum(int(r["c"] or 0) for r in rows if r["status"] == "OPEN")
    closed_rows = [r for r in rows if r["status"] == "CLOSED"]
    closed_count = sum(int(r["c"] or 0) for r in closed_rows)

    positive = 0
    negative = 0
    weighted_sum = 0.0
    weighted_n = 0
    scope_counts = {"POSITION": 0, "INTRADAY": 0}

    for r in rows:
        scope_counts[r["scope"]] = scope_counts.get(r["scope"], 0) + int(r["c"] or 0)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT result_pct, net_result_pct
        FROM paper_trades
        WHERE policy_version=? AND status='CLOSED' AND opened_at>=?
        """,
        (POLICY_VERSION, start_at.isoformat()),
    )
    metric_rows = [dict(r) for r in cur.fetchall()]
    for r in metric_rows:
        value = r["net_result_pct"] if r["net_result_pct"] is not None else r["result_pct"]
        if value is None:
            continue
        value = float(value)
        positive += 1 if value > 0 else 0
        negative += 1 if value <= 0 else 0
        weighted_sum += value
        weighted_n += 1
    conn.close()

    avg_result = weighted_sum / weighted_n if weighted_n else None
    validation = summarize_closed_trades(metric_rows)

    lines = [
        f"📊 <b>{title}</b>",
        f"📡 Yeni seçici sinyal: <b>{total_signals}</b>",
        f"📌 Pozisyon: <b>{scope_counts.get('POSITION', 0)}</b> | Gün içi: <b>{scope_counts.get('INTRADAY', 0)}</b>",
        f"🟢 Açık takip: <b>{open_count}</b>",
        f"🏁 Kapanan: <b>{closed_count}</b>",
    ]

    if closed_count:
        lines.append(f"✅ Pozitif kapanış: <b>{positive}</b> | ❌ Negatif kapanış: <b>{negative}</b>")
        if avg_result is not None:
            lines.append(f"📈 Maliyet sonrası ortalama: <b>%{avg_result:.2f}</b>")
        if validation.get("profit_factor") is not None:
            lines.append(f"⚖️ Profit factor: <b>{validation['profit_factor']}</b>")
        if validation.get("max_drawdown_pct") is not None:
            lines.append(f"📉 Sıralı maksimum düşüş: <b>%{validation['max_drawdown_pct']}</b>")
        lines.append(
            f"🧪 Doğrulama: <b>{validation['status']}</b> · örnek {validation['sample_size']}"
        )

    lines.append("ℹ️ Açık işlemler başarısız sayılmaz; yalnız kapanmış işlemler sonuç istatistiğine girer.")
    return "\n".join(lines)


def _price_text(value):
    if value is None:
        return "-"
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def build_v4_daily_report(scope=None, verified_close_date=None, report_date=None):
    now = _now()
    report_day = str(report_date or now.date())[:10]
    start = datetime.fromisoformat(report_day).replace(tzinfo=TR_TZ)
    end = start + timedelta(days=1)
    position_cutoff = (start - timedelta(days=16)).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    sql = """
        SELECT symbol, scope, status, entry_price, exit_price, result_pct,
               net_result_pct, tp1_hit, opened_at, closed_at, tracking_day_count,
               tracking_path_json
        FROM paper_trades
        WHERE policy_version=? AND opened_at>=?
    """
    params = [POLICY_VERSION, start.isoformat()]
    if scope == "POSITION":
        sql = """
            SELECT symbol, scope, status, entry_price, exit_price, result_pct,
                   net_result_pct, tp1_hit, opened_at, closed_at, tracking_day_count,
                   tracking_path_json
            FROM paper_trades
            WHERE policy_version=? AND scope='POSITION'
              AND opened_at>=? AND opened_at<?
              AND (status='OPEN' OR closed_at>=?)
        """
        params = [POLICY_VERSION, position_cutoff, end.isoformat(), end.isoformat()]
    elif scope:
        sql += " AND scope=? AND opened_at<?"
        params.extend([scope, end.isoformat()])
        if scope == "INTRADAY":
            # The channel's daily report includes every signal opened that day;
            # closed rows keep their recorded exits, while open rows use the
            # verified session close.
            sql += " AND opened_at>=?"
            params.append(start.isoformat())
    else:
        sql = """
            SELECT symbol, scope, status, entry_price, exit_price, result_pct,
                   net_result_pct, tp1_hit, opened_at, closed_at, tracking_day_count,
                   tracking_path_json
            FROM paper_trades
            WHERE policy_version=? AND (
                opened_at>=?
                OR (scope='POSITION' AND status='OPEN' AND opened_at>=?)
                OR (scope='POSITION' AND status='CLOSED' AND closed_at>=?)
            )
        """
        params = [POLICY_VERSION, start.isoformat(), position_cutoff, start.isoformat()]
    sql += " ORDER BY opened_at, id"
    cur.execute(sql, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    if not rows:
        return None

    success = fail = neutral = active = 0
    title = (
        "🤖 BOT POZİSYON TAKİP RAPORU"
        if scope == "POSITION"
        else "📣 KANAL GÜN İÇİ RAPORU"
        if scope == "INTRADAY"
        else "📊 GÜN SONU RAPORU"
    )
    verified_day = str(verified_close_date)[:10] if verified_close_date else None
    lines = [f"<b>{title}</b>"]
    if verified_day and scope in {"POSITION", "INTRADAY"}:
        lines.append(f"📅 Kapanış teyidi: {verified_day} · Yahoo günlük OHLCV")
    lines.append("")
    for row in rows:
        symbol = str(row.get("symbol") or "").replace(".IS", "")
        entry = _price_text(row.get("entry_price"))
        first_target_hit = bool(row.get("tp1_hit"))
        closed_at = row.get("closed_at")
        open_as_of_report = (
            row.get("status") != "CLOSED"
            or (closed_at and str(closed_at) >= end.isoformat())
        )
        if open_as_of_report:
            active += 1
            current = None
            try:
                path = json.loads(row.get("tracking_path_json") or "[]")
                if path:
                    if verified_day and row.get("scope") in {"POSITION", "INTRADAY"}:
                        final_day = next(
                            (item for item in path if item.get("date") == verified_day),
                            None,
                        )
                        if final_day and final_day.get("close_source") == "YAHOO_DAILY_CLOSE":
                            current = final_day.get("last")
                    else:
                        current = path[-1].get("last")
            except Exception:
                current = None
            if current and (
                row.get("scope") == "POSITION"
                or (verified_day and row.get("scope") == "INTRADAY")
            ):
                move = _pct(float(current), float(row.get("entry_price") or 0))
                day_count = int(row.get("tracking_day_count") or 0)
                if first_target_hit:
                    success += 1
                    lines.append(
                        f"✅ {symbol} | {entry} → {_price_text(current)} | "
                        f"{move:+.2f}% · 1. hedef gün içinde görüldü | Takip {day_count}/10"
                    )
                else:
                    lines.append(
                        f"⏳ {symbol} | {entry} → {_price_text(current)} | "
                        f"{move:+.2f}% | Takip {day_count}/10"
                    )
            elif verified_day and row.get("scope") in {"POSITION", "INTRADAY"}:
                day_count = int(row.get("tracking_day_count") or 0)
                if first_target_hit:
                    success += 1
                    lines.append(
                        f"✅ {symbol} | {entry} → kapanış doğrulanamadı | "
                        f"1. hedef gün içinde görüldü | Takip {day_count}/10"
                    )
                else:
                    lines.append(
                        f"⏳ {symbol} | {entry} → kapanış doğrulanamadı | Takip {day_count}/10"
                    )
            else:
                if first_target_hit:
                    success += 1
                    lines.append(f"✅ {symbol} | {entry} → - | 1. hedef gün içinde görüldü")
                else:
                    lines.append(f"⏳ {symbol} | {entry} → -")
            continue
        result = row.get("net_result_pct")
        if result is None:
            result = row.get("result_pct")
        result = float(result or 0.0)
        exit_price = _price_text(row.get("exit_price"))
        if first_target_hit:
            success += 1
            marker = "✅"
            outcome = " · 1. hedef gün içinde görüldü"
        elif abs(result) < 0.005:
            neutral += 1
            marker = "➖"
            outcome = ""
        elif result > 0:
            success += 1
            marker = "✅"
            outcome = ""
        else:
            fail += 1
            marker = "❌"
            outcome = ""
        lines.append(
            f"{marker} {symbol} | {entry} → {exit_price} | {result:+.2f}%{outcome}"
        )

    total = len(rows)
    success_rate = success / total * 100.0 if total else 0.0
    lines.extend([
        "", "📊 <b>ÖZET</b>",
        f"Toplam: {total}",
        f"✅ Başarılı: {success}",
        f"❌ Başarısız: {fail}",
        f"➖ Nötr: {neutral}",
        f"⏳ Açık: {active}",
        "", f"📈 Başarı Oranı: %{success_rate:.1f}",
        "", f"🕒 {now.strftime('%H:%M')}",
    ])
    return "\n".join(lines)


def build_v4_weekly_report():
    now = _now()
    start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return _period_report(start, "HAFTALIK SEÇİCİ SİNYAL RAPORU")
