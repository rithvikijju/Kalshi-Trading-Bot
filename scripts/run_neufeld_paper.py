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
    )
    run(model, cfg, max_iters=args.max_iters)


if __name__ == "__main__":
    main()
