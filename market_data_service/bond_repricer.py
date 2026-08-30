"""
bond_repricer.py
=================
Mark-to-model repricing leg. Every ~2-minute cycle:
    1. Read the latest rate-proxy futures price (ZN/ZB/TN/UB)
    2. Convert Δfutures price → an approximate Δyield since the last anchor
    3. Apply duration/convexity to each portfolio position to get a new
       model_price and OAS estimate
    4. Write to bond_prices, anchored to the rate-proxy observation used

This uses a linear duration + convexity approximation, which is the
same math QuantLib would give you for a single parallel-shift rate
scenario. If/when you want full OAS with a Monte Carlo prepayment
model (not just a duration bump), swap `reprice_position()` internals
for a QuantLib `MortgageBackedSecurity` / CMS pricer — the interface
below (position in, model_price+oas out) doesn't need to change.

Futures → yield conversion:
    ZN (10Y note futures): DV01 per contract ≈ $62-70 depending on CTD.
    We use a fixed approximate BPV table below. For production accuracy,
    pull the actual cheapest-to-deliver DV01 from your futures data
    vendor rather than the static table — CTD switches over time.
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger("bond_repricer")

# Approximate BPV (price change per 1bp yield move) per futures contract.
# Replace with live CTD-DV01 if you have it — this is a reasonable static
# proxy for a personal/simulation pipeline, not a trading-grade number.
FUTURES_BPV = {
    "ZN": 0.065,   # 10Y T-Note futures
    "ZB": 0.145,   # 30Y T-Bond futures
    "TN": 0.095,   # Ultra 10Y
    "UB": 0.185,   # Ultra Bond
}

PRIMARY_ANCHOR_CONTRACT = "ZN"  # 10Y is the standard duration proxy for MBS


def futures_price_to_yield_delta(contract: str, price_now: float, price_anchor: float) -> float:
    """Convert a futures price move into an approximate yield move in bps.
    Futures price and yield move inversely."""
    bpv = FUTURES_BPV.get(contract, 0.07)
    price_delta = price_now - price_anchor
    yield_delta_bps = -(price_delta / bpv)
    return yield_delta_bps


def reprice_position(position: dict, yield_delta_bps: float) -> dict:
    """
    position expects: position_id, face_value, price (32nds or decimal,
    your call — keep consistent with your inject_signals.py convention),
    oas_bps, mod_duration, convexity

    Returns: { position_id, model_price, oas }

    Price impact formula (standard bond math):
        ΔP/P ≈ -Duration * Δy + 0.5 * Convexity * Δy^2
    where Δy is in decimal (bps / 10000).
    """
    dy = yield_delta_bps / 10000.0
    duration  = position.get("mod_duration", 0.0)
    convexity = position.get("convexity", 0.0)
    base_price = position.get("price", 100.0)
    base_oas   = position.get("oas_bps", 0.0)

    pct_change = -duration * dy + 0.5 * convexity * (dy ** 2)
    model_price = base_price * (1 + pct_change)

    # OAS drifts slightly with rate direction as a simple, directional
    # placeholder — for real OAS you need the prepayment-adjusted cashflow
    # model. This keeps a plausible number flowing until QuantLib is wired in.
    oas_drift = -yield_delta_bps * 0.15
    new_oas = base_oas + oas_drift

    return {
        "position_id": position["position_id"],
        "model_price": round(model_price, 4),
        "oas": round(new_oas, 1),
    }


def reprice_portfolio(positions: list, rate_proxy_rows: list, anchor_price_map: dict) -> list:
    """
    positions: list of position dicts (see reprice_position docstring)
    rate_proxy_rows: latest snapshot rows from webull_client.fetch_rate_proxy_snapshots()
    anchor_price_map: {contract: last_reconciled_price} — the price at
        the last daily/weekly reconciliation, used as the zero point

    Returns rows ready for market_db.insert_bond_prices()
    """
    ts = datetime.now(timezone.utc).isoformat()

    anchor_row = next((r for r in rate_proxy_rows if r["contract"] == PRIMARY_ANCHOR_CONTRACT), None)
    if not anchor_row or PRIMARY_ANCHOR_CONTRACT not in anchor_price_map:
        log.warning("No rate-proxy anchor available — skipping repricing cycle")
        return []

    yield_delta_bps = futures_price_to_yield_delta(
        PRIMARY_ANCHOR_CONTRACT,
        anchor_row["price"],
        anchor_price_map[PRIMARY_ANCHOR_CONTRACT],
    )

    rows = []
    for pos in positions:
        priced = reprice_position(pos, yield_delta_bps)
        rows.append({
            "ts": ts,
            "position_id": priced["position_id"],
            "model_price": priced["model_price"],
            "oas": priced["oas"],
            "anchor_ts": anchor_row["ts"],
            "anchor_source": "rate_proxy",
        })
    return rows
