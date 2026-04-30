"""Record real exchange quotes and analyze whether triangular arbitrage
ever clears post-fee on this venue.

Phase 1 — Record:  poll the venue every `--poll` seconds for `--minutes`
                   minutes and write each snapshot's bid/ask matrix to a
                   JSONL file.

Phase 2 — Analyze: replay the JSONL, run Bellman-Ford-style cycle search
                   on each snapshot with the venue's *actual* taker fee,
                   and report:
                       * how often a post-fee profitable cycle existed
                       * the size distribution of those cycles in bps
                       * how many distinct cycles fired
                       * the implied cumulative PnL on a paper $200 notional

This is the experiment the backtester does NOT run for you. Public-quote
triangular arb on liquid spot crypto is essentially extinct after fees;
this script will prove or disprove that on your venue with your data.

Usage:
    # record 30 minutes from Binance.US
    python scripts/record_and_analyze.py record --venue binanceus --minutes 30

    # analyze whatever was recorded
    python scripts/record_and_analyze.py analyze --file kraken_record.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gnn_arbitrage.data import FXSnapshot
from gnn_arbitrage.arbitrage import find_arbitrage_cycles


def record(args):
    import ccxt
    ex = getattr(ccxt, args.venue)({"enableRateLimit": True})
    ex.load_markets()
    currencies = tuple(args.currencies)
    n = len(currencies)

    # Resolve symbols and venue taker fees once.
    pair_map: dict[tuple[int, int], tuple[str, str, float]] = {}
    syms = set(ex.symbols or [])
    for i, ci in enumerate(currencies):
        for j, cj in enumerate(currencies):
            if i == j:
                continue
            buy_sym = f"{cj}/{ci}"
            sell_sym = f"{ci}/{cj}"
            if buy_sym in syms:
                fee = float(ex.markets[buy_sym].get("taker") or 0.001)
                pair_map[(i, j)] = (buy_sym, "buy", fee)
            elif sell_sym in syms:
                fee = float(ex.markets[sell_sym].get("taker") or 0.001)
                pair_map[(i, j)] = (sell_sym, "sell", fee)
    missing = [(currencies[i], currencies[j]) for i in range(n) for j in range(n)
               if i != j and (i, j) not in pair_map]
    if missing:
        print(f"[record] no listing for: {missing}")
    listed_symbols = sorted({s for s, _, _ in pair_map.values()})
    print(f"[record] venue={args.venue} symbols={listed_symbols}")

    out = Path(args.file)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not args.append:
        out.write_text("")
    deadline = time.time() + args.minutes * 60.0
    n_snaps = 0
    with out.open("a") as f:
        # Header so analyze() can reconstruct the matrix without re-fetching markets.
        if not args.append:
            f.write(json.dumps({
                "_meta": True,
                "venue": args.venue,
                "currencies": list(currencies),
                "pair_map": {f"{i},{j}": [sym, side, fee] for (i, j), (sym, side, fee) in pair_map.items()},
            }) + "\n")
        while time.time() < deadline:
            try:
                tickers = ex.fetch_tickers(listed_symbols)
            except Exception as e:
                print(f"[record] {e!r}; sleeping 5s")
                time.sleep(5.0)
                continue
            row = {"ts": time.time(), "tickers": {}}
            for sym in listed_symbols:
                t = tickers.get(sym) or {}
                row["tickers"][sym] = {"bid": t.get("bid"), "ask": t.get("ask")}
            f.write(json.dumps(row) + "\n")
            f.flush()
            n_snaps += 1
            if n_snaps % 10 == 0:
                rem = max(0, deadline - time.time())
                print(f"[record] snaps={n_snaps}  remaining={rem:.0f}s")
            time.sleep(args.poll)

    print(f"[record] wrote {n_snaps} snapshots to {out}")


def _snap_from_row(row: dict, meta: dict) -> FXSnapshot | None:
    currencies = tuple(meta["currencies"])
    n = len(currencies)
    bid = np.zeros((n, n)); ask = np.zeros((n, n))
    np.fill_diagonal(bid, 1.0); np.fill_diagonal(ask, 1.0)
    pair_map = {tuple(int(x) for x in k.split(",")): v for k, v in meta["pair_map"].items()}
    tickers = row.get("tickers", {})
    for (i, j), (sym, side, _fee) in pair_map.items():
        t = tickers.get(sym) or {}
        tb = t.get("bid"); ta = t.get("ask")
        if not tb or not ta or tb <= 0 or ta <= 0:
            continue
        if side == "buy":
            bid[i, j] = 1.0 / ta
            ask[i, j] = 1.0 / tb
        else:
            bid[i, j] = tb
            ask[i, j] = ta
    return FXSnapshot(t=int(row.get("ts", 0)), currencies=currencies, rates_bid=bid, rates_ask=ask)


def analyze(args):
    path = Path(args.file)
    if not path.exists():
        print(f"[analyze] no such file: {path}")
        return
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    meta = next((l for l in lines if l.get("_meta")), None)
    if meta is None:
        print("[analyze] file missing _meta header — re-record with this version of the script")
        return
    rows = [l for l in lines if not l.get("_meta")]
    pair_map = {tuple(int(x) for x in k.split(",")): v for k, v in meta["pair_map"].items()}
    fees = {ij: pair_map[ij][2] for ij in pair_map}
    avg_fee_bps = float(np.mean(list(fees.values()))) * 1e4 if fees else 0.0
    print(f"[analyze] file={path}  snaps={len(rows)}  venue={meta.get('venue')}  avg taker={avg_fee_bps:.1f} bps")

    n_snaps_with_arb_pre_fee = 0
    n_snaps_with_arb_post_fee = 0
    pre_fee_bps: list[float] = []
    post_fee_bps: list[float] = []
    paper_pnl = 0.0
    notional = float(args.notional)
    cycles_fired: list[tuple[str, ...]] = []

    for row in rows:
        snap = _snap_from_row(row, meta)
        if snap is None:
            continue
        cycles = find_arbitrage_cycles(snap, max_len=3, min_profit=1.0)
        if cycles:
            n_snaps_with_arb_pre_fee += 1
            best_gross = max(g for _, g in cycles)
            pre_fee_bps.append((best_gross - 1.0) * 1e4)

        # Apply post-fee analysis.
        best_net = 1.0; best_cyc = None
        for cyc, _ in cycles:
            nodes = list(cyc) + [cyc[0]]
            net = 1.0
            ok = True
            for a, b in zip(nodes[:-1], nodes[1:]):
                f = fees.get((a, b), 0.001)
                r = float(snap.rates_bid[a, b])
                if r <= 0:
                    ok = False; break
                net *= r * (1.0 - f)
            if ok and net > best_net:
                best_net = net; best_cyc = cyc
        if best_cyc is not None and best_net > 1.0:
            n_snaps_with_arb_post_fee += 1
            post_fee_bps.append((best_net - 1.0) * 1e4)
            paper_pnl += notional * (best_net - 1.0)
            cycles_fired.append(tuple(snap.currencies[i] for i in best_cyc))

    print()
    print(f"snapshots with PRE-fee arbitrage:  {n_snaps_with_arb_pre_fee}/{len(rows)}  "
          f"({100*n_snaps_with_arb_pre_fee/max(len(rows),1):.2f}%)")
    if pre_fee_bps:
        print(f"  pre-fee gross edge bps:  median={np.median(pre_fee_bps):.2f}  "
              f"p90={np.percentile(pre_fee_bps, 90):.2f}  max={max(pre_fee_bps):.2f}")
    print(f"snapshots with POST-fee arbitrage: {n_snaps_with_arb_post_fee}/{len(rows)}  "
          f"({100*n_snaps_with_arb_post_fee/max(len(rows),1):.2f}%)")
    if post_fee_bps:
        print(f"  post-fee net edge bps:   median={np.median(post_fee_bps):.2f}  "
              f"p90={np.percentile(post_fee_bps, 90):.2f}  max={max(post_fee_bps):.2f}")
    print(f"paper PnL on ${notional} notional, executed every post-fee positive cycle: "
          f"${paper_pnl:+.4f}")
    if cycles_fired:
        from collections import Counter
        print("most fired cycles:")
        for cyc, n in Counter(cycles_fired).most_common(5):
            print(f"  {' -> '.join(cyc)}  ×{n}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record")
    pr.add_argument("--venue", default="binanceus")
    pr.add_argument("--currencies", nargs="+", default=["USDT", "BTC", "ETH", "SOL"])
    pr.add_argument("--minutes", type=float, default=30.0)
    pr.add_argument("--poll", type=float, default=1.0)
    pr.add_argument("--file", default="market_record.jsonl")
    pr.add_argument("--append", action="store_true")

    pa = sub.add_parser("analyze")
    pa.add_argument("--file", default="market_record.jsonl")
    pa.add_argument("--notional", type=float, default=200.0)

    args = p.parse_args()
    if args.cmd == "record":
        record(args)
    elif args.cmd == "analyze":
        analyze(args)


if __name__ == "__main__":
    main()
