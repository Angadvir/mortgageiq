# MortgageIQ Data Pipeline

Fetches live macro + mortgage market signals from FRED (Federal Reserve
Economic Data) and injects them into the MortgageIQ dashboard.

---

## Quick Start (5 minutes)

### Step 1 — Get a free FRED API key

Go to **https://fred.stlouisfed.org** → My Account → API Keys → Request Key.
Takes ~1 minute, no approval required.

### Step 2 — Install dependencies

```bash
pip3 install requests pandas schedule
```

### Step 3 — Set your key and run

```bash
export FRED_API_KEY=your_key_here
python3 pipeline.py --run-once
```

This fetches ~20 signal series and writes `signals_live.json`.

### Step 4 — Inject into your dashboard

```bash
python3 inject_signals.py \
  --signals signals_live.json \
  --html    MortgageIQ_Portfolio_App.html \
  --output  MortgageIQ_Live.html
```

Open `MortgageIQ_Live.html` — all signals are now live.

---

## Automated Setup (server deployment)

Run the setup script once on your VPS/Mac:

```bash
bash setup_cron.sh
```

This installs cron jobs that:
- Refresh rates daily at 06:30 (Mon–Fri)
- Full refresh on the 1st of each month (CPI, NFP, HPI, PCE)

---

## Signal Inventory

| Signal Key         | FRED Series   | Frequency  | Description                   |
|--------------------|---------------|------------|-------------------------------|
| fedFunds           | FEDFUNDS      | Monthly    | Federal Funds Rate            |
| sofr               | SOFR          | Daily      | Secured Overnight Financing   |
| tenYrTsy           | DGS10         | Daily      | 10-Year Treasury Yield        |
| twoYrTsy           | DGS2          | Daily      | 2-Year Treasury Yield         |
| pmms30             | MORTGAGE30US  | Weekly     | Freddie Mac 30Y PMMS          |
| pmms15             | MORTGAGE15US  | Weekly     | Freddie Mac 15Y PMMS          |
| mba30              | OBMMIC30YF    | Weekly     | MBA 30Y Contract Rate         |
| cpi                | CPIAUCSL      | Monthly    | CPI (YoY transform)           |
| corePce            | PCEPILFE      | Monthly    | Core PCE (YoY transform)      |
| ppi                | PPIACO        | Monthly    | PPI (YoY transform)           |
| unemployment       | UNRATE        | Monthly    | Unemployment Rate             |
| nfp                | PAYEMS        | Monthly    | Nonfarm Payrolls (MoM change) |
| jolts              | JTSJOL        | Monthly    | JOLTS Job Openings            |
| hpiNational        | CSUSHPISA     | Monthly    | Case-Shiller HPI (YoY)        |
| housingStarts      | HOUST         | Monthly    | Housing Starts                |
| existingHomeSales  | EXHOSLUSM495S | Monthly    | Existing Home Sales           |
| mortgageDelinquency| DRSFRMACBS    | Quarterly  | Mortgage Delinquency Rate     |
| igSpread           | BAMLC0A0CM    | Daily      | IG Corp OAS (bps)             |
| hySpread           | BAMLH0A0HYM2  | Daily      | HY Corp OAS (bps)             |
| mbsSpread          | (estimated)   | Weekly     | Agency MBS OAS estimate (bps) |

**HPI by State** — 25 states via FHFA state-level HPI series (quarterly).

> **Note on MBS OAS:** Real-time agency MBS OAS requires Bloomberg Terminal
> or ICE Data. The pipeline computes an estimate from PMMS vs. Treasury spread.
> For production, replace with your Bloomberg `LMBS0A0C Index OAS_SPREAD` pull.

---

## CLI Reference

```bash
# Fetch all signals now (24 months of history)
python3 pipeline.py --run-once

# Backfill 5 years of history
python3 pipeline.py --backfill 60

# Fetch one specific signal
python3 pipeline.py --signal pmms30

# Run as daemon (scheduled, forever)
python3 pipeline.py --daemon

# Re-export JSON from existing DB without fetching
python3 pipeline.py --export

# List all available signal keys
python3 pipeline.py --list
```

```bash
# Inject latest signals into dashboard
python3 inject_signals.py

# Overwrite dashboard in-place
python3 inject_signals.py --inplace
```

---

## Environment Variables

| Variable           | Default             | Description                    |
|--------------------|---------------------|--------------------------------|
| FRED_API_KEY       | (required)          | Your FRED API key              |
| MORTGAGEIQ_DB      | signals.db          | SQLite database path           |
| MORTGAGEIQ_JSON    | signals_live.json   | JSON export path               |

Set in `.env` file (created by setup_cron.sh) or export before running:

```bash
export FRED_API_KEY=abcd1234...
export MORTGAGEIQ_DB=/data/mortgageiq/signals.db
python3 pipeline.py --run-once
```

---

## Database Schema

```sql
-- Every observation for every signal
signal_history (signal_key, obs_date, value, fetched_at)

-- HPI with YoY computed
hpi_state_history (state, obs_date, value, yoy_change, fetched_at)

-- Audit log of every fetch run
fetch_log (signal_key, fetched_at, status, rows_saved, error_msg)
```

Query example — last 12 months of PMMS:
```sql
SELECT obs_date, value FROM signal_history
WHERE signal_key = 'pmms30'
ORDER BY obs_date DESC LIMIT 52;
```

---

## Adding Signals

To add a new FRED series, add an entry to the `SIGNALS` dict in `pipeline.py`:

```python
"mortgageDebt": {
    "series_id": "MDOAH",
    "label": "Mortgage Debt Outstanding",
    "unit": "$B",
    "schedule": "quarterly",
    "transform": "divide_1000",
    "zscore_window": 40,
},
```

Available transforms: `yoy`, `mom_change`, `divide_1000`, `multiply_100`, `mbs_oas_estimate`

---

## Upgrading to Live MBS OAS

For true agency MBS OAS (not estimated), replace the `mbsSpread` fetch with:

**Bloomberg (if you have Terminal access):**
```python
# Use blpapi Python library
import blpapi
# Fetch LMBS0A0C Index OAS_SPREAD
```

**ICE BondPoint / ICE Data Services:**
```python
# ICE provides daily UMBS OAS via their data API
# Contact: icedataservices.com
```

**FINRA TRACE (free, T+1):**
```python
# Bond transaction data — can compute implied OAS
# https://www.finra.org/filing-reporting/trace
```

---

## Files

```
mortgage-pipeline/
├── pipeline.py          Main FRED data pipeline
├── inject_signals.py    Injects live JSON into dashboard HTML
├── setup_cron.sh        Automated cron/systemd setup
├── README.md            This file
├── signals.db           SQLite database (created on first run)
├── signals_live.json    Latest export (created on first run)
├── pipeline.log         Fetch log (created on first run)
└── .env                 API keys (created by setup_cron.sh)
```
