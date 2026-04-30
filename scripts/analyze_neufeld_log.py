"""Summarise a Neufeld paper-trading JSONL log.

Reads `--log` and reports, for the rows captured:

  * how many proposals the NN flagged as arbitrage
  * how many cleared the NN's price after the slippage haircut
  * how many were strictly feasible on the dense eval grid
  * how many also agreed with the LSIP ground truth (if logged)
  * the joint count where ALL of those are true

The last number is the only one worth eyeballing as a candidate trade.

Usage:
    python scripts/analyze_neufeld_log.py --log neufeld_paper.jsonl
    python scripts/analyze_neufeld_log.py --log neufeld_paper.jsonl --filter feasible
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--log", required=True)
    p.add_argument("--filter", choices=["all", "feasible", "feasible_after_slip", "lsip_arb", "all_clean"],
                   default="all", help="show only rows matching this predicate")
    p.add_argument("--top", type=int, default=10, help="show top N rows by stressed price")
    args = p.parse_args()

    rows = []
    with open(args.log) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        print(f"[analyze] {args.log} has no usable rows")
        return

    # Backwards compatibility for older logs that used the original schema.
    for r in rows:
        r.setdefault("is_arbitrage_nn", r.get("is_arbitrage", False))
        r.setdefault("is_arbitrage_after_slippage", None)
        r.setdefault("is_strictly_feasible", r.get("min_payoff_unit", -1.0) >= -1e-6)
        r.setdefault("lsip_is_arbitrage", None)
        r.setdefault("pred_price_stressed_unit", r.get("pred_price_unit"))
        r.setdefault("pred_price_stressed_usd", r.get("pred_price_usd"))

    n = len(rows)
    n_nn_arb = sum(1 for r in rows if r["is_arbitrage_nn"])
    n_after_slip = sum(1 for r in rows if r["is_arbitrage_after_slippage"])
    n_feas = sum(1 for r in rows if r["is_strictly_feasible"])
    n_lsip = sum(1 for r in rows if r["lsip_is_arbitrage"] is True)
    n_lsip_logged = sum(1 for r in rows if r["lsip_is_arbitrage"] is not None)

    n_all_clean = sum(
        1 for r in rows
        if r["is_arbitrage_nn"] and r["is_arbitrage_after_slippage"]
        and r["is_strictly_feasible"]
        and (r["lsip_is_arbitrage"] is True or r["lsip_is_arbitrage"] is None)
    )

    print(f"=== {args.log} ({n} rows) ===")
    print(f"  NN says arbitrage:                  {n_nn_arb}/{n}  ({100*n_nn_arb/n:.1f}%)")
    print(f"  Still arb after {rows[0].get('slippage_bps_per_leg', 'N/A')} bps slippage: "
          f"{n_after_slip}/{n}  ({100*(n_after_slip or 0)/n:.1f}%)")
    print(f"  Strictly feasible (min_payoff>=0): {n_feas}/{n}  ({100*n_feas/n:.1f}%)")
    if n_lsip_logged > 0:
        print(f"  LSIP target says arbitrage:         {n_lsip}/{n_lsip_logged}  "
              f"({100*n_lsip/n_lsip_logged:.1f}% of logged)")
    print(f"  ALL OF THE ABOVE clean:             {n_all_clean}/{n}  ({100*n_all_clean/n:.1f}%)")

    print(f"\n--- top {args.top} rows by most-negative stressed price (best apparent edge) ---")
    rows_sorted = sorted(rows, key=lambda r: r.get("pred_price_stressed_unit") or 0.0)[: args.top]
    for r in rows_sorted:
        flags = []
        if r["is_arbitrage_nn"]: flags.append("nn")
        if r["is_arbitrage_after_slippage"]: flags.append("slip")
        if r["is_strictly_feasible"]: flags.append("feas")
        if r["lsip_is_arbitrage"] is True: flags.append("lsip")
        elif r["lsip_is_arbitrage"] is False: flags.append("¬lsip")
        print(f"  {r['iso_time'][:19]}  "
              f"price={r['pred_price_unit']:+.4f}  "
              f"stressed={r['pred_price_stressed_unit']:+.4f}  "
              f"min_payoff={r['min_payoff_unit']:+.4f}  "
              f"notional=${r['abs_notional_usd']:.0f}  "
              f"[{', '.join(flags) or 'none'}]")

    # Filter mode: print the matching rows in full so the user can pipe to jq.
    if args.filter != "all":
        if args.filter == "feasible":
            keep = [r for r in rows if r["is_strictly_feasible"]]
        elif args.filter == "feasible_after_slip":
            keep = [r for r in rows
                    if r["is_strictly_feasible"] and r["is_arbitrage_after_slippage"]]
        elif args.filter == "lsip_arb":
            keep = [r for r in rows if r["lsip_is_arbitrage"] is True]
        elif args.filter == "all_clean":
            keep = [r for r in rows
                    if r["is_arbitrage_nn"] and r["is_arbitrage_after_slippage"]
                    and r["is_strictly_feasible"]
                    and (r["lsip_is_arbitrage"] is True or r["lsip_is_arbitrage"] is None)]
        else:
            keep = rows
        print(f"\n--- {args.filter} rows ({len(keep)}) ---")
        for r in keep:
            print(json.dumps({k: r[k] for k in r if k != "legs"}))


if __name__ == "__main__":
    main()
