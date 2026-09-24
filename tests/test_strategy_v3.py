import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import strategy_v3 as sv3


TR_TZ = ZoneInfo("Europe/Istanbul")


def daily_frame(periods=260, start=80.0, end=140.0, volume=1_000_000):
    idx = pd.date_range("2025-01-02", periods=periods, freq="B", tz="UTC")
    close = np.linspace(start, end, periods)
    return pd.DataFrame({
        "Open": close * 0.998,
        "High": close * 1.006,
        "Low": close * 0.994,
        "Close": close,
        "Volume": np.full(periods, volume, dtype=float),
    }, index=idx)


def intraday_frame(days=6, bars_per_day=32, base=100.0):
    rows = []
    idx = []
    price = base
    start = pd.Timestamp("2026-09-14 10:00", tz=TR_TZ)

    for day in range(days):
        date = (start + pd.Timedelta(days=day)).date()
        if pd.Timestamp(date).weekday() >= 5:
            continue
        for bar in range(bars_per_day):
            ts = pd.Timestamp.combine(date, pd.Timestamp("10:00").time()).tz_localize(TR_TZ)
            ts += pd.Timedelta(minutes=15 * bar)
            step = 0.0008 + (0.00008 * bar if day == days - 1 else 0)
            new_price = price * (1 + step)
            rows.append({
                "Open": price,
                "High": new_price * 1.002,
                "Low": price * 0.998,
                "Close": new_price,
                "Volume": 500_000 + (bar * 5_000),
            })
            idx.append(ts.tz_convert("UTC"))
            price = new_price

    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    return df


def item(symbol, daily=None, intraday=None):
    daily = daily if daily is not None else daily_frame()
    intraday = intraday if intraday is not None else intraday_frame()

    d1h = intraday.resample("1h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    d4h = intraday.resample("4h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()

    c15 = intraday["Close"]
    return {
        "symbol": symbol,
        "current_price": float(daily["Close"].iloc[-1] * 1.01),
        "tf": {
            "15m": {
                "df": intraday,
                "ema20": float(c15.ewm(span=20, adjust=False).mean().iloc[-1]),
                "ema50": float(c15.ewm(span=50, adjust=False).mean().iloc[-1]),
            },
            "1h": {"df": d1h},
            "4h": {"df": d4h},
            "1d": {"df": daily},
        },
    }


class StrategyV3Tests(unittest.TestCase):
    def setUp(self):
        sv3._LAST_SENT.clear()

    def test_session_vwap_uses_latest_session_only(self):
        df = intraday_frame()
        local = df.copy()
        local.index = local.index.tz_convert(TR_TZ)
        last_date = local.index[-1].date()
        session = local[local.index.date == last_date]
        typical = (session["High"] + session["Low"] + session["Close"]) / 3
        expected = float((typical * session["Volume"]).sum() / session["Volume"].sum())

        with patch("strategy_v3._now", return_value=datetime(2026, 9, 19, 14, 0, tzinfo=TR_TZ)):
            actual = sv3.session_vwap(df)

        self.assertAlmostEqual(actual, expected, places=8)

    def test_market_context_ranks_stronger_symbol_higher(self):
        weak = item("WEAK.IS")
        strong = item("STRONG.IS")

        strong_daily = strong["tf"]["1d"]["df"].copy()
        strong_daily.loc[strong_daily.index[-25]:, "Close"] *= np.linspace(1.0, 1.12, 25)
        strong["tf"]["1d"]["df"] = strong_daily

        strong_15 = strong["tf"]["15m"]["df"].copy()
        strong_15.loc[strong_15.index[-8]:, "Close"] *= np.linspace(1.0, 1.04, 8)
        strong["tf"]["15m"]["df"] = strong_15

        ctx = sv3.build_market_context([weak, strong])
        self.assertGreater(
            ctx["position_rank"]["STRONG.IS"],
            ctx["position_rank"]["WEAK.IS"],
        )
        self.assertGreater(
            ctx["intraday_rank"]["STRONG.IS"],
            ctx["intraday_rank"]["WEAK.IS"],
        )

    def test_position_engine_is_independent_from_intraday_engine(self):
        x = item("TEST.IS")
        d1 = x["tf"]["1d"]["df"]
        breakout = d1["High"].iloc[-21:-1].max()
        x["current_price"] = float(breakout * 1.002)

        ctx = {
            "regime": "RISK_ON",
            "breadth_intraday": 0.7,
            "breadth_daily": 0.7,
            "position_rank": {"TEST.IS": 0.98},
            "intraday_rank": {"TEST.IS": 0.98},
        }

        with patch("strategy_v3._now", return_value=datetime(2026, 9, 24, 14, 0, tzinfo=TR_TZ)):
            position = sv3.evaluate_position_signals(x, ctx)
            intraday = sv3.evaluate_intraday_signals(x, ctx)

        self.assertIsInstance(position, list)
        self.assertIsInstance(intraday, list)
        for sig in position:
            self.assertEqual(sig["signal_scope"], "POSITION")
        for sig in intraday:
            self.assertEqual(sig["signal_scope"], "INTRADAY")

    def test_risk_levels_are_not_fixed_plus_one_minus_five(self):
        risk = sv3._risk_levels(100.0, 2.0, structural=98.7, position=False)
        self.assertLess(risk["stop_loss"], 100.0)
        self.assertGreater(risk["tp1"], 100.0)
        self.assertGreater(risk["tp2"], risk["tp1"])
        self.assertGreater(risk["tp3"], risk["tp2"])
        self.assertNotEqual(risk["stop_loss"], 95.0)


if __name__ == "__main__":
    unittest.main()
