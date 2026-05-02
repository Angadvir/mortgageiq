#!/bin/bash
# ============================================================
# MortgageIQ Pipeline — Cron + Systemd Setup Script
# ============================================================
# Run this once on your server to configure automated fetching.
# Usage: bash setup_cron.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE="$SCRIPT_DIR/pipeline.py"
INJECT="$SCRIPT_DIR/inject_signals.py"
DASHBOARD="$SCRIPT_DIR/MortgageIQ_Portfolio_App.html"
LOG="$SCRIPT_DIR/pipeline.log"

echo ""
echo "MortgageIQ Pipeline Setup"
echo "========================="
echo ""

# ── 1. Check Python ──────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "✗ Python 3 not found. Install with: sudo apt install python3 python3-pip"
    exit 1
fi
echo "✓ Python: $(python3 --version)"

# ── 2. Install dependencies ───────────────────────────────────
echo ""
echo "Installing Python dependencies…"
pip3 install requests pandas schedule -q 2>/dev/null || pip3 install requests pandas schedule --user -q
echo "✓ Dependencies installed"

# ── 3. Prompt for FRED API key ────────────────────────────────
echo ""
echo "FRED API Key Setup"
echo "------------------"
echo "Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html"
echo ""
read -p "Enter your FRED API key (or press Enter to skip): " FRED_KEY

if [ -n "$FRED_KEY" ]; then
    # Save to .env file
    cat > "$SCRIPT_DIR/.env" <<EOF
FRED_API_KEY=$FRED_KEY
MORTGAGEIQ_DB=$SCRIPT_DIR/signals.db
MORTGAGEIQ_JSON=$SCRIPT_DIR/signals_live.json
EOF
    echo "✓ FRED key saved to .env"
else
    echo "⚠  Skipping FRED key — add it to .env manually before running the pipeline"
fi

# ── 4. Create wrapper script ──────────────────────────────────
cat > "$SCRIPT_DIR/run_pipeline.sh" <<WRAPPER
#!/bin/bash
set -a
source "$SCRIPT_DIR/.env" 2>/dev/null || true
set +a

cd "$SCRIPT_DIR"
python3 "$PIPELINE" --run-once >> "$LOG" 2>&1

# If dashboard HTML exists, inject live signals
if [ -f "$DASHBOARD" ]; then
    python3 "$INJECT" \
        --signals "$SCRIPT_DIR/signals_live.json" \
        --html    "$DASHBOARD" \
        --output  "$SCRIPT_DIR/MortgageIQ_Live.html" \
        >> "$LOG" 2>&1
fi
WRAPPER
chmod +x "$SCRIPT_DIR/run_pipeline.sh"
echo ""
echo "✓ Wrapper script created: run_pipeline.sh"

# ── 5. Cron jobs ──────────────────────────────────────────────
echo ""
echo "Setting up cron jobs…"

# Remove any existing MortgageIQ cron entries
crontab -l 2>/dev/null | grep -v "mortgageiq\|run_pipeline" > /tmp/crontab_clean || true

# Add new entries
cat >> /tmp/crontab_clean <<CRON
# MortgageIQ Pipeline — daily rates (Mon-Fri 6:30 AM)
30 6 * * 1-5 $SCRIPT_DIR/run_pipeline.sh >> $LOG 2>&1

# MortgageIQ Pipeline — full refresh 1st of each month (CPI, NFP, HPI)
0 9 1 * * $SCRIPT_DIR/run_pipeline.sh >> $LOG 2>&1
CRON

crontab /tmp/crontab_clean
echo "✓ Cron jobs installed:"
echo "  Mon-Fri 06:30   Daily signal refresh"
echo "  1st monthly     Full refresh (CPI, NFP, PCE, HPI)"

# ── 6. Optional: systemd service ──────────────────────────────
if command -v systemctl &>/dev/null; then
    echo ""
    read -p "Install as systemd daemon (always-on scheduler)? [y/N]: " SYSTEMD

    if [[ "$SYSTEMD" =~ ^[Yy]$ ]]; then
        SERVICE_FILE="/etc/systemd/system/mortgageiq-pipeline.service"
        sudo tee "$SERVICE_FILE" > /dev/null <<SERVICE
[Unit]
Description=MortgageIQ Data Pipeline
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
EnvironmentFile=$SCRIPT_DIR/.env
ExecStart=python3 $PIPELINE --daemon
Restart=on-failure
RestartSec=60
StandardOutput=append:$LOG
StandardError=append:$LOG

[Install]
WantedBy=multi-user.target
SERVICE

        sudo systemctl daemon-reload
        sudo systemctl enable mortgageiq-pipeline
        sudo systemctl start  mortgageiq-pipeline
        echo "✓ systemd service installed and started"
        echo "  Check status: sudo systemctl status mortgageiq-pipeline"
        echo "  View logs:    journalctl -u mortgageiq-pipeline -f"
    fi
fi

# ── 7. Run immediately ────────────────────────────────────────
echo ""
read -p "Run the pipeline now to fetch initial data? [Y/n]: " RUN_NOW

if [[ ! "$RUN_NOW" =~ ^[Nn]$ ]]; then
    echo ""
    echo "Fetching signals (this takes ~60s for all 20 series)…"
    bash "$SCRIPT_DIR/run_pipeline.sh"
    echo ""
    echo "✓ Initial fetch complete. Check signals_live.json and pipeline.log"
fi

echo ""
echo "========================================================="
echo "Setup complete!"
echo ""
echo "Files:"
echo "  signals.db          SQLite database (all history)"
echo "  signals_live.json   Latest signals export (dashboard reads this)"
echo "  MortgageIQ_Live.html  Dashboard with live data injected"
echo "  pipeline.log        Fetch log"
echo ""
echo "Manual commands:"
echo "  python3 pipeline.py --run-once      Fetch all signals now"
echo "  python3 pipeline.py --export        Re-export JSON without fetching"
echo "  python3 pipeline.py --list          List all signal keys"
echo "  python3 pipeline.py --signal pmms30 Fetch one signal"
echo "  python3 pipeline.py --backfill 60   Backfill 5 years of history"
echo "  python3 inject_signals.py           Inject latest JSON into dashboard"
echo "========================================================="
echo ""
