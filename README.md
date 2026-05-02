# MortgageIQ — Portfolio Command Center

A self-contained mortgage portfolio intelligence dashboard built for buy-side analysts, MBS traders, asset managers, and mortgage capital markets professionals. Tracks live macro signals, analyzes portfolio risk, and delivers AI-powered market intelligence — all from a single HTML file with no backend required.

**Live demo:** [angadvir.github.io/mortgageiq](https://angadvir.github.io/mortgageiq/Mortgage_IQ_Portfolio_App.html)

---

## What's Inside

### Analysis
| Tab | Description |
|-----|-------------|
| **Dashboard** | Live KPI strip (Fed Funds, PMMS, CPI, unemployment, DV01, P&L), rate history charts, portfolio allocation, active alerts, and AI macro brief |
| **Market Signals** | 12-signal z-score heatmap (Fed Funds, SOFR, 10Y TSY, PMMS, CPI, Core PCE, NFP, JOLTS, HPI, MBS OAS, Refi Index), MBS spread chart, and mortgage market metrics |
| **HPI by State** | House Price Index YoY change across 25 US states, heat-coded by appreciation level |
| **Latest News** | AI-curated live news across 4 sections: Primary Capital Markets & MBS · AI in Mortgage · US & Geo-Politics · Latest AI Developments |

### Portfolio
| Tab | Description |
|-----|-------------|
| **Positions** | Full book with search/filter, DV01, OAS, CPR, unrealized P&L per position. Includes a **Portfolio Analyzer** with Pair Analysis, Correlation Screener, and Trend Compare tools |
| **Trade Blotter** | Full transaction log with rationale capture |
| **Hedge Book** | DV01 offset bar chart, net exposure summary, hedge instruments |

### Intelligence
| Tab | Description |
|-----|-------------|
| **Scenarios** | Rate shock sliders (±300bps), OAS/CPR/HPI stress, 5 preset macro scenarios (Soft Landing, Higher for Longer, Stagflation, Hard Landing, Fed Cut 200bps) |
| **Alerts** | Signal-driven alerts ranked by severity with portfolio implications |
| **AI Brief** | Custom Claude AI analysis — ask anything about your portfolio, macro signals, or market conditions |

---

## Prerequisites

### 1. Claude API Key (Required for AI features)
The AI Macro Brief, Latest News, AI Brief, and PDF import all require an active Claude API key from Anthropic.

1. Go to **[console.anthropic.com](https://console.anthropic.com)** and create an account
2. Navigate to **API Keys → Create Key**
3. Add billing credits under **Billing** (even $5 covers extensive usage)
4. Copy your key — it starts with `sk-ant-api03-...`

Once you have the key, open the dashboard and click **⚙ Claude Active** in the top-right corner to enter it. The key is stored only in your browser's local storage — it is never transmitted anywhere except directly to Anthropic's API.

> **Without a Claude API key**, the dashboard still works fully for portfolio tracking, signal viewing, scenario analysis, and charting. Only the AI-powered features require the key.

### 2. FRED API Key (Required for live macro signals)
Macro signals (Fed Funds, CPI, PMMS, HPI by state, unemployment, etc.) are sourced from the Federal Reserve Economic Data (FRED) API — free with registration.

1. Go to **[fred.stlouisfed.org](https://fred.stlouisfed.org)** → My Account → API Keys → Request Key
2. Takes ~1 minute, no approval required

### 3. Python 3.9+ with dependencies
```bash
pip3 install yfinance curl_cffi requests pandas schedule
```

---

## Quick Start

### Option A — Use the live site directly
Open **[angadvir.github.io/mortgageiq/Mortgage_IQ_Portfolio_App.html](https://angadvir.github.io/mortgageiq/Mortgage_IQ_Portfolio_App.html)** in your browser, enter your Claude API key, and start using the dashboard immediately. No installation required.

### Option B — Run locally with live data

```bash
# 1. Clone the repository
git clone https://github.com/Angadvir/mortgageiq.git
cd mortgageiq

# 2. Install Python dependencies
pip3 install yfinance curl_cffi requests pandas schedule

# 3. Set your FRED API key
export FRED_API_KEY=your_fred_key_here

# 4. Fetch live macro signals from FRED
python3 pipeline.py --run-once

# 5. Fetch stock price history from Yahoo Finance
python3 fetch_stocks.py

# 6. Bake live data into the dashboard HTML
python3 inject_signals.py --signals signals_live.json --html Mortgage_IQ_Portfolio_App.html --inplace

# 7. Start local server
python3 -m http.server 8080
```

Open **[http://localhost:8080/Mortgage_IQ_Portfolio_App.html](http://localhost:8080/Mortgage_IQ_Portfolio_App.html)** in your browser.

> **Important:** The dashboard must be served via a local web server (step 7) rather than opened directly as a file. Opening `file://` URLs blocks the Claude API calls required for AI features.

---

## Data Pipeline

### Automated refresh (recommended)
Run the setup script once to install cron jobs that auto-refresh data every weekday:

```bash
bash setup_cron.sh
```

This installs:
- **Daily 06:30** — rates and spreads (SOFR, 10Y TSY, IG/HY OAS)
- **Thursday 11:00** — PMMS and MBA mortgage rates (Freddie Mac releases Thursdays)
- **1st of month** — full refresh (CPI, Core PCE, NFP, HPI, unemployment)

### Manual refresh
```bash
python3 pipeline.py --run-once    # all FRED signals
python3 fetch_stocks.py           # Yahoo Finance stock prices
python3 inject_signals.py --signals signals_live.json --html Mortgage_IQ_Portfolio_App.html --inplace
```

### Signal inventory
The pipeline fetches 20+ FRED series including:

| Signal | FRED Series | Frequency |
|--------|-------------|-----------|
| Fed Funds Rate | FEDFUNDS | Monthly |
| SOFR | SOFR | Daily |
| 10Y / 2Y Treasury | DGS10, DGS2 | Daily |
| PMMS 30Y / 15Y | MORTGAGE30US, MORTGAGE15US | Weekly |
| CPI YoY | CPIAUCSL | Monthly |
| Core PCE | PCEPILFE | Monthly |
| Unemployment | UNRATE | Monthly |
| NFP | PAYEMS | Monthly |
| Case-Shiller HPI | CSUSHPISA | Monthly |
| FHFA HPI (25 states) | State series | Quarterly |
| IG / HY Corp OAS | BAMLC0A0CM, BAMLH0A0HYM2 | Daily |

---

## Portfolio Analyzer

The Positions tab includes a built-in supply chain and pair analysis tool powered by Yahoo Finance price data (no API key required).

### Pair Analysis
Computes three signals for any two tickers:
- **Trend Score** — are both stocks above their N-day moving average in the same direction?
- **Spearman Weekly** — weekly return rank correlation (industry-level co-movement)
- **Cross-correlation at 1–12 week lags** — does one stock lead the other, and by how much?

Pre-loaded supply chain pairs: MU→NVDA, WD→NVDA, MP→NVDA, MP→TSLA, AMAT→TSM, TSM→NVDA, LRCX→INTC

### Correlation Screener
Pick one base stock and screen it against a universe (Semiconductors or EV/Clean Energy) to rank correlations by Spearman coefficient. Drill into any result for full pair analysis.

### Trend Compare
Compare 2–4 stocks simultaneously against their 20/50/200-day moving average. Score cards show return, MA position (above/below), and slope direction.

---

## Importing Positions

### PDF Import (AI-powered)
Upload any portfolio PDF — Robinhood statements, brokerage confirmations, account summaries. Claude reads the document and extracts positions automatically into Positions, Trade Blotter, and Hedge Book tabs.

Requires a Claude API key.

### CSV Import
**Robinhood export:** Account → Statements & History → Generate Report → Positions

**Generic CSV columns:**
```
name, type, shares or upb, purchase_price, current_price
```
Optional: `coupon, wal, oas, cpr, signal`

Type values: `Equity`, `MBS`, `Whole Loan`, `Hedge`

### Manual entry
Click **+ Add Position** in the top-right. Supports both equity (shares × price) and bond/MBS (UPB, coupon, WAL, OAS, CPR) position types.

---

## Contributing via Pull Request

Contributions are welcome. To open a pull request:

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/mortgageiq.git
   cd mortgageiq
   ```
3. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Make your changes** to `Mortgage_IQ_Portfolio_App.html` or the Python pipeline scripts
5. **Test locally** using `python3 -m http.server 8080` before submitting
6. **Commit and push:**
   ```bash
   git add .
   git commit -m "describe your change"
   git push origin feature/your-feature-name
   ```
7. Open a **Pull Request** on GitHub against the `main` branch

### Guidelines
- Keep `Mortgage_IQ_Portfolio_App.html` as a single self-contained file — do not split into separate CSS/JS files
- Test all 8 navigation tabs before submitting
- If adding a new data source, document the API key requirement and free tier details in this README
- Validate JavaScript syntax before submitting: `node --check Mortgage_IQ_Portfolio_App.html` will not work directly — extract the script block first
- Signal data lives in `signals_live.json` and `stocks_live.json` — do not commit these files if they contain sensitive portfolio data

---

## File Structure

```
mortgageiq/
├── Mortgage_IQ_Portfolio_App.html   # Main dashboard (self-contained)
├── pipeline.py                      # FRED macro signal fetcher
├── fetch_stocks.py                  # Yahoo Finance stock price fetcher
├── inject_signals.py                # Bakes live JSON data into dashboard HTML
├── setup_cron.sh                    # Automated cron job installer
├── fetch_stocks.py                  # Stock universe fetcher (23 tickers default)
├── signals_live.json                # Latest FRED signal export (auto-generated)
├── stocks_live.json                 # Latest stock price history (auto-generated)
├── signals.db                       # SQLite time-series database (auto-generated)
├── pipeline.log                     # Fetch audit log (auto-generated)
├── index.html                       # Root redirect to dashboard
└── README.md                        # This file
```

---

## Deploying Your Own Instance

### GitHub Pages (free)
```bash
git init
git add .
git commit -m "initial deploy"
git remote add origin https://github.com/YOUR_USERNAME/mortgageiq.git
git push -u origin main
```
Enable Pages: **Settings → Pages → Deploy from branch → main → Save**

Your dashboard is live at `https://YOUR_USERNAME.github.io/mortgageiq/Mortgage_IQ_Portfolio_App.html`

### VPS with Nginx (~$6/month)
For auto-refreshing live data without manual pushes, deploy to a Linux VPS (Hetzner, DigitalOcean) and run the cron daemon. The `setup_cron.sh` script handles all configuration. Pair with Let's Encrypt for free HTTPS.

---

## Security Notes

- **Claude API key** — stored in browser `localStorage` only. Never committed to the repository. Each user enters their own key.
- **FRED API key** — stored in `.env` file on disk. Add `.env` to `.gitignore` before committing.
- **Portfolio positions** — stored in browser `localStorage` only. Never transmitted or stored in any file.
- **Stock/signal JSON files** — contain only public market data. Safe to commit.

---

## License

MIT License — free to use, fork, and modify. Attribution appreciated but not required.

---

*Built with Chart.js · Powered by FRED API · AI by Anthropic Claude · Stock data via Yahoo Finance*
