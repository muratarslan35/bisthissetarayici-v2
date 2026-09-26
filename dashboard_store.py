import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_connection
from signal_policy import POLICY_VERSION

TR_TZ = ZoneInfo("Europe/Istanbul")


def _now():
    return datetime.now(TR_TZ)


def init_dashboard_store():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dashboard_runtime (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        worker_heartbeat TEXT,
        market_open INTEGER DEFAULT 0,
        market_regime TEXT,
        regime_score REAL,
        breadth_intraday REAL,
        breadth_daily REAL,
        universe_size INTEGER,
        data_source TEXT,
        snapshot_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS market_prices (
        symbol TEXT PRIMARY KEY,
        price REAL NOT NULL,
        data_source TEXT,
        source_bar_time TEXT,
        data_age_minutes REAL,
        data_confidence REAL,
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_market_prices_updated
    ON market_prices(updated_at)
    """)

    conn.commit()
    conn.close()


def update_worker_heartbeat(market_open=None):
    now = _now().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    if market_open is None:
        cur.execute("""
        INSERT INTO dashboard_runtime (id, worker_heartbeat, updated_at)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            worker_heartbeat = excluded.worker_heartbeat,
            updated_at = excluded.updated_at
        """, (now, now))
    else:
        cur.execute("""
        INSERT INTO dashboard_runtime (id, worker_heartbeat, market_open, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            worker_heartbeat = excluded.worker_heartbeat,
            market_open = excluded.market_open,
            updated_at = excluded.updated_at
        """, (now, 1 if market_open else 0, now))

    conn.commit()
    conn.close()


def persist_market_snapshot(market_data, context, market_open):
    now = _now().isoformat()

    source = None
    if market_data:
        source = market_data[0].get("data_source")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO dashboard_runtime (
        id, worker_heartbeat, market_open, market_regime, regime_score,
        breadth_intraday, breadth_daily, universe_size, data_source,
        snapshot_at, updated_at
    )
    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        worker_heartbeat = excluded.worker_heartbeat,
        market_open = excluded.market_open,
        market_regime = excluded.market_regime,
        regime_score = excluded.regime_score,
        breadth_intraday = excluded.breadth_intraday,
        breadth_daily = excluded.breadth_daily,
        universe_size = excluded.universe_size,
        data_source = excluded.data_source,
        snapshot_at = excluded.snapshot_at,
        updated_at = excluded.updated_at
    """, (
        now,
        1 if market_open else 0,
        context.get("regime") if context else None,
        context.get("regime_score") if context else None,
        context.get("breadth_intraday") if context else None,
        context.get("breadth_daily") if context else None,
        context.get("universe_size") if context else len(market_data or []),
        source,
        now,
        now,
    ))

    rows = []
    for item in market_data or []:
        symbol = item.get("symbol")
        price = item.get("current_price")
        if not symbol or not isinstance(price, (int, float)):
            continue

        bar_time = item.get("source_bar_time")
        rows.append((
            symbol,
            float(price),
            item.get("data_source"),
            str(bar_time) if bar_time is not None else None,
            item.get("data_age_minutes"),
            item.get("data_confidence"),
            now,
        ))

    if rows:
        cur.executemany("""
        INSERT INTO market_prices (
            symbol, price, data_source, source_bar_time,
            data_age_minutes, data_confidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            price = excluded.price,
            data_source = excluded.data_source,
            source_bar_time = excluded.source_bar_time,
            data_age_minutes = excluded.data_age_minutes,
            data_confidence = excluded.data_confidence,
            updated_at = excluded.updated_at
        """, rows)

    conn.commit()
    conn.close()


def _runtime_payload(cur):
    cur.execute("SELECT * FROM dashboard_runtime WHERE id = 1")
    row = cur.fetchone()
    if not row:
        return {
            "worker_active": False,
            "worker_heartbeat": None,
            "market_open": False,
            "market_regime": "UNKNOWN",
            "regime_score": None,
            "breadth_intraday": None,
            "breadth_daily": None,
            "universe_size": 0,
            "data_source": None,
            "snapshot_at": None,
        }

    data = dict(row)
    heartbeat = data.get("worker_heartbeat")
    worker_active = False

    if heartbeat:
        try:
            ts = datetime.fromisoformat(heartbeat)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=TR_TZ)
            worker_active = (_now() - ts) <= timedelta(minutes=3)
        except Exception:
            worker_active = False

    data["worker_active"] = worker_active
    data["market_open"] = bool(data.get("market_open"))
    return data


def _signal_rows(cur, scope, limit=80):
    cur.execute("""
    SELECT
        s.fingerprint,
        s.symbol,
        s.scope,
        s.algorithm,
        s.score,
        s.entry_price,
        s.stop_loss,
        s.tp1,
        s.tp2,
        s.tp3,
        s.market_regime,
        s.rs_percentile,
        s.created_at,
        p.status,
        p.trailing_stop,
        p.tp1_hit,
        p.tp2_hit,
        p.max_price,
        p.min_price,
        p.mfe_pct,
        p.mae_pct,
        p.exit_price,
        p.result_pct,
        p.exit_reason,
        p.opened_at,
        p.expires_at,
        p.closed_at,
        mp.price AS current_price,
        mp.data_confidence,
        mp.data_age_minutes,
        mp.data_source,
        s.metadata_json
    FROM strategy_signals s
    LEFT JOIN paper_trades p ON p.fingerprint = s.fingerprint
    LEFT JOIN market_prices mp ON mp.symbol = s.symbol
    WHERE s.scope = ?
      AND s.policy_version = ?
    ORDER BY s.id DESC
    LIMIT ?
    """, (scope, POLICY_VERSION, int(limit)))

    rows = []
    for row in cur.fetchall():
        d = dict(row)
        try:
            metadata = json.loads(d.pop("metadata_json") or "{}")
        except Exception:
            metadata = {}

        d["quality"] = metadata.get("quality")
        d["reasons"] = metadata.get("reasons", [])
        d["session_rvol"] = metadata.get("session_rvol")
        d["session_vwap"] = metadata.get("session_vwap")
        d["rsi_15m"] = metadata.get("rsi_15m")
        d["rsi_4h"] = metadata.get("rsi_4h")
        d["rsi_1d"] = metadata.get("rsi_1d")
        d["fast_change_60s_pct"] = metadata.get("fast_change_60s_pct")
        d["holding_horizon"] = metadata.get("holding_horizon")
        d["valid_until"] = metadata.get("valid_until")
        d["event_title"] = metadata.get("event_title")

        current = d.get("current_price")
        entry = d.get("entry_price")
        d["live_gain_pct"] = None
        if isinstance(current, (int, float)) and isinstance(entry, (int, float)) and entry:
            d["live_gain_pct"] = round((current - entry) / entry * 100.0, 2)

        rows.append(d)

    return rows


def _performance(cur, days=30):
    since = (_now() - timedelta(days=days)).isoformat()

    cur.execute("""
    SELECT
        scope,
        algorithm,
        COUNT(*) AS closed_trades,
        SUM(CASE WHEN result_pct > 0 THEN 1 ELSE 0 END) AS winning_trades,
        AVG(result_pct) AS avg_result_pct,
        AVG(mfe_pct) AS avg_mfe_pct,
        AVG(mae_pct) AS avg_mae_pct,
        MAX(result_pct) AS best_result_pct,
        MIN(result_pct) AS worst_result_pct
    FROM paper_trades
    WHERE status = 'CLOSED'
      AND policy_version = ?
      AND closed_at >= ?
    GROUP BY scope, algorithm
    ORDER BY scope, avg_result_pct DESC
    """, (POLICY_VERSION, since))

    algorithms = []
    for row in cur.fetchall():
        d = dict(row)
        total = d.get("closed_trades") or 0
        wins = d.get("winning_trades") or 0
        d["win_rate_pct"] = round((wins / total) * 100.0, 1) if total else None
        for key in ("avg_result_pct", "avg_mfe_pct", "avg_mae_pct", "best_result_pct", "worst_result_pct"):
            if d.get(key) is not None:
                d[key] = round(float(d[key]), 2)
        algorithms.append(d)

    cur.execute("""
    SELECT
        scope,
        COUNT(*) AS open_trades,
        AVG(mfe_pct) AS avg_open_mfe_pct,
        AVG(mae_pct) AS avg_open_mae_pct
    FROM paper_trades
    WHERE status = 'OPEN'
      AND policy_version = ?
    GROUP BY scope
    """, (POLICY_VERSION,))

    open_summary = {}
    for row in cur.fetchall():
        d = dict(row)
        open_summary[d["scope"]] = d

    return {
        "window_days": days,
        "algorithms": algorithms,
        "open_summary": open_summary,
    }


def _recent_closed(cur, limit=40):
    cur.execute("""
    SELECT
        symbol, scope, algorithm, entry_price, exit_price, result_pct,
        mfe_pct, mae_pct, exit_reason, opened_at, closed_at
    FROM paper_trades
    WHERE status = 'CLOSED'
      AND policy_version = ?
    ORDER BY id DESC
    LIMIT ?
    """, (POLICY_VERSION, int(limit)))

    return [dict(r) for r in cur.fetchall()]


def get_dashboard_data():
    conn = get_connection()
    cur = conn.cursor()

    payload = {
        "server_time": _now().strftime("%H:%M:%S"),
        "runtime": _runtime_payload(cur),
        "position_signals": _signal_rows(cur, "POSITION", 80),
        "intraday_signals": _signal_rows(cur, "INTRADAY", 100),
        "performance": _performance(cur, 30),
        "recent_closed": _recent_closed(cur, 40),
    }

    conn.close()

    try:
        from kap_service import get_kap_health
        payload["kap"] = get_kap_health()
    except Exception as exc:
        payload["kap"] = {
            "overall": "error",
            "verified_events_24h": 0,
            "sources": [],
            "recent": [],
            "error": str(exc),
        }

    return payload
