#!/bin/bash
set -a
source "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/.env" 2>/dev/null || true
set +a

cd "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App"
python3 "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/pipeline.py" --run-once >> "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/pipeline.log" 2>&1

# If dashboard HTML exists, inject live signals
if [ -f "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/MortgageIQ_Portfolio_App.html" ]; then
    python3 "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/inject_signals.py"         --signals "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/signals_live.json"         --html    "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/MortgageIQ_Portfolio_App.html"         --inplace  "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/MortgageIQ_Live.html"         >> "/Users/angadvirpaintal/Downloads/Mortgage_Bond_Analyzer_App/pipeline.log" 2>&1
fi
python3 "$SCRIPT_DIR/fetch_stocks.py" >> "$LOG" 2>&1
