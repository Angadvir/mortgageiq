"""
fetch_stocks.py
===============
Fetches stock price history using yfinance and writes stocks_live.json
alongside your signals_live.json. The MortgageIQ dashboard reads this
file first — so the Portfolio Analyzer works reliably with no CORS issues.

Install once:
    pip3 install yfinance curl_cffi

Usage:
    python3 fetch_stocks.py                     # fetch full default universe
    python3 fetch_stocks.py NVDA MU TSLA        # specific tickers only
    python3 fetch_stocks.py --days 504          # 2 years of history (default)

Add to your existing cron (run_pipeline.sh) — runs after pipeline.py:
    python3 /path/to/fetch_stocks.py >> pipeline.log 2>&1
"""

import json
import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("fetch_stocks")

OUTPUT_JSON = Path(__file__).parent / "stocks_live.json"

DEFAULT_TICKERS = [
    # Semiconductors / supply chain
    "NVDA", "MU", "AMD", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "INTC", "QCOM",
    # Memory / storage
    "WD", "STX",
    # Rare earth / materials
    "MP", "ALB", "LAC",
    # EV
    "TSLA", "F", "GM", "RIVN",
    # Mortgage originators
    "RKT", "PFSI", "UWMC", "BAM",
]

def check_deps():
    missing = []
    try:
        import yfinance
    except ImportError:
        missing.append("yfinance")
    try:
        import curl_cffi
    except ImportError:
        missing.append("curl_cffi")
    if missing:
        print(f"\n  Missing packages: {', '.join(missing)}")
        print(f"  Install with:  pip3 install {' '.join(missing)}")
        print()
        sys.exit(1)

def fetch_ticker(ticker: str, days: int) -> list:
    import yfinance as yf

    period_map = {63:"3mo", 126:"6mo", 252:"1y", 504:"2y", 756:"3y"}
    period = period_map.get(days) or ("2y" if days >= 504 else "1y")

    try:
        hist = yf.download(
            ticker,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=True,
            timeout=20,
        )

        if hist is None or len(hist) == 0:
            log.warning(f"  {ticker:<6}  No data returned")
            return []

        # yfinance 1.x returns MultiIndex columns when downloading single ticker
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.droplevel(1)

        if "Close" not in hist.columns:
            log.warning(f"  {ticker:<6}  No Close column")
            return []

        series = []
        for date, row in hist.iterrows():
            price = row["Close"]
            if price is not None and str(price) != "nan":
                series.append({
                    "date":  date.strftime("%Y-%m-%d"),
                    "price": round(float(price), 4),
                })

        trimmed = series[-days:]
        last = trimmed[-1]["price"] if trimmed else "?"
        log.info(f"  {ticker:<6}  {len(trimmed):>3} days  last=${last}")
        return trimmed

    except Exception as e:
        log.warning(f"  {ticker:<6}  Error: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Fetch stock prices for MortgageIQ Portfolio Analyzer")
    parser.add_argument("tickers", nargs="*", help="Tickers to fetch (default: full universe)")
    parser.add_argument("--days",   type=int, default=504, help="Days of history (default: 504 = 2 years)")
    parser.add_argument("--output", default=str(OUTPUT_JSON), help="Output JSON path")
    args = parser.parse_args()

    check_deps()
    import yfinance as yf
    log.info(f"yfinance {yf.__version__}")

    tickers = [t.upper() for t in args.tickers] if args.tickers else DEFAULT_TICKERS
    output  = Path(args.output)

    # Load existing data so we don't lose tickers that might fail this run
    existing = {}
    if output.exists():
        try:
            existing = json.loads(output.read_text())
            log.info(f"Loaded {len([k for k in existing if not k.startswith('_')])} existing tickers from {output.name}")
        except Exception:
            pass

    log.info(f"Fetching {len(tickers)} tickers → {output}")
    log.info("-" * 50)

    result = dict(existing)
    ok = fail = 0

    for ticker in tickers:
        series = fetch_ticker(ticker, args.days)
        if series:
            result[ticker] = series
            ok += 1
        else:
            fail += 1
        time.sleep(0.5)   # polite rate limit

    result["_meta"] = {
        "fetched_at":   datetime.utcnow().isoformat() + "Z",
        "ticker_count": len([k for k in result if not k.startswith("_")]),
        "days":         args.days,
        "yfinance":     yf.__version__,
    }

    output.write_text(json.dumps(result, separators=(",", ":")))
    log.info("-" * 50)
    log.info(f"Done — {ok} fetched, {fail} failed → {output}")
    if fail > 0:
        log.info("Tip: failed tickers are kept from previous run if available.")


if __name__ == "__main__":
    main()
