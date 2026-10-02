"""Deterministic admin-only fixture rendered by the production card engine."""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from signal_card_v3 import build_signal_card

TR_TZ = ZoneInfo("Europe/Istanbul")


def build_test_signal_card(latest=None):
    latest = dict(latest or {})
    base = float(latest.get("entry_price") or 105.80)
    count = 72
    x = np.arange(count, dtype=float)
    close = base * 0.91 + x * (base * 0.00095) + np.sin(x / 3.2) * (base * 0.012)
    close[-9:] += np.linspace(0.0, base * 0.032, 9)
    volume = np.full(count, 900_000.0)
    volume[-9:] = np.linspace(1_100_000, 1_900_000, 9)
    index = pd.date_range(end=datetime.now(TR_TZ), periods=count, freq="4h")
    frame = pd.DataFrame({
        "Open": close - np.sin(x) * base * 0.0025,
        "High": close + base * 0.006,
        "Low": close - base * 0.006,
        "Close": close,
        "Volume": volume,
    }, index=index)
    entry = round(float(close[-1]), 2)
    risk = round(entry * 0.039, 2)
    signal = {
        **latest,
        "symbol": latest.get("symbol") or "TEST.IS",
        "signal_scope": "POSITION",
        "main_algorithm": latest.get("main_algorithm") or "TREND_START_V3",
        "score": latest.get("score") or 88,
        "entry_price": entry,
        "current_price": round(entry * 1.002, 2),
        "stop_loss": round(entry - risk, 2),
        "tp1": round(entry + risk * 1.5, 2),
        "tp2": round(entry + risk * 3.0, 2),
        "tp3": round(entry + risk * 5.0, 2),
        "risk_pct": 3.9,
        "rsi_4h": latest.get("rsi_4h") or 57.8,
        "rsi_1d": latest.get("rsi_1d") or 61.2,
        "relative_strength_percentile": latest.get("relative_strength_percentile") or 91,
        "session_rvol": latest.get("session_rvol") or 1.74,
        "day_change_pct": latest.get("day_change_pct") or 3.25,
        "market_structure_phase": "STARTING",
        "technical_confirmations": [
            "4H yapı kırılımı ve hacim teyidi (TEST)",
            "4H MOST yukarı dönüş (TEST)",
            "Fiyat-hacim birikim izi (TEST)",
        ],
        "technical_warnings": ["Bu kart yalnız görsel ve Telegram teslimat testidir"],
        "technical_structures": {
            "support_resistance": {"4H": {
                "support": round(entry - risk * 0.65, 2),
                "resistance": round(entry + risk * 1.05, 2),
            }}
        },
        "calibration_status": "TEST",
        "calibration_sample_size": 0,
        "time": datetime.now(TR_TZ).strftime("%H:%M"),
    }
    hx = np.arange(120, dtype=float)
    hourly_close = base * 0.97 + hx * (base * 0.00025) + np.sin(hx / 4.2) * (base * 0.003)
    hourly_close[-18:] += np.linspace(0.0, base * 0.02, 18)
    hourly_volume = np.full(120, 320_000.0)
    hourly_volume[-18:] = np.linspace(450_000, 920_000, 18)
    hourly_index = pd.date_range(end=datetime.now(TR_TZ), periods=120, freq="1h")
    hourly_frame = pd.DataFrame({
        "Open": hourly_close - np.sin(hx) * base * 0.001,
        "High": hourly_close + base * 0.003,
        "Low": hourly_close - base * 0.003,
        "Close": hourly_close,
        "Volume": hourly_volume,
    }, index=hourly_index)
    item = {"tf": {"4h": {"df": frame}, "1h": {"df": hourly_frame}}}
    path = build_signal_card(signal, item, state={"stage": "TEST", "history": []})
    return path, signal
