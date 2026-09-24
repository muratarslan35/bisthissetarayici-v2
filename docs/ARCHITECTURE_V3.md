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


## Dashboard V3

The dashboard is durable and process-safe. It does not rely on Python globals shared
between the Flask web process and scanner worker.

Data flow:

```text
scanner worker
   │
   ├─ market snapshot/context ──> dashboard_runtime + market_prices
   ├─ signals ──────────────────> strategy_signals
   └─ paper lifecycle ──────────> paper_trades
                                      │
                                      ▼
                              Flask /api/dashboard
                                      │
                                      ▼
                               dashboard.html
```

The dashboard exposes:
- worker heartbeat and market-open state
- market regime and breadth
- configured universe size and data source
- open POSITION and INTRADAY paper trades
- current observed price and live return
- stop / TP1 / TP2 / TP3 / trailing state
- relative-strength percentile
- data confidence / data age
- MFE / MAE
- 30-day per-algorithm closed-trade statistics
- recent closed paper trades

`/api/dashboard` requires an authenticated application session.

SQLite is configured with WAL and a busy timeout so the read-heavy web process can
coexist with scanner writes. The worker persists only refreshed market snapshots and
processes the full universe only when a new snapshot or fresh KAP event exists.



## KAP Ingestion V3

KAP data is ingested once through a central verified service. Trading logic and
restriction filters do not maintain separate social-media/RSS readers.

### Current free source

The former `/tr/rss/all` endpoint returns HTTP 404 on the current KAP site and is not
used.

Primary discovery uses the public JSON backend used by the KAP web application:

```text
POST https://www.kap.org.tr/tr/api/disclosure/members/byCriteria
```

Two member groups are queried:
- `IGS` — listed-company disclosures, primary trading source.
- `DDK` — regulatory/market disclosures, lower-frequency source used by restriction
  workflows such as brüt-takas monitoring.

The project does **not** depend on KAP's separate subscriber data-dissemination product.
Third-party Twitter/Nitter feeds are not accepted as trade-event sources.

### Structured parsing

The list API returns structured fields such as:
- `disclosureIndex`
- `publishDate`
- `kapTitle`
- `subject`
- `summary`
- `stockCodes`
- `relatedStocks`
- disclosure class/type/category

Symbol mapping and publication time therefore come from JSON fields rather than title
string slicing or a brittle HTML table parser.

### Second-level verification

New trade-relevant disclosures can be reconciled through:

```text
GET /tr/api/notification/attachment-detail/<disclosureIndex>
```

If that endpoint is temporarily unavailable, the official
`/tr/Bildirim/<disclosureIndex>` page is a last-resort verification layer.

A detail failure does not invent or create an event: only an event discovered through
the official list API can enter `kap_events`.

### Verification and dedupe

A disclosure is accepted only when:
- the response remains on `kap.org.tr`,
- a numeric `disclosureIndex` exists,
- `publishDate` parses to an authoritative timestamp,
- at least one structured stock code maps to the configured BIST universe.

The durable key is `<disclosureIndex>:<symbol>`.

All accepted events are stored in `kap_events`; source health is stored in
`kap_source_health`.

### Freshness by strategy

- **INTRADAY / channel:** verified KAP event must be no more than 30 minutes old.
- **POSITION / bot:** verified after-close events may remain eligible into the next
  trading session (up to 18 hours).

The worker monitors KAP even while the equity market is closed, so an after-hours filing
is not lost.

### Request discipline

- IGS: about once per minute during active hours.
- DDK: about once every five minutes.
- Off-hours IGS cadence is reduced.
- failures trigger exponential backoff.
- process startup queries today and yesterday separately once, preventing after-close
  data loss after a restart while avoiding oversized multi-day responses.

### Health and observability

The dashboard exposes:
- overall KAP health,
- `KAP_API_IGS` / `KAP_API_DDK` status,
- detail API / HTML fallback status when used,
- last successful request,
- HTTP status and latency,
- parsed and verified counts,
- consecutive failures,
- verified events in the last 24 hours,
- recent KAP IDs and detail-verification state.

`/health` exposes aggregate KAP status without disclosure contents.

### Brüt-takas integration

Brüt-takas logic consumes KAP IDs already verified by the central KAP service and no
longer owns a second feed reader.

Restriction extraction uses a deterministic parser first. Gemini can enrich/fallback,
but AI is not the sole basis for a restriction flag.

### Runtime flow

```text
KAP public JSON API
  IGS 60s / DDK 5m
         |
         v
schema + host validation
         |
         v
disclosureIndex + publishDate + stockCodes
         |
         +------> kap_events (audit / dedupe)
         |
         +------> optional detail API verification
         |
         +------> recent verified cache
                      |
             +--------+--------+
             |                 |
      POSITION engine     INTRADAY engine
```
