"""
webull_client.py
=================
Thin wrapper around Webull's OFFICIAL OpenAPI market-data SDK.

IMPORTANT — one correction to the original architecture note: Webull's
market-data snapshot call is exposed over **gRPC**, not plain REST. HTTP
covers trading/account/candlestick endpoints; MQTT covers streaming;
snapshots specifically go through `webullsdkquotescore` over gRPC. The
2-minute polling cadence you specified still works fine — you just call
the gRPC snapshot method every cycle instead of hitting a REST URL.

Setup (one-time):
    1. Log into webull.com → OpenAPI Management → apply for API access.
       Approval is typically 1-2 business days. A shared demo/sandbox
       app_key + app_secret is available immediately for testing.
    2. pip3 install webullsdkcore webullsdkquotescore webullsdktrade
    3. export WEBULL_APP_KEY=...
       export WEBULL_APP_SECRET=...

This module intentionally keeps the Webull SDK import lazy/optional so
the rest of the pipeline (rate-proxy leg, bond repricing) still runs
even before you have API access approved.
"""

import os
import logging
from datetime import datetime, timezone

log = logging.getLogger("webull_client")

APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
REGION     = os.environ.get("WEBULL_REGION", "us")   # 'us' | 'hk' | 'sg' per developer.webull.<region>

# Originator equity tickers tracked per your architecture notes
ORIGINATOR_TICKERS = ["RKT", "PFSI", "COOP", "UWMC"]

# UST Treasury futures used as the intraday rate-proxy leg
RATE_PROXY_CONTRACTS = ["ZN", "ZB", "TN", "UB"]   # 10Y, 30Y, Ultra-10Y, Ultra-Bond


def _client():
    """Lazily build the gRPC quotes client. Raises a clear error if the
    SDK isn't installed or credentials aren't set, rather than failing
    silently mid-pipeline."""
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError(
            "WEBULL_APP_KEY / WEBULL_APP_SECRET not set. "
            "Apply for OpenAPI access at webull.com → OpenAPI Management, "
            "or use the shared sandbox credentials for testing."
        )
    try:
        from webullsdkquotescore.grpc_client import GrpcApiClient
        from webullsdktrade.grpc_api import API
    except ImportError as e:
        raise RuntimeError(
            "Webull SDK not installed. Run: "
            "pip3 install webullsdkcore webullsdkquotescore webullsdktrade"
        ) from e

    grpc_client = GrpcApiClient(APP_KEY, APP_SECRET, REGION)
    return API(grpc_client)


def fetch_equity_snapshots(tickers=None):
    """
    Pull a batched snapshot for the given tickers (default: originator
    universe). Returns rows shaped for market_db.insert_equity_prices().

    NOTE: rate limit is enforced by Webull at the App ID level — the
    docs currently cite ~1 call/sec for snapshot batches, well within
    a 2-minute polling cadence for a handful of tickers.
    """
    tickers = tickers or ORIGINATOR_TICKERS
    ts = datetime.now(timezone.utc).isoformat()

    api = _client()
    response = api.market_data.get_snapshot(tickers, category="US_STOCK")

    if response.status_code != 200:
        log.warning(f"Webull snapshot error {response.status_code}: {response.text}")
        return []

    result = response.json()
    rows = []
    for quote in result.get("data", []):
        rows.append({
            "ts":     ts,
            "ticker": quote.get("symbol"),
            "price":  quote.get("close") or quote.get("lastPrice"),
            "bid":    quote.get("bidPrice"),
            "ask":    quote.get("askPrice"),
            "volume": quote.get("volume"),
            "source": "webull",
        })
    return rows


def fetch_rate_proxy_snapshots(contracts=None):
    """
    Pull UST Treasury futures snapshots (ZN/ZB/TN/UB) as an intraday
    rate-move proxy between daily FRED/PMMS updates.

    Futures fall under Webull's US_FUTURE category in the same
    get_snapshot() call. yield_equiv is left None here — the conversion
    from futures price to an approximate yield move belongs in
    bond_repricer.py since it depends on which anchor/duration you use.
    """
    contracts = contracts or RATE_PROXY_CONTRACTS
    ts = datetime.now(timezone.utc).isoformat()

    api = _client()
    response = api.market_data.get_snapshot(contracts, category="US_FUTURE")

    if response.status_code != 200:
        log.warning(f"Webull futures snapshot error {response.status_code}: {response.text}")
        return []

    result = response.json()
    rows = []
    for quote in result.get("data", []):
        rows.append({
            "ts":          ts,
            "contract":    quote.get("symbol"),
            "price":       quote.get("close") or quote.get("lastPrice"),
            "yield_equiv": None,  # computed downstream in bond_repricer.py
        })
    return rows


def market_hours_now(now=None) -> bool:
    """9:30-16:00 ET, weekdays. Uses a fixed UTC offset approximation —
    swap for zoneinfo('America/New_York') if you need exact DST handling."""
    from datetime import time as dtime
    now = now or datetime.now(timezone.utc)
    et_hour = (now.hour - 4) % 24  # rough EDT offset; see note above
    is_weekday = now.weekday() < 5
    in_window = dtime(9, 30) <= dtime(et_hour, now.minute) <= dtime(16, 0)
    return is_weekday and in_window
