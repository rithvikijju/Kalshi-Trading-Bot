"""Backtest the trained Neufeld-Sester detector on held-out markets
sampled from a real-options snapshot.

Usage:
    python scripts/run_neufeld_backtest.py --data data/sp500_options.parquet \\
        --model models/neufeld.pt --n 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from neufeld_arb.backtester import replay
from neufeld_arb.data import load_bundles
from neufeld_arb.market import sample_market
from neufeld_arb.model import StaticArbDetector


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--n", type=int, default=500, help="held-out market samples")
    p.add_argument("--eval-grid", type=int, default=1024)
    p.add_argument("--seed", type=int, default=2024)
    p.add_argument("--no-target", action="store_true",
                   help="skip LSIP target (faster; loses precision/recall)")
    args = p.parse_args()

    bundles, expiry = load_bundles(args.data)
    ckpt = torch.load(args.model, map_location="cpu", weights_only=True)
    model = StaticArbDetector(N=ckpt["config"]["N"], a_min=ckpt["config"]["a_min"], H=ckpt["config"]["H"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    rng = np.random.default_rng(args.seed)
    markets = [sample_market(bundles, d=ckpt["config"]["d"],
                             n_per_asset=ckpt["config"]["n_per_asset"], rng=rng)
               for _ in range(args.n)]
    print(f"[bt] {len(markets)} markets, eval_grid={args.eval_grid}")

    summary = replay(
        model, markets,
        eval_grid_size=args.eval_grid,
        compute_lsip_target=not args.no_target,
        rng=rng,
    )

    print(f"\n=== net profit (I_S - f) over eval grid, mean per market ===")
    for k, v in summary.profit_stats().items():
        print(f"  {k}: {v}")

    n_arb = sum(1 for r in summary.rows if r.pred_is_arb)
    print(f"\nNN proposes arbitrage on {n_arb}/{summary.n} markets ({100*n_arb/max(summary.n,1):.2f}%)")
    print(f"feasibility violation rate (any S in grid with payoff < -1e-6): "
          f"{summary.feasibility_violation_rate()*100:.2f}%")

    if not args.no_target:
        cm = summary.confusion()
        print(f"\nvs LSIP target sign:")
        for k, v in cm.items():
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
