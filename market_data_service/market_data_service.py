"""
market_data_service.py
=======================
Near-real-time equities + bond mark-to-model pricing service.
Implements the 5-part architecture from your data-pipeline-architecture.md:

    1. Scheduler        — APScheduler, every ~2 min, market hours only
    2. Equities leg      — Webull snapshot → equity_prices
    3. Rate-proxy leg     — UST futures snapshot → rate_proxy_prices
    4. Bond/MBS repricing — duration/convexity model → bond_prices
    5. Reconciliation     — daily TRACE EOD + weekly PMMS re-anchor

Run:
    pip3 install fastapi uvicorn apscheduler requests
    # + webullsdkcore webullsdkquotescore webullsdktrade once you have API access

    export WEBULL_APP_KEY=...
    export WEBULL_APP_SECRET=...
    python3 market_data_service.py

Then:
    GET http://localhost:8090/prices/equities
    GET http://localhost:8090/prices/bonds
    GET http://localhost:8090/prices/rate-proxy
    GET http://localhost:8090/health

This runs alongside your existing pipeline.py / fetch_stocks.py — it
does not replace them. pipeline.py still owns the daily FRED/PMMS
signals baked into the dashboard by inject_signals.py. This service
adds the 2-minute intraday layer on top.
"""

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import market_db
import webull_client
import bond_repricer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("market_data_service")

# Where your portfolio positions live. Point this at wherever your
# dashboard's position book is exported from — for now it reads a
# simple JSON list; wire it to your real positions store when ready.
POSITIONS_JSON = Path(os.environ.get("POSITIONS_JSON", "positions_live.json"))

# In-memory anchor prices, seeded by reconciliation jobs (falls back to
# the first observed price on cold start so the pipeline doesn't stall
# waiting for a reconciliation run).
_anchor_price_map = {}


def load_positions():
    if not POSITIONS_JSON.exists():
        log.warning(f"{POSITIONS_JSON} not found — bond repricing leg will be skipped this cycle")
        return []
    return json.loads(POSITIONS_JSON.read_text())


# ─────────────────────────────────────────────────────────────────────────
# Scheduled jobs
# ─────────────────────────────────────────────────────────────────────────

def equities_and_rateproxy_and_bonds_cycle():
    """Steps 2, 3, 4 — runs every ~2 minutes, market hours only."""
    if not webull_client.market_hours_now():
        log.info("Outside market hours — skipping cycle")
        return

    # Step 2 — equities leg
    try:
        equity_rows = webull_client.fetch_equity_snapshots()
        if equity_rows:
            market_db.insert_equity_prices(equity_rows)
            log.info(f"Equities: wrote {len(equity_rows)} rows")
    except RuntimeError as e:
        log.warning(f"Equities leg skipped: {e}")
        equity_rows = []

    # Step 3 — rate-proxy leg
    try:
        rate_rows = webull_client.fetch_rate_proxy_snapshots()
        if rate_rows:
            market_db.insert_rate_proxy_prices(rate_rows)
            log.info(f"Rate-proxy: wrote {len(rate_rows)} rows")
            for row in rate_rows:
                _anchor_price_map.setdefault(row["contract"], row["price"])
    except RuntimeError as e:
        log.warning(f"Rate-proxy leg skipped: {e}")
        rate_rows = []

    # Step 4 — bond/MBS repricing leg
    if rate_rows:
        positions = load_positions()
        if positions:
            bond_rows = bond_repricer.reprice_portfolio(positions, rate_rows, _anchor_price_map)
            if bond_rows:
                market_db.insert_bond_prices(bond_rows)
                log.info(f"Bonds: repriced {len(bond_rows)} positions")


def reconcile_trace_eod():
    """Step 5a — daily. Placeholder: wire to your TRACE pull once you
    have FINRA TRACE access configured (see pipeline_README.md's note
    on agency-MBS OAS). Re-anchors _anchor_price_map so intraday drift
    resets against the real end-of-day print."""
    log.info("Running daily TRACE EOD reconciliation (placeholder)")
    # TODO: pull TRACE EOD prints, compare to model_price, adjust anchor
    market_db.log_reconciliation("trace_eod", "ok", "placeholder — TRACE pull not yet wired")


def reconcile_pmms_weekly():
    """Step 5b — Thursdays, when Freddie Mac releases PMMS. Re-anchors
    the rate-proxy baseline against the authoritative weekly print
    (same series pipeline.py already pulls from FRED)."""
    log.info("Running weekly PMMS reconciliation (placeholder)")
    # TODO: read latest pmms30 value from signals_live.json, re-anchor
    market_db.log_reconciliation("pmms_weekly", "ok", "placeholder — PMMS re-anchor not yet wired")


# ─────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────

app = FastAPI(title="MortgageIQ Market Data Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this to your GitHub Pages origin before going further than local dev
    allow_methods=["GET"],
)

scheduler = BackgroundScheduler()


@app.on_event("startup")
def startup():
    market_db.init_db()

    # Step 1 — scheduler, every 2 min, market hours only (job itself
    # also checks market_hours_now() so cron doesn't need DST logic)
    scheduler.add_job(
        equities_and_rateproxy_and_bonds_cycle,
        trigger="interval",
        minutes=2,
        id="market_cycle",
        next_run_time=datetime.now(timezone.utc),  # fire once immediately on boot
    )
    scheduler.add_job(
        reconcile_trace_eod,
        trigger=CronTrigger(hour=17, minute=0, day_of_week="mon-fri"),
        id="trace_eod",
    )
    scheduler.add_job(
        reconcile_pmms_weekly,
        trigger=CronTrigger(hour=11, minute=0, day_of_week="thu"),
        id="pmms_weekly",
    )
    scheduler.start()
    log.info("Scheduler started — 2min market cycle, daily TRACE recon, weekly PMMS recon")


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "market_hours": webull_client.market_hours_now(),
        "anchor_prices": _anchor_price_map,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/prices/equities")
def get_equities():
    return market_db.latest_equity_prices()


@app.get("/prices/rate-proxy")
def get_rate_proxy():
    return market_db.latest_rate_proxy()


@app.get("/prices/bonds")
def get_bonds():
    return market_db.latest_bond_prices()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
