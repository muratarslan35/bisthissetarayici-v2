# Professional Technical Engine — 27 September 2026

## Purpose

The POSITION bot must behave like a disciplined swing trader monitoring the
market, not like an indicator voting machine. A trade still requires one of
the existing V3 setups. The technical engine determines whether the move is
starting, is still early, or is already extended, then applies only evidence
relevant to that phase.

## Decision contract

1. Existing V3 setup: controlled pullback, structural breakout, early trend or
   verified KAP-supported position.
2. Liquidity, data quality, relative strength and market-regime gates.
3. 1H/4H/1D professional structure assessment.
4. Hard rejection for confirmed bearish structure, double-timeframe extension
   or simultaneous 4H/1D MOST-down state.
5. Phase-relevant score adjustment is capped between -15 and +12; many
   correlated indicators cannot inflate a weak setup indefinitely.
6. Only the best POSITION setup for a symbol/snapshot is published. Existing
   cooldown and state-transition controls continue to suppress duplicates.

## Measurements

| Layer | Measurements | Use |
|---|---|---|
| Market structure | confirmed pivots, HH/HL, break of structure, liquidity sweep | beginning/reversal and invalidation |
| Support/resistance | clustered 1H/4H/1D pivots, touch count, ATR distance | bounce, nearby ceiling, stop context |
| Trend lines/channels | pivot-line geometry and log-regression channels | healthy channel position and late-entry risk |
| Patterns | symmetric/ascending/descending triangle, rising/falling wedge, bull flag/pennant | only confirmed breaks receive a score |
| Trend confirmation | persistent 1H/4H/1D MOST, DMI/ADX | direction and trend quality |
| Exhaustion | TD Setup, perfection, TDST and Countdown state | exhaustion risk and reversal context |
| Price/volume | CMF20, OBV/ADL slope, relative volume, candle close location/spread | accumulation, distribution, demand and absorption |
| Value context | swing-anchored VWAP and bar-based volume-profile POC | acceptance above value and support context |
| Candle zones | displacement order block, mitigation/hold and bullish FVG | pullback protection and imbalance context |

## Move phases

- `STARTING`: confirmed support turn, MOST turn or volume-confirmed pattern break.
- `EARLY_TREND`: 4H and 1D MOST are aligned while price has not reached a
  double-timeframe channel extreme.
- `EXTENDED`: both 4H and daily channel positioning indicate late entry, or
  DeMark exhaustion and nearby resistance coincide. No new bot entry.
- `UNCONFIRMED`: professional structure has not confirmed the move. The base
  V3 setup can proceed only if it remains eligible and meets the final score.

## Volume and institutional-flow terminology

The free feeds provide OHLCV bars, not exchange order book, broker distribution
or institution identity. Therefore:

- CMF/OBV/ADL, high-volume demand candles, absorption and anchored VWAP are
  reported as **price-volume accumulation evidence**.
- Order blocks and FVGs are explicitly stored as
  `CANDLE_DERIVED_NOT_L2`.
- Volume profile is bar-based and approximate, not tick-level exchange
  volume-at-price.
- The product must never state that a named institution bought shares or that
  a real limit order exists without licensed L2/broker data.

This distinction prevents false certainty while still extracting the strongest
defensible evidence available from free data.

## Signal presentation

Bot messages show:

- movement phase;
- at most two newly formed professional confirmations;
- at most one monitored risk;
- the existing entry, structural/ATR stop, targets, RSI, relative strength and
  statistical calibration state.

All deeper measurements stay in signal metadata and the ledger for validation.
They are not dumped into Telegram, preventing noisy, unreadable messages.

### Single-image trade terminal

POSITION delivery uses a 1440×1680 professional card:

- top: signal strength, entry, stop, first target, relative strength and 4H RSI;
- center: a large 4H candlestick/volume chart with EMA20/50, regression-channel
  upper/middle/lower bands, validated pivot trend lines, 4H support/resistance,
  entry, stop and first target;
- confirmed triangle/wedge/flag/pennant badge only when the geometry and volume
  confirmation truly exist;
- bottom: full trade plan, compact KPIs, calibration state, up to five formed
  confirmations and one monitored risk;
- footer: explicit OHLCV/L2 data limitation and generation timestamp.

The chart is rendered from the same closed-candle frames and technical metadata
used by the decision engine, so the image cannot invent a confirmation absent
from the signal payload.

## Non-repainting and validation rules

- Structure is calculated from closed candles supplied by V3.
- A geometric pattern receives confirmation only after price crosses its line;
  bullish breaks also require relative volume.
- Unconfirmed patterns remain background context and do not add signal score.
- Execution-cost modeling and net-result validation remain unchanged.
- New evidence fields can be evaluated by algorithm and movement phase before
  thresholds are promoted.

## Isolation

This implementation belongs only to `muratarslan35/bisthissetarayici-v2` and
the dedicated BIST deployment at `89.168.99.141`. It has no IMS repository,
service, database, environment or deployment dependency.
