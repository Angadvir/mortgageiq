#!/bin/bash
cd ~/Downloads/Mortgage_Bond_Analyzer_App
python3 pipeline.py --run-once
python3 fetch_stocks.py
python3 inject_signals.py --signals signals_live.json --html Mortgage_IQ_Portfolio_App.html --inplace
git add .
git commit -m "daily update $(date '+%Y-%m-%d')"
git push
echo "Done — app deployed at $(date '+%H:%M:%S')"
