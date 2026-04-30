"""Fetch real S&P 500 option chains via yfinance and save to a Parquet
snapshot. This is a one-shot data collection step; subsequent train /
backtest runs read the saved file.

Usage:
    python scripts/run_neufeld_fetch.py --out data/sp500_options.parquet
    python scripts/run_neufeld_fetch.py --out data/sp500_options.parquet \\
        --tickers AAPL MSFT GOOGL AMZN META --expiry 2025-06-20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from neufeld_arb.data import SP500_TOP, fetch_bundles, pick_expiry, save_bundles


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="output parquet path")
    p.add_argument("--tickers", nargs="+", default=list(SP500_TOP))
    p.add_argument("--expiry", default=None, help="YYYY-MM-DD (else earliest common in 14-90d)")
    p.add_argument("--sleep", type=float, default=0.4)
    args = p.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    expiry = args.expiry or pick_expiry(args.tickers)
    if expiry is None:
        print("[fetch] no common expiry found; aborting", file=sys.stderr)
        sys.exit(2)
    print(f"[fetch] expiry={expiry} tickers={len(args.tickers)}")
    bundles = fetch_bundles(args.tickers, expiry=expiry, sleep_between=args.sleep)
    save_bundles(bundles, args.out, expiry=expiry)


if __name__ == "__main__":
    main()
