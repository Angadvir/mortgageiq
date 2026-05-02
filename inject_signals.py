"""
inject_signals.py
=================
Reads pipeline's signals_live.json and injects live data into the
MortgageIQ dashboard HTML using sentinel comments — safe and precise.

Usage:
    python3 inject_signals.py
    python3 inject_signals.py --signals signals_live.json --html Mortgage_IQ_Portfolio_App.html --inplace
    python3 inject_signals.py --signals signals_live.json --html Mortgage_IQ_Portfolio_App.html --output MortgageIQ_Live.html
"""

import json
import argparse
import sys
from pathlib import Path
from datetime import datetime


def load_signals(json_path):
    with open(json_path) as f:
        return json.load(f)


def build_signals_js(signals, exported_at):
    lines = [
        f"// Live data — {exported_at}",
        "const SEED_SIGNALS = {"
    ]
    for key, s in signals.items():
        hist_str = ", ".join(str(round(v, 4)) for v in s.get("hist", []))
        zscore = s.get("zscore") or 0.0
        lines.append(
            f'  {key}: {{ label:{json.dumps(s["label"])}, value:{s["value"]}, '
            f'prev:{s["prev"]}, unit:{json.dumps(s["unit"])}, zscore:{zscore}, '
            f'hist:[{hist_str}] }},'
        )
    lines.append("};")
    return "\n".join(lines)


def build_hpi_js(hpi):
    lines = ["const HPI_STATE_DATA = ["]
    for row in hpi:
        lines.append(
            f'  {{state:{json.dumps(row["state"])}, '
            f'val:{row["val"]}, chg:{json.dumps(row["chg"])}}},'
        )
    lines.append("];")
    return "\n".join(lines)


def replace_between_sentinels(html, start_marker, end_marker, new_content):
    start_idx = html.find(start_marker)
    end_idx   = html.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        return html, False
    end_idx += len(end_marker)
    return html[:start_idx] + new_content + html[end_idx:], True


def inject(html, data):
    signals     = data.get("signals", {})
    hpi         = data.get("hpi",     [])
    exported_at = data.get("meta", {}).get("exported_at", datetime.utcnow().isoformat())

    new_signals = (
        "// @@SIGNALS_START@@\n" +
        build_signals_js(signals, exported_at) +
        "\n// @@SIGNALS_END@@"
    )
    html, ok = replace_between_sentinels(html, "// @@SIGNALS_START@@", "// @@SIGNALS_END@@", new_signals)
    if ok:
        print(f"  ✓ SEED_SIGNALS replaced ({len(signals)} signals)")
    else:
        print("  ✗ SEED_SIGNALS sentinels not found in HTML")
        print("    Make sure you are pointing at the latest Mortgage_IQ_Portfolio_App.html")

    if hpi:
        new_hpi = (
            "// @@HPI_START@@\n" +
            build_hpi_js(hpi) +
            "\n// @@HPI_END@@"
        )
        html, ok = replace_between_sentinels(html, "// @@HPI_START@@", "// @@HPI_END@@", new_hpi)
        if ok:
            print(f"  ✓ HPI_STATE_DATA replaced ({len(hpi)} states)")
        else:
            print("  ✗ HPI sentinels not found — skipping")

    return html


def main():
    parser = argparse.ArgumentParser(description="Inject live FRED signals into MortgageIQ dashboard")
    parser.add_argument("--signals", default="signals_live.json")
    parser.add_argument("--html",    default="Mortgage_IQ_Portfolio_App.html")
    parser.add_argument("--output",  default="MortgageIQ_Live.html")
    parser.add_argument("--inplace", action="store_true",
                        help="Update source HTML directly (keeps Claude API key in browser)")
    args = parser.parse_args()

    signals_path = Path(args.signals)
    html_path    = Path(args.html)

    if not signals_path.exists():
        print(f"✗ Not found: {signals_path}  —  run: python3 pipeline.py --run-once")
        sys.exit(1)
    if not html_path.exists():
        print(f"✗ Not found: {html_path}")
        sys.exit(1)

    print(f"\nMortgageIQ Signal Injector")
    print(f"  Signals : {signals_path}")
    print(f"  Source  : {html_path}")

    data    = load_signals(signals_path)
    html    = html_path.read_text(encoding="utf-8")
    updated = inject(html, data)

    out = html_path if args.inplace else Path(args.output)
    out.write_text(updated, encoding="utf-8")
    print(f"  ✓ Dashboard {'updated in place' if args.inplace else 'written → ' + str(out)}\n")


if __name__ == "__main__":
    main()
