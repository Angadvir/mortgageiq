# MortgageIQ — Live Market Data Service

Implements the 2-minute-cadence equities + bond mark-to-model pricing layer
from your architecture notes. Runs **alongside** `pipeline.py` — it doesn't
replace your FRED/PMMS daily signal pipeline, it adds an intraday layer on top.

## Files

```
market_data_service/
├── market_data_service.py   FastAPI app + APScheduler jobs (the 5-part architecture)
├── webull_client.py         Wraps Webull's OpenAPI market-data SDK (gRPC-based)
├── bond_repricer.py         Duration/convexity mark-to-model repricing
├── market_db.py             SQLite storage (schema matches your TimescaleDB spec)
└── requirements.txt
```

## One correction to the original architecture notes

Webull's market-data **snapshot** call is **gRPC**, not plain REST — HTTP
covers trading/account endpoints, MQTT covers streaming, and snapshots
specifically go through the `webullsdkquotescore` gRPC client. Your 2-minute
polling cadence is unaffected — you're just calling a gRPC method each
cycle instead of hitting a REST URL. `webull_client.py` is built against
this real interface.

## Setup

```bash
cd market_data_service
pip3 install -r requirements.txt

# Once your Webull OpenAPI application is approved (1-2 business days,
# or use the shared sandbox credentials to start testing immediately):
pip3 install webullsdkcore webullsdkquotescore webullsdktrade

export WEBULL_APP_KEY=your_app_key
export WEBULL_APP_SECRET=your_app_secret

python3 market_data_service.py
```

Runs on `http://localhost:8090`. Endpoints:

| Endpoint              | Returns                                  |
|-----------------------|-------------------------------------------|
| `GET /health`          | Service status, market-hours flag, anchors |
| `GET /prices/equities` | Latest snapshot per originator ticker      |
| `GET /prices/rate-proxy` | Latest ZN/ZB/TN/UB futures snapshot      |
| `GET /prices/bonds`   | Latest model_price/OAS per position        |

## What's still a placeholder — and why

1. **TRACE EOD reconciliation** (`reconcile_trace_eod`) — needs your FINRA
   TRACE access wired in. Right now it just logs a placeholder row so the
   scheduler shape is correct; plug in the actual pull per
   `pipeline_README.md`'s note on `mbsSpread`.

2. **PMMS weekly re-anchor** (`reconcile_pmms_weekly`) — should read the
   `pmms30` value your existing `pipeline.py` already fetches from FRED
   each Thursday and use it to re-anchor the rate-proxy baseline. Left as
   a placeholder since it depends on your `signals_live.json` file path.

3. **Futures BPV table** in `bond_repricer.py` is a static approximation
   (`FUTURES_BPV`). The cheapest-to-deliver DV01 for ZN/ZB/TN/UB actually
   shifts over time as CTD switches — fine for a simulation/personal
   pipeline, not precise enough to trade off directly.

4. **OAS drift** in `reprice_position()` is a simple directional
   placeholder (`-yield_delta_bps * 0.15`), not a real prepayment-adjusted
   OAS. When you're ready, swap the internals of `reprice_position()` for
   a QuantLib `MortgageBackedSecurity`/CMS pricer — the function signature
   (position in, model_price + oas out) doesn't need to change, so nothing
   upstream breaks.

5. **`positions_live.json`** — the service expects a simple JSON list of
   position dicts (`position_id`, `price`, `oas_bps`, `mod_duration`,
   `convexity`, `face_value`). Point `POSITIONS_JSON` at wherever your
   dashboard's real position book is exported from, or add an export step
   to your deploy pipeline.

## Wiring into your existing dashboard

Once this is running, the cleanest integration is a small addition to
`inject_signals.py` (or a sibling script) that:

1. Calls `GET http://localhost:8090/prices/equities` and
   `GET http://localhost:8090/prices/bonds`
2. Writes the result into the same sentinel-comment pattern
   (`@@SIGNALS_START@@` / `@@SIGNALS_END@@`) your dashboard already reads

That keeps the dashboard's "single self-contained HTML file" design intact —
this service just becomes another local data source feeding the same
injection pipeline, run right before `inject_signals.py` in `deploy.sh`.

## Moving from SQLite to TimescaleDB later

The schema in `market_db.py` is deliberately shaped to match the
TimescaleDB spec 1:1. To move to Postgres+TimescaleDB when you outgrow
SQLite (e.g. you start running this on a VPS instead of a Mac that sleeps
overnight):

1. `docker run -d --name timescaledb -p 5432:5432 -e POSTGRES_PASSWORD=... timescale/timescaledb:latest-pg16`
2. Swap `sqlite3.connect(...)` for `psycopg2`/`SQLAlchemy` against that connection
3. Run `SELECT create_hypertable('equity_prices', 'ts');` (and same for the other two time-series tables)

No changes needed to `webull_client.py`, `bond_repricer.py`, or the
FastAPI route handlers — only `market_db.py`'s connection layer.
