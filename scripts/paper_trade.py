"""Live paper trading: poll a ccxt exchange, score cycles, log fills.

Three modes (`--mode`):
    oracle   - any positive-net-return triangle gets executed (no GNN needed).
               Useful as a reality check: real public crypto markets almost
               never show post-fee triangular arbitrage, so this baseline
               will mostly be silent.
    gnn      - load (or train fresh) an EdgeArbitrageGNN, score edges,
               execute triangles whose edges all clear --edge-threshold and
               whose net post-fee return clears --min-edge-bps.
    baseline - same as oracle but uses a configurable gross-edge threshold,
               so you can stress-test sizing/risk without real arbs.

Nothing is sent to the exchange — `PaperBroker` only writes a JSONL log.
Use Ctrl+C to stop.

Examples:
    # smoke test on Kraken (one snapshot)
    python scripts/paper_trade.py --venue kraken --mode oracle --max-iters 1

    # paper-run a fresh GNN against Binance every 2s
    python scripts/paper_trade.py --venue binance --mode gnn \\
        --currencies USDT BTC ETH SOL XRP --poll 2 --notional 200

    # tail the log
    tail -f paper_trades.jsonl
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gnn_arbitrage.arbitrage import find_arbitrage_cycles
from gnn_arbitrage.data import FXSnapshot
from gnn_arbitrage.graph import build_rate_graph
from gnn_arbitrage.live import CCXTFeed
from gnn_arbitrage.paper import PaperBroker, RiskCaps


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--venue", default="kraken", choices=["kraken", "binance", "coinbase", "bybit"])
    p.add_argument("--currencies", nargs="+", default=["USDT", "BTC", "ETH", "SOL", "XRP"])
    p.add_argument("--mode", default="oracle", choices=["oracle", "gnn", "baseline"])
    p.add_argument("--poll", type=float, default=2.0, help="seconds between snapshots")
    p.add_argument("--max-iters", type=int, default=None, help="stop after N snapshots")
    p.add_argument("--notional", type=float, default=200.0, help="base-currency notional per cycle")
    p.add_argument("--base", default="USDT")
    p.add_argument("--starting-equity", type=float, default=10_000.0)
    p.add_argument("--max-daily-loss", type=float, default=500.0)
    p.add_argument("--max-trades-per-day", type=int, default=500)
    p.add_argument("--log-path", default="paper_trades.jsonl")
    p.add_argument("--edge-threshold", type=float, default=0.5,
                   help="GNN per-edge confidence threshold (mode=gnn)")
    p.add_argument("--min-edge-bps", type=float, default=1.0,
                   help="minimum net cycle profit in bps to fire")
    p.add_argument("--min-gross-bps", type=float, default=5.0,
                   help="baseline mode: minimum gross cycle profit in bps")
    p.add_argument("--fee-bps", type=float, default=None,
                   help="override per-leg taker fee in bps (else use venue's)")
    p.add_argument("--gnn-pretrained-snapshots", type=int, default=400,
                   help="mode=gnn: number of synthetic-FX snaps to pretrain on")
    return p.parse_args()


def maybe_train_gnn(args, n_currencies: int):
    """Pretrain a GNN on synthetic snapshots (warning: distribution shift vs real markets)."""
    import torch
    from gnn_arbitrage.data import SyntheticFXFeed
    from gnn_arbitrage.arbitrage import edge_in_cycle_labels
    from gnn_arbitrage.gnn_model import TrainingSample, train_gnn

    print("[gnn] pretraining on synthetic FX (distribution shift WILL apply on live)")
    feed = SyntheticFXFeed(seed=0, currencies=tuple(args.currencies), arb_prob=0.5, arb_bps=80.0)
    samples = []
    for _ in range(args.gnn_pretrained_snapshots):
        snap = feed.snapshot()
        ei, ea, x = build_rate_graph(snap)
        pairs = [(int(ei[0, k]), int(ei[1, k])) for k in range(ei.shape[1])]
        cycles = find_arbitrage_cycles(snap, max_len=3, min_profit=1.0)
        labels = edge_in_cycle_labels(pairs, cycles)
        samples.append(TrainingSample(x=x, edge_index=ei, edge_attr=ea, edge_label=labels))
    return train_gnn(samples, epochs=20, lr=1e-3, hidden_dim=64, num_layers=3, verbose=False)


def best_cycle_via_oracle(snap: FXSnapshot, fees: dict, min_net_bps: float) -> tuple | None:
    cycles = find_arbitrage_cycles(snap, max_len=3, min_profit=1.0)
    if not cycles:
        return None
    best = None
    best_net = 1.0 + min_net_bps * 1e-4
    for cyc, gross in cycles:
        nodes = list(cyc) + [cyc[0]]
        net = 1.0
        for a, b in zip(nodes[:-1], nodes[1:]):
            f = fees.get((a, b), 0.001)
            net *= snap.rates_bid[a, b] * (1.0 - f)
        if net > best_net:
            best_net = net
            best = cyc
    return best


def best_cycle_via_baseline(snap: FXSnapshot, min_gross_bps: float) -> tuple | None:
    cycles = find_arbitrage_cycles(snap, max_len=3, min_profit=1.0 + min_gross_bps * 1e-4)
    if not cycles:
        return None
    return cycles[0][0]


def best_cycle_via_gnn(snap: FXSnapshot, model, fees: dict, edge_thresh: float, min_net_bps: float):
    from gnn_arbitrage.gnn_model import predict_edge_scores
    ei, ea, x = build_rate_graph(snap)
    scores = predict_edge_scores(model, x, ei, ea)
    n = snap.n
    edge_score = {(int(ei[0, k]), int(ei[1, k])): float(scores[k]) for k in range(ei.shape[1])}

    best = None
    best_net = 1.0 + min_net_bps * 1e-4
    for i in range(n):
        for j in range(n):
            if j == i or edge_score.get((i, j), 0) < edge_thresh:
                continue
            for k in range(n):
                if k in (i, j):
                    continue
                if edge_score.get((j, k), 0) < edge_thresh:
                    continue
                if edge_score.get((k, i), 0) < edge_thresh:
                    continue
                cyc = (i, j, k)
                nodes = list(cyc) + [cyc[0]]
                net = 1.0
                for a, b in zip(nodes[:-1], nodes[1:]):
                    f = fees.get((a, b), 0.001)
                    net *= snap.rates_bid[a, b] * (1.0 - f)
                if net > best_net:
                    best_net = net
                    best = cyc
    return best


def main():
    args = parse_args()
    feed = CCXTFeed(
        exchange=args.venue,
        currencies=tuple(args.currencies),
        poll_seconds=args.poll,
    )
    fees = feed.fees()
    if args.fee_bps is not None:
        fees = {k: args.fee_bps * 1e-4 for k in fees}
    avg_fee = float(np.mean(list(fees.values()))) if fees else 0.001
    print(f"[live] venue={args.venue} symbols={feed.symbols()}")
    print(f"[live] avg taker fee: {avg_fee*1e4:.1f} bps  (3 legs ≈ {3*avg_fee*1e4:.1f} bps cost)")

    model = None
    if args.mode == "gnn":
        model = maybe_train_gnn(args, n_currencies=len(args.currencies))

    broker = PaperBroker(
        base_currency=args.base,
        starting_equity=args.starting_equity,
        fee_bps=args.fee_bps if args.fee_bps is not None else avg_fee * 1e4,
        fees=fees,
        log_path=args.log_path,
        risk=RiskCaps(
            max_daily_loss_base=args.max_daily_loss,
            max_trades_per_day=args.max_trades_per_day,
            max_notional_base=args.notional * 5.0,
        ),
    )

    print(f"[paper] mode={args.mode} notional={args.notional} log={args.log_path}")
    print("[paper] starting loop  (Ctrl+C to stop)")

    n_iter = 0
    n_signals = 0
    try:
        for snap in feed.stream(max_iters=args.max_iters):
            n_iter += 1
            cyc = None
            if args.mode == "oracle":
                cyc = best_cycle_via_oracle(snap, fees, args.min_edge_bps)
            elif args.mode == "baseline":
                cyc = best_cycle_via_baseline(snap, args.min_gross_bps)
            else:
                cyc = best_cycle_via_gnn(snap, model, fees, args.edge_threshold, args.min_edge_bps)

            if cyc is not None:
                tr = broker.execute_cycle(snap, cyc, args.notional)
                if tr:
                    n_signals += 1
                    cycle_str = "->".join(tr.cycle_currencies + (tr.cycle_currencies[0],))
                    print(
                        f"[t={n_iter}] FILL {cycle_str}  net={tr.net_return:.6f}  "
                        f"pnl={tr.pnl_base:+.4f} {tr.base_currency}  equity={tr.equity_after:.2f}"
                    )
            elif n_iter % 10 == 0:
                # Periodic heartbeat so the log shows liveness even when idle.
                print(f"[t={n_iter}] no signal  equity={broker.equity:.2f}  fills={n_signals}")
    except KeyboardInterrupt:
        print("\n[paper] interrupted")
    except Exception:
        traceback.print_exc()
    finally:
        s = broker.summary()
        print("\n=== session summary ===")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print(f"  iterations: {n_iter}")
        print(f"  fills: {n_signals}")


if __name__ == "__main__":
    main()
