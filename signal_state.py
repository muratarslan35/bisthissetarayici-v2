import json
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_connection

TR_TZ = ZoneInfo("Europe/Istanbul")

_STAGE_LEVEL = {
    "ADAY": 1,
    "GÜÇLÜ": 2,
    "ÇOK GÜÇLÜ": 3,
    "UZAMIŞ": 4,
}


def _now():
    return datetime.now(TR_TZ)


def _num(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def init_signal_state():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_states (
            symbol TEXT NOT NULL,
            scope TEXT NOT NULL,
            algorithm TEXT,
            stage TEXT NOT NULL,
            score REAL NOT NULL,
            entry_price REAL,
            last_price REAL,
            last_sent_at TEXT,
            trading_day TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            history_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(symbol, scope)
        )
        """
    )
    conn.commit()
    conn.close()


def classify_stage(signal):
    score = _num(signal.get("score"), 0.0) or 0.0
    day_change = _num(signal.get("day_change_pct"), 0.0) or 0.0
    rsi4 = _num(signal.get("rsi_4h"))
    rsi15 = _num(signal.get("rsi_15m"))

    if day_change >= 9.2 or (rsi4 is not None and rsi4 >= 76) or (
        rsi15 is not None and rsi15 >= 80
    ):
        return "UZAMIŞ"
    if score >= 90:
        return "ÇOK GÜÇLÜ"
    if score >= 83:
        return "GÜÇLÜ"
    return "ADAY"


def _event_text(signal, stage, previous=None):
    algo = str(signal.get("main_algorithm") or "").replace("_V3", "").replace("_", " ")
    score = round(_num(signal.get("score"), 0.0) or 0.0, 1)
    if previous is None:
        return f"{stage} sinyal oluştu · {algo} · {score}/100"

    prev_score = round(_num(previous.get("score"), 0.0) or 0.0, 1)
    if _STAGE_LEVEL.get(stage, 0) > _STAGE_LEVEL.get(previous.get("stage"), 0):
        return f"Güç seviyesi {previous.get('stage')} → {stage} · {prev_score} → {score}"
    return f"Sinyal gücü arttı · {prev_score} → {score}"


def _load_history(raw):
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def assess_signal_transition(signal):
    """
    Return NEW / UPGRADE / NONE without imposing a market-wide signal quota.

    Spam control is state based:
      - one active setup per symbol/scope,
      - upgrade only when strength materially improves,
      - a new trading day re-arms the symbol,
      - near-ceiling/overextended states never become new buy signals.
    """
    init_signal_state()

    symbol = str(signal.get("symbol") or "")
    scope = str(signal.get("signal_scope") or "")
    if not symbol or scope not in {"POSITION", "INTRADAY"}:
        return {"action": "NONE", "reason": "invalid"}

    now = _now()
    day = now.date().isoformat()
    stage = classify_stage(signal)
    score = _num(signal.get("score"), 0.0) or 0.0
    price = _num(signal.get("current_price") or signal.get("entry_price"))

    if stage == "UZAMIŞ":
        return {
            "action": "NONE",
            "reason": "extended",
            "stage": stage,
            "history": [],
        }

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM signal_states WHERE symbol=? AND scope=?",
        (symbol, scope),
    )
    row = cur.fetchone()
    previous = dict(row) if row else None

    if previous and previous.get("trading_day") == day and int(previous.get("active") or 0) == 1:
        prev_score = _num(previous.get("score"), 0.0) or 0.0
        prev_stage = previous.get("stage") or "ADAY"
        history = _load_history(previous.get("history_json"))

        stronger_stage = _STAGE_LEVEL.get(stage, 0) > _STAGE_LEVEL.get(prev_stage, 0)
        meaningful_score_gain = score >= prev_score + 7.0

        if stronger_stage or meaningful_score_gain:
            history.append(
                {
                    "time": now.strftime("%H:%M"),
                    "text": _event_text(signal, stage, previous),
                }
            )
            history = history[-6:]
            cur.execute(
                """
                UPDATE signal_states
                   SET algorithm=?, stage=?, score=?, last_price=?,
                       last_sent_at=?, history_json=?, updated_at=?
                 WHERE symbol=? AND scope=?
                """,
                (
                    signal.get("main_algorithm"),
                    stage,
                    score,
                    price,
                    now.isoformat(),
                    json.dumps(history, ensure_ascii=False),
                    now.isoformat(),
                    symbol,
                    scope,
                ),
            )
            conn.commit()
            conn.close()
            return {
                "action": "UPGRADE",
                "stage": stage,
                "previous_stage": prev_stage,
                "previous_score": prev_score,
                "history": history,
            }

        cur.execute(
            """
            UPDATE signal_states
               SET score=?, last_price=?, updated_at=?
             WHERE symbol=? AND scope=?
            """,
            (max(score, prev_score), price, now.isoformat(), symbol, scope),
        )
        conn.commit()
        conn.close()
        return {
            "action": "NONE",
            "reason": "same_active_setup",
            "stage": prev_stage,
            "history": history,
        }

    history = [
        {
            "time": now.strftime("%H:%M"),
            "text": _event_text(signal, stage, None),
        }
    ]
    cur.execute(
        """
        INSERT INTO signal_states (
            symbol, scope, algorithm, stage, score, entry_price, last_price,
            last_sent_at, trading_day, active, history_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(symbol, scope) DO UPDATE SET
            algorithm=excluded.algorithm,
            stage=excluded.stage,
            score=excluded.score,
            entry_price=excluded.entry_price,
            last_price=excluded.last_price,
            last_sent_at=excluded.last_sent_at,
            trading_day=excluded.trading_day,
            active=1,
            history_json=excluded.history_json,
            updated_at=excluded.updated_at
        """,
        (
            symbol,
            scope,
            signal.get("main_algorithm"),
            stage,
            score,
            _num(signal.get("entry_price")),
            price,
            now.isoformat(),
            day,
            json.dumps(history, ensure_ascii=False),
            now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "action": "NEW",
        "stage": stage,
        "history": history,
    }


def mark_signal_state_closed(symbol, scope, reason=None):
    init_signal_state()
    now = _now()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT history_json FROM signal_states WHERE symbol=? AND scope=?",
        (symbol, scope),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return

    history = _load_history(row["history_json"])
    history.append(
        {
            "time": now.strftime("%H:%M"),
            "text": f"Takip kapandı · {reason or 'işlem sonlandı'}",
        }
    )
    cur.execute(
        """
        UPDATE signal_states
           SET active=0, history_json=?, updated_at=?
         WHERE symbol=? AND scope=?
        """,
        (
            json.dumps(history[-6:], ensure_ascii=False),
            now.isoformat(),
            symbol,
            scope,
        ),
    )
    conn.commit()
    conn.close()


def get_signal_state(symbol, scope):
    init_signal_state()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM signal_states WHERE symbol=? AND scope=?",
        (symbol, scope),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    out = dict(row)
    out["history"] = _load_history(out.pop("history_json", "[]"))
    return out
