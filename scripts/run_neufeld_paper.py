"""Live paper trading with the trained Neufeld-Sester detector.

Polls real options chains via yfinance every `--poll` seconds, runs the
NN, and writes proposed strategies to a JSONL log. Nothing is sent to a
broker.

Usage:
    python scripts/run_neufeld_paper.py --model models/neufeld.pt \\
        --tickers AAPL MSFT GOOGL AMZN META --poll 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from neufeld_arb.model import StaticArbDetector
from neufeld_arb.paper import PaperConfig, run


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT", "GOOGL", "AMZN", "META"])
    p.add_argument("--expiry", default=None)
    p.add_argument("--poll", type=float, default=60.0)
    p.add_argument("--max-iters", type=int, default=None)
    p.add_argument("--max-notional", type=float, default=5_000.0)
    p.add_argument("--log", default="neufeld_paper.jsonl")
    p.add_argument("--require-feasibility", action="store_true",
                   help="only log rows whose min_payoff_unit >= 0 on the dense grid")
    p.add_argument("--feasibility-grid", type=int, default=4096,
                   help="size of the fresh grid used to evaluate feasibility (default 4096)")
    p.add_argument("--slippage-bps", type=float, default=5.0,
                   help="per-leg haircut: longs pay ask*(1+s/2), shorts receive bid*(1-s/2)")
    p.add_argument("--lsip-target", action="store_true",
                   help="solve the LP for V(K, π) on each row; adds ~1s per snapshot")
    p.add_argument("--lsip-grid", type=int, default=512,
                   help="LSIP target grid size when --lsip-target is on")
    args = p.parse_args()

    ckpt = torch.load(args.model, map_location="cpu", weights_only=True)
    model = StaticArbDetector(N=ckpt["config"]["N"], a_min=ckpt["config"]["a_min"], H=ckpt["config"]["H"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    cfg = PaperConfig(
        tickers=tuple(args.tickers),
        expiry=args.expiry,
        poll_seconds=args.poll,
        max_abs_notional=args.max_notional,
        log_path=args.log,
        require_feasibility=args.require_feasibility,
        s_grid_size=args.feasibility_grid,
        slippage_bps_per_leg=args.slippage_bps,
        compute_lsip_target=args.lsip_target,
        lsip_grid_size=args.lsip_grid,
    )
    run(model, cfg, max_iters=args.max_iters)


if __name__ == "__main__":
    main()
