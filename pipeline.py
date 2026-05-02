"""
MortgageIQ Data Pipeline
========================
Fetches live macro + mortgage market signals from FRED and stores them
in a local SQLite database. Outputs a signals JSON file that the
MortgageIQ dashboard reads on load.

Setup:
    pip install requests pandas schedule

Usage:
    python pipeline.py --run-once          # fetch now and exit
    python pipeline.py --daemon            # run on schedule forever
    python pipeline.py --export            # export JSON for dashboard
    python pipeline.py --backfill 24       # backfill 24 months of history
"""

import os
import sys
import json
import sqlite3
import logging
import argparse
import schedule
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  — edit these or set as environment variables
# ─────────────────────────────────────────────────────────────────────────────

FRED_API_KEY  = os.environ.get("FRED_API_KEY", "")          # fred.stlouisfed.org → My Account → API Keys
DB_PATH       = os.environ.get("MORTGAGEIQ_DB", "signals.db")
OUTPUT_JSON   = os.environ.get("MORTGAGEIQ_JSON", "signals_live.json")
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DEFINITIONS
# key          → internal name used by the dashboard
# series_id    → FRED series identifier
# label        → human-readable name
# unit         → display unit
# transform    → optional lambda applied to raw value
# schedule     → when to fetch ('daily', 'weekly', 'monthly')
# ─────────────────────────────────────────────────────────────────────────────

SIGNALS = {
    # ── RATES ────────────────────────────────────────────────────────────────
    "fedFunds": {
        "series_id": "FEDFUNDS",
        "label": "Fed Funds Rate",
        "unit": "%",
        "schedule": "monthly",
        "zscore_window": 60,   # months
    },
    "sofr": {
        "series_id": "SOFR",
        "label": "SOFR",
        "unit": "%",
        "schedule": "daily",
        "zscore_window": 24,
    },
    "tenYrTsy": {
        "series_id": "DGS10",
        "label": "10Y Treasury",
        "unit": "%",
        "schedule": "daily",
        "zscore_window": 60,
    },
    "twoYrTsy": {
        "series_id": "DGS2",
        "label": "2Y Treasury",
        "unit": "%",
        "schedule": "daily",
        "zscore_window": 60,
    },

    # ── MORTGAGE RATES ────────────────────────────────────────────────────────
    "pmms30": {
        "series_id": "MORTGAGE30US",
        "label": "PMMS 30Y Rate",
        "unit": "%",
        "schedule": "weekly",   # Freddie Mac releases every Thursday
        "zscore_window": 60,
    },
    "pmms15": {
        "series_id": "MORTGAGE15US",
        "label": "PMMS 15Y Rate",
        "unit": "%",
        "schedule": "weekly",
        "zscore_window": 60,
    },
    "mba30": {
        "series_id": "OBMMIC30YF",
        "label": "MBA 30Y Rate",
        "unit": "%",
        "schedule": "weekly",
        "zscore_window": 36,
    },

    # ── INFLATION ─────────────────────────────────────────────────────────────
    "cpi": {
        "series_id": "CPIAUCSL",
        "label": "CPI YoY",
        "unit": "%",
        "schedule": "monthly",
        "transform": "yoy",    # compute year-over-year % change
        "zscore_window": 60,
    },
    "corePce": {
        "series_id": "PCEPILFE",
        "label": "Core PCE",
        "unit": "%",
        "schedule": "monthly",
        "transform": "yoy",
        "zscore_window": 60,
    },
    "ppi": {
        "series_id": "PPIACO",
        "label": "PPI",
        "unit": "%",
        "schedule": "monthly",
        "transform": "yoy",
        "zscore_window": 60,
    },

    # ── LABOR MARKET ──────────────────────────────────────────────────────────
    "unemployment": {
        "series_id": "UNRATE",
        "label": "Unemployment Rate",
        "unit": "%",
        "schedule": "monthly",
        "zscore_window": 60,
    },
    "nfp": {
        "series_id": "PAYEMS",
        "label": "NFP (000s)",
        "unit": "k",
        "schedule": "monthly",
        "transform": "mom_change",   # month-over-month change in thousands
        "zscore_window": 36,
    },
    "jolts": {
        "series_id": "JTSJOL",
        "label": "JOLTS Openings",
        "unit": "M",
        "schedule": "monthly",
        "transform": "divide_1000",
        "zscore_window": 36,
    },

    # ── HOUSING ───────────────────────────────────────────────────────────────
    "hpiNational": {
        "series_id": "CSUSHPISA",
        "label": "HPI National YoY",
        "unit": "%",
        "schedule": "monthly",
        "transform": "yoy",
        "zscore_window": 60,
    },
    "housingStarts": {
        "series_id": "HOUST",
        "label": "Housing Starts (000s)",
        "unit": "k",
        "schedule": "monthly",
        "zscore_window": 60,
    },
    "existingHomeSales": {
        "series_id": "EXHOSLUSM495S",
        "label": "Existing Home Sales",
        "unit": "M",
        "schedule": "monthly",
        "transform": "divide_1000",
        "zscore_window": 36,
    },
    "mortgageDelinquency": {
        "series_id": "DRSFRMACBS",
        "label": "Mortgage Delinquency Rate",
        "unit": "%",
        "schedule": "quarterly",
        "zscore_window": 40,
    },

    # ── MBA APPLICATION INDICES ───────────────────────────────────────────────
    "refiBoomster": {
        "series_id": "MORTGAGE30US",   # proxy — MBA refi requires paid subscription
        "label": "Refi App Index (proxy)",
        "unit": "",
        "schedule": "weekly",
        "zscore_window": 36,
    },

    # ── CREDIT / SPREADS ──────────────────────────────────────────────────────
    "igSpread": {
        "series_id": "BAMLC0A0CM",
        "label": "IG Corp OAS",
        "unit": "bps",
        "schedule": "daily",
        "transform": "multiply_100",
        "zscore_window": 60,
    },
    "hySpread": {
        "series_id": "BAMLH0A0HYM2",
        "label": "HY Corp OAS",
        "unit": "bps",
        "schedule": "daily",
        "transform": "multiply_100",
        "zscore_window": 60,
    },
    "mbsSpread": {
        "series_id": "MORTGAGE30US",   # approximate — real MBS OAS from Bloomberg
        "label": "MBS OAS (est.)",
        "unit": "bps",
        "schedule": "weekly",
        "transform": "mbs_oas_estimate",
        "zscore_window": 36,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# STATE HPI SERIES  (FHFA state-level)
# ─────────────────────────────────────────────────────────────────────────────

HPI_STATE_SERIES = {
    "CA": "CASTHPI", "TX": "TXSTHPI", "FL": "FLSTHPI", "NY": "NYSTHPI",
    "WA": "WASTHPI", "CO": "COSTHPI", "AZ": "AZSTHPI", "NV": "NVSTHPI",
    "OR": "ORSTHPI", "GA": "GASTHPI", "NC": "NCSTHPI", "SC": "SCSTHPI",
    "TN": "TNSTHPI", "OH": "OHSTHPI", "MI": "MISTHPI", "IL": "ILSTHPI",
    "PA": "PASTHPI", "NJ": "NJSTHPI", "MA": "MASTHPI", "VA": "VASTHPI",
    "MD": "MDSTHPI", "MN": "MNSTHPI", "WI": "WISTHPI", "UT": "UTSTHPI",
    "ID": "IDSTHPI",
}

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log"),
    ],
)
log = logging.getLogger("mortgageiq")

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key  TEXT NOT NULL,
            obs_date    TEXT NOT NULL,
            raw_value   REAL,
            value       REAL,
            fetched_at  TEXT NOT NULL,
            UNIQUE(signal_key, obs_date)
        );

        CREATE TABLE IF NOT EXISTS hpi_state_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            state       TEXT NOT NULL,
            obs_date    TEXT NOT NULL,
            value       REAL,
            yoy_change  REAL,
            fetched_at  TEXT NOT NULL,
            UNIQUE(state, obs_date)
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key  TEXT NOT NULL,
            fetched_at  TEXT NOT NULL,
            status      TEXT NOT NULL,
            rows_saved  INTEGER DEFAULT 0,
            error_msg   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_signal_date ON signal_history(signal_key, obs_date);
        CREATE INDEX IF NOT EXISTS idx_hpi_state   ON hpi_state_history(state, obs_date);
    """)
    conn.commit()
    conn.close()
    log.info(f"Database initialised → {DB_PATH}")

# ─────────────────────────────────────────────────────────────────────────────
# FRED FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fred_fetch(series_id: str, observation_start: str = None, limit: int = 120) -> list:
    """Fetch observations from FRED API. Returns list of {date, value} dicts."""
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not set. Get one free at fred.stlouisfed.org → My Account → API Keys")

    params = {
        "series_id": series_id,
        "api_key":   FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit":      limit,
    }
    if observation_start:
        params["observation_start"] = observation_start

    resp = requests.get(FRED_BASE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    observations = []
    for obs in data.get("observations", []):
        if obs["value"] == ".":          # FRED uses "." for missing
            continue
        observations.append({
            "date":  obs["date"],
            "value": float(obs["value"]),
        })
    return observations

# ─────────────────────────────────────────────────────────────────────────────
# TRANSFORMS
# ─────────────────────────────────────────────────────────────────────────────

def apply_transform(obs_list: list, transform: str) -> list:
    """Apply a named transform to a list of {date, value} observations."""
    if not transform or transform == "none":
        return obs_list

    df = pd.DataFrame(obs_list).sort_values("date")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    if transform == "yoy":
        df["value"] = df["value"].pct_change(12) * 100

    elif transform == "mom_change":
        df["value"] = df["value"].diff()

    elif transform == "divide_1000":
        df["value"] = df["value"] / 1000

    elif transform == "multiply_100":
        df["value"] = df["value"] * 100

    elif transform == "mbs_oas_estimate":
        # Rough proxy: MBS OAS ≈ (PMMS30 - 10Y TSY - 0.25) * 100 bps
        # Real OAS requires Bloomberg/ICE; this is a serviceable estimate
        df["value"] = (df["value"] - 4.5 - 0.25).clip(lower=0) * 100

    df = df.dropna(subset=["value"])
    return df[["date", "value"]].to_dict("records")

# ─────────────────────────────────────────────────────────────────────────────
# Z-SCORE COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_zscore(conn: sqlite3.Connection, signal_key: str, window_months: int):
    """Compute rolling z-score for the latest value of a signal."""
    rows = conn.execute("""
        SELECT value FROM signal_history
        WHERE signal_key = ?
        ORDER BY obs_date DESC
        LIMIT ?
    """, (signal_key, window_months)).fetchall()

    if len(rows) < 4:
        return None

    vals = [r["value"] for r in rows]
    latest = vals[0]
    mean   = sum(vals) / len(vals)
    std    = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5

    if std == 0:
        return 0.0
    return round((latest - mean) / std, 2)

# ─────────────────────────────────────────────────────────────────────────────
# SAVE TO DB
# ─────────────────────────────────────────────────────────────────────────────

def save_observations(conn: sqlite3.Connection, signal_key: str, obs_list: list) -> int:
    fetched_at = datetime.utcnow().isoformat()
    saved = 0
    for obs in obs_list:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO signal_history
                    (signal_key, obs_date, value, fetched_at)
                VALUES (?, ?, ?, ?)
            """, (signal_key, obs["date"], obs["value"], fetched_at))
            saved += 1
        except Exception as e:
            log.warning(f"  Could not save {signal_key}/{obs['date']}: {e}")
    conn.commit()
    return saved

def save_hpi_state(conn: sqlite3.Connection, state: str, obs_list: list) -> int:
    """Save HPI observations with YoY computed."""
    df = pd.DataFrame(obs_list).sort_values("date")
    df["value"] = pd.to_numeric(df["value"])
    df["yoy_change"] = df["value"].pct_change(4) * 100   # quarterly series → 4 quarters
    df = df.dropna(subset=["yoy_change"])
    fetched_at = datetime.utcnow().isoformat()
    saved = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT OR REPLACE INTO hpi_state_history
                    (state, obs_date, value, yoy_change, fetched_at)
                VALUES (?, ?, ?, ?, ?)
            """, (state, row["date"], row["value"], row["yoy_change"], fetched_at))
            saved += 1
        except Exception:
            pass
    conn.commit()
    return saved

# ─────────────────────────────────────────────────────────────────────────────
# FETCH ONE SIGNAL
# ─────────────────────────────────────────────────────────────────────────────

def fetch_signal(signal_key: str, backfill_months: int = 24) -> bool:
    cfg = SIGNALS[signal_key]
    series_id = cfg["series_id"]
    transform  = cfg.get("transform")

    obs_start = (datetime.today() - timedelta(days=backfill_months * 31)).strftime("%Y-%m-%d")

    log.info(f"  Fetching {signal_key} ({series_id})…")
    conn = get_db()
    try:
        raw_obs = fred_fetch(series_id, observation_start=obs_start)
        obs = apply_transform(raw_obs, transform) if transform else raw_obs
        saved = save_observations(conn, signal_key, obs)
        conn.execute("""
            INSERT INTO fetch_log (signal_key, fetched_at, status, rows_saved)
            VALUES (?, ?, 'ok', ?)
        """, (signal_key, datetime.utcnow().isoformat(), saved))
        conn.commit()
        log.info(f"    → {saved} observations saved")
        return True
    except Exception as e:
        log.error(f"    ✗ {signal_key}: {e}")
        conn.execute("""
            INSERT INTO fetch_log (signal_key, fetched_at, status, error_msg)
            VALUES (?, ?, 'error', ?)
        """, (signal_key, datetime.utcnow().isoformat(), str(e)))
        conn.commit()
        return False
    finally:
        conn.close()

def fetch_hpi_states(backfill_months: int = 24) -> None:
    obs_start = (datetime.today() - timedelta(days=backfill_months * 31)).strftime("%Y-%m-%d")
    conn = get_db()
    log.info("  Fetching HPI by state…")
    for state, series_id in HPI_STATE_SERIES.items():
        try:
            raw_obs = fred_fetch(series_id, observation_start=obs_start)
            saved = save_hpi_state(conn, state, raw_obs)
            log.info(f"    {state}: {saved} rows")
            time.sleep(0.12)   # FRED rate limit: ~8 req/sec
        except Exception as e:
            log.warning(f"    ✗ HPI {state}: {e}")
    conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# FULL FETCH RUN
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all(backfill_months: int = 24) -> None:
    log.info("=" * 60)
    log.info(f"MortgageIQ Pipeline — fetch run starting ({datetime.now():%Y-%m-%d %H:%M})")
    log.info("=" * 60)

    ok = 0
    fail = 0
    for key in SIGNALS:
        success = fetch_signal(key, backfill_months)
        if success:
            ok += 1
        else:
            fail += 1
        time.sleep(0.15)   # FRED rate limit courtesy pause

    fetch_hpi_states(backfill_months)
    export_json()

    log.info(f"Run complete — {ok} OK, {fail} failed")

# ─────────────────────────────────────────────────────────────────────────────
# JSON EXPORT  (what the dashboard reads)
# ─────────────────────────────────────────────────────────────────────────────

def export_json() -> None:
    """
    Export the latest signal values + 12-month history into the JSON format
    expected by the MortgageIQ dashboard's SEED_SIGNALS constant.
    """
    conn = get_db()
    output = {"signals": {}, "hpi": [], "meta": {}}

    for signal_key, cfg in SIGNALS.items():
        # Latest value
        latest = conn.execute("""
            SELECT obs_date, value FROM signal_history
            WHERE signal_key = ?
            ORDER BY obs_date DESC LIMIT 1
        """, (signal_key,)).fetchone()

        if not latest:
            continue

        # Previous value (one period back)
        prev = conn.execute("""
            SELECT value FROM signal_history
            WHERE signal_key = ? AND obs_date < ?
            ORDER BY obs_date DESC LIMIT 1
        """, (signal_key, latest["obs_date"])).fetchone()

        # 12-month history for sparkline
        hist_rows = conn.execute("""
            SELECT value FROM signal_history
            WHERE signal_key = ?
            ORDER BY obs_date DESC LIMIT 12
        """, (signal_key,)).fetchall()
        hist = [round(r["value"], 4) for r in reversed(hist_rows)]

        # Z-score
        zscore = compute_zscore(conn, signal_key, cfg.get("zscore_window", 36))

        output["signals"][signal_key] = {
            "label":  cfg["label"],
            "unit":   cfg["unit"],
            "value":  round(latest["value"], 4),
            "prev":   round(prev["value"], 4) if prev else latest["value"],
            "obs_date": latest["obs_date"],
            "zscore": zscore,
            "hist":   hist,
        }

    # HPI by state — latest value + YoY change per state
    for state in HPI_STATE_SERIES:
        row = conn.execute("""
            SELECT obs_date, value, yoy_change FROM hpi_state_history
            WHERE state = ?
            ORDER BY obs_date DESC LIMIT 1
        """, (state,)).fetchone()
        if row:
            output["hpi"].append({
                "state": state,
                "val":   round(row["yoy_change"], 1),
                "chg":   f"{row['yoy_change']:+.1f}",
                "obs_date": row["obs_date"],
            })

    output["meta"] = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "signal_count": len(output["signals"]),
        "hpi_states": len(output["hpi"]),
        "db_path": str(Path(DB_PATH).resolve()),
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2)

    conn.close()
    log.info(f"JSON exported → {OUTPUT_JSON}  ({len(output['signals'])} signals, {len(output['hpi'])} states)")

# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

def run_daemon() -> None:
    """
    Scheduled fetch cadence:
      Daily    06:30 ET  →  rates, spreads (DGS10, SOFR, HY/IG OAS)
      Thursday 08:00 ET  →  PMMS + MBA mortgage rates
      Monthly  1st day   →  CPI, PCE, unemployment, NFP, housing starts
      Quarterly          →  HPI state data (FHFA releases quarterly)
    """
    log.info("Starting MortgageIQ daemon scheduler…")

    # Daily signals (rates, spreads)
    DAILY_SIGNALS = ["sofr", "tenYrTsy", "twoYrTsy", "igSpread", "hySpread"]
    schedule.every().day.at("06:30").do(
        lambda: [fetch_signal(k, backfill_months=3) for k in DAILY_SIGNALS] or export_json()
    )

    # Weekly — Thursday (Freddie Mac PMMS releases Thursday morning)
    WEEKLY_SIGNALS = ["pmms30", "pmms15", "mba30", "mbsSpread", "refiBoomster"]
    schedule.every().thursday.at("11:00").do(
        lambda: [fetch_signal(k, backfill_months=6) for k in WEEKLY_SIGNALS] or export_json()
    )

    # Monthly — fetch everything on the 2nd of each month (most releases by then)
    schedule.every().month.do(fetch_all)

    log.info("Schedule configured:")
    log.info("  Daily    06:30  — rates & spreads")
    log.info("  Thursday 11:00  — PMMS & mortgage rates")
    log.info("  Monthly  2nd    — full refresh (CPI, PCE, NFP, housing)")
    log.info("")
    log.info("Press Ctrl+C to stop.")

    # Run once immediately on start
    fetch_all(backfill_months=24)

    while True:
        schedule.run_pending()
        time.sleep(60)

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MortgageIQ FRED data pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--run-once",   action="store_true", help="Fetch all signals now and exit")
    parser.add_argument("--daemon",     action="store_true", help="Run on schedule forever")
    parser.add_argument("--export",     action="store_true", help="Re-export JSON from existing DB")
    parser.add_argument("--backfill",   type=int, default=24, metavar="MONTHS", help="Months of history to backfill (default 24)")
    parser.add_argument("--signal",     type=str, metavar="KEY", help="Fetch one specific signal by key")
    parser.add_argument("--list",       action="store_true", help="List all available signal keys")
    args = parser.parse_args()

    if not FRED_API_KEY and not args.list and not args.export:
        print("\n⚠  FRED_API_KEY not set.")
        print("   Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html")
        print("   Then: export FRED_API_KEY=your_key_here\n")
        if not args.run_once and not args.daemon:
            sys.exit(1)

    init_db()

    if args.list:
        print("\nAvailable signal keys:")
        for k, cfg in SIGNALS.items():
            print(f"  {k:<20}  {cfg['label']:<35}  FRED: {cfg['series_id']}")
        print("\nHPI states:", ", ".join(HPI_STATE_SERIES.keys()))
        return

    if args.export:
        export_json()
        return

    if args.signal:
        if args.signal not in SIGNALS:
            print(f"Unknown signal key '{args.signal}'. Use --list to see options.")
            sys.exit(1)
        fetch_signal(args.signal, args.backfill)
        export_json()
        return

    if args.run_once:
        fetch_all(backfill_months=args.backfill)
        return

    if args.daemon:
        run_daemon()
        return

    # Default: run once
    fetch_all(backfill_months=args.backfill)

if __name__ == "__main__":
    main()
