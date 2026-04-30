"""Train the Neufeld-Sester static-arbitrage detector on a real-options
snapshot fetched by run_neufeld_fetch.py.

Usage:
    python scripts/run_neufeld_train.py --data data/sp500_options.parquet \\
        --pool 5000 --iters 5000 --out models/neufeld.pt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from neufeld_arb.data import load_bundles
from neufeld_arb.training import TrainConfig, train_detector


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="parquet from run_neufeld_fetch.py")
    p.add_argument("--out", required=True, help="output model path (.pt)")
    p.add_argument("--pool", type=int, default=5000, help="LSIP pool size")
    p.add_argument("--iters", type=int, default=5000)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--sb", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--d", type=int, default=5, help="stocks per market")
    p.add_argument("--n-per-asset", type=int, default=11)
    p.add_argument("--lsip-grid", type=int, default=256)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default="cpu")
    p.add_argument("--gamma-min", type=float, default=1.0)
    p.add_argument("--gamma-max", type=float, default=10_000.0)
    args = p.parse_args()

    bundles, expiry = load_bundles(args.data)
    print(f"[train] loaded {len(bundles)} bundles for expiry={expiry}")
    if len(bundles) < args.d:
        print(f"[train] need at least d={args.d} bundles; got {len(bundles)}", file=sys.stderr)
        sys.exit(2)

    cfg = TrainConfig(
        iters=args.iters,
        batch_size=args.batch,
        s_batch_size=args.sb,
        lr=args.lr,
        d=args.d,
        n_per_asset=args.n_per_asset,
        lsip_grid_size=args.lsip_grid,
        seed=args.seed,
        device=args.device,
        pretrained_pool_size=args.pool,
        gamma_min=args.gamma_min,
        gamma_max=args.gamma_max,
    )
    model, history = train_detector(bundles, cfg)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "config": {
            "N": model.N, "a_min": model.a_min, "H": model.H,
            "d": cfg.d, "n_per_asset": cfg.n_per_asset, "expiry": expiry,
        },
    }, out)
    print(f"[train] saved -> {out}")

    hist_path = out.with_suffix(".history.json")
    with hist_path.open("w") as f:
        json.dump(history, f)
    print(f"[train] saved history -> {hist_path}")


if __name__ == "__main__":
    main()
