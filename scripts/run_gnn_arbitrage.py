"""End-to-end demo: train the arbitrage GNN on synthetic FX data, then
backtest both the GNN strategy and an oracle baseline.

Usage:
    python scripts/run_gnn_arbitrage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gnn_arbitrage.arbitrage import edge_in_cycle_labels, find_arbitrage_cycles
from gnn_arbitrage.backtester import Backtester, baseline_optimal_run
from gnn_arbitrage.data import SyntheticFXFeed
from gnn_arbitrage.gnn_model import TrainingSample, train_gnn
from gnn_arbitrage.graph import build_rate_graph, edge_index_from_snapshot


def make_samples(snaps):
    samples: list[TrainingSample] = []
    for snap in snaps:
        ei, ea, x = build_rate_graph(snap)
        _, pairs = edge_index_from_snapshot(snap)
        cycles = find_arbitrage_cycles(snap, max_len=3, min_profit=1.0)
        labels = edge_in_cycle_labels(pairs, cycles)
        samples.append(TrainingSample(x=x, edge_index=ei, edge_attr=ea, edge_label=labels))
    return samples


def main():
    feed = SyntheticFXFeed(seed=0, arb_prob=0.45, arb_bps=70.0)
    train_snaps = list(feed.stream(600))
    val_snaps = list(feed.stream(150))
    test_snaps = list(feed.stream(400))

    print(f"train snapshots: {len(train_snaps)}  val: {len(val_snaps)}  test: {len(test_snaps)}")
    train_samples = make_samples(train_snaps)
    val_samples = make_samples(val_snaps)

    n_train_arbs = sum(1 for s in train_samples if s.edge_label.sum() > 0)
    print(f"snapshots with arbitrage in train: {n_train_arbs}/{len(train_samples)}")

    model = train_gnn(train_samples, val_samples=val_samples, epochs=30, lr=1e-3,
                      hidden_dim=64, num_layers=3, verbose=True)

    bt = Backtester(
        model=model,
        starting_equity=100_000.0,
        trade_notional=10_000.0,
        fee_bps_per_leg=1.0,
        slippage_bps_per_leg=0.5,
        edge_threshold=0.5,
        min_net_edge_bps=0.5,
        max_cycle_len=3,
    )
    result = bt.run(test_snaps)
    print("\n=== GNN strategy ===")
    for k, v in result.summary().items():
        print(f"  {k}: {v}")

    oracle = baseline_optimal_run(
        test_snaps,
        fee_bps_per_leg=1.0,
        slippage_bps_per_leg=0.5,
        starting_equity=100_000.0,
        trade_notional=10_000.0,
    )
    print("\n=== Oracle (Bellman-Ford ground truth) ===")
    print(f"  n_trades: {len(oracle.trades)}")
    print(f"  final_equity: {oracle.equity_curve[-1]:.2f}")
    print(f"  total_return: {oracle.total_return:.4%}")


if __name__ == "__main__":
    main()
