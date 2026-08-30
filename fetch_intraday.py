"""
fetch_intraday.py
==================
Bridges market_data_service.py (the Webull equities + bond mark-to-model
repricing service) into the dashboard's static-file pattern. Writes
intraday_live.json in the same folder as the dashboard HTML — the
overlay JS added to Mortgage_IQ_Portfolio_App.html fetches this file
every 2 minutes and updates position prices/OAS live, without touching
your daily FRED signals or Portfolio Analyzer's price history.

Requires market_data_service.py to be running locally first:
    cd market_data_service && python3 market_data_service.py

Usage:
    python3 fetch_intraday.py
    python3 fetch_intraday.py --service-url http://localhost:8090

Add to deploy.sh AFTER inject_signals.py, so a slow/offline market
data service never blocks your daily FRED-driven deploy:

    python3 fetch_intraday.py || echo "  (intraday service unreachable — skipping, not fatal)"
"""

import json
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests

DEFAULT_SERVICE_URL = "http://localhost:8090"
OUTPUT_JSON = Path(__file__).parent / "intraday_live.json"


def fetch_json(url, timeout=5):
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def build_intraday_payload(service_url):
    health = fetch_json(f"{service_url}/health")
    equities_raw = fetch_json(f"{service_url}/prices/equities")
    bonds_raw = fetch_json(f"{service_url}/prices/bonds")

    equities = {
        row["ticker"]: {
            "price": row["price"],
            "bid": row.get("bid"),
            "ask": row.get("ask"),
            "ts": row["ts"],
        }
        for row in equities_raw
    }

    bonds = {
        row["position_id"]: {
            "model_price": row["model_price"],
            "oas": row["oas"],
            "ts": row["ts"],
        }
        for row in bonds_raw
    }

    return {
        "equities": equities,
        "bonds": bonds,
        "meta": {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "market_hours": health.get("market_hours", False),
            "equity_count": len(equities),
            "bond_count": len(bonds),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Pull live intraday prices into the dashboard's static-file pattern")
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--output", default=str(OUTPUT_JSON))
    args = parser.parse_args()

    try:
        payload = build_intraday_payload(args.service_url)
    except requests.exceptions.ConnectionError:
        print(f"✗ Could not reach market_data_service at {args.service_url}")
        print("  Start it with: cd market_data_service && python3 market_data_service.py")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"✗ Request failed: {e}")
        sys.exit(1)

    out = Path(args.output)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"✓ intraday_live.json written — {payload['meta']['equity_count']} equities, "
          f"{payload['meta']['bond_count']} bonds (market_hours={payload['meta']['market_hours']})")


if __name__ == "__main__":
    main()
