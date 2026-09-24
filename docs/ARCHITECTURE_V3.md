# BIST Trading V3 Architecture

## Scope

This repository is the BIST scanner/trading project only. It must remain operationally isolated from any IMS project even when both run on the same physical host.

Recommended service isolation:
- dedicated project directory
- dedicated virtual environment
- dedicated systemd services
- dedicated environment file
- dedicated working directory
- dedicated runtime state under this repository's own `data/` directory
- independent CPU/memory limits

## Signal families

### POSITION -> Telegram bot users
Holding horizon: roughly 2-10 trading days.

Strategies:
- `KOMBINE_V3`: trend pullback / re-acceleration
- `SUPER_KOMBINE_V3`: structural daily breakout
- `KAP_POSITION_V3`: fresh event + position-trend confirmation

Primary features:
- 1D / 4H structure
- EMA50 / EMA200 trend
- EMA slope
- 20-day relative strength vs the scanned BIST universe
- daily turnover/liquidity
- daily ATR
- market breadth/regime
- KAP event confirmation

### INTRADAY -> Telegram channel
Positions are intended to be managed and closed intraday.

Strategies:
- `MOMENTUM_IGNITION_V3`: early acceleration after compression
- `INTRADAY_MOMENTUM_V3`: confirmed continuation
- `KAP_EVENT_INTRADAY_V3`: fresh KAP event + intraday confirmation

Primary features:
- cross-sectional intraday relative-strength percentile
- session VWAP (reset per trading session)
- session RVOL using real candle volume, never application polling counts
- volatility compression
- momentum velocity / acceleration
- breakout proximity
- ATR-normalized extension
- market breadth/regime

## Free-data design

V3 is built to minimize external requests.

`market_data_hub.py`:
- Yahoo multi-symbol batch requests
- TTL cache
- one shared snapshot for all algorithms
- exponential backoff after failures
- no per-indicator network calls
- no synthetic "live volume" derived from API polling

Default V3 does not require the automated TradingView price engine. It can be explicitly enabled only as an optional compatibility source.

## Paper trade ledger

Every published V3 signal is persisted.

Tracked fields include:
- entry
- stop
- TP1 / TP2 / TP3
- trailing stop
- maximum favorable excursion (MFE)
- maximum adverse excursion (MAE)
- close reason
- realized price move
- strategy
- signal scope
- market context / feature metadata

TP1 no longer means "strategy success". It tightens paper risk and lets the trade continue toward larger moves.

## Runtime

Recommended production split:

```text
gunicorn app:app        -> web/dashboard
python worker.py        -> scanner/signal worker
```

This prevents web-worker topology from accidentally duplicating the scanner.

## Feature flags

```text
TRADING_V3_ENABLED=1
ENABLE_ULTRA_PRICE_ENGINE=0
YF_BATCH_SIZE=35
YF_INTRADAY_TTL_SECONDS=300
YF_DAILY_TTL_SECONDS=21600
YF_MAX_BACKOFF_SECONDS=900
MIN_AVG_DAILY_TURNOVER_TL=20000000
```

## Safety rule

No strategy promises a future price move. Signals are ranked statistical/technical candidates. All new strategy changes should first be evaluated through the persistent paper-trade ledger before being treated as production-grade execution logic.
