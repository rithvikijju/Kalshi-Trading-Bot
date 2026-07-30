"""
Order-flow / microstructure features — the data real short-term traders actually read.

Built from the captured TAPE (every trade + aggressor side) and BOOK (top-of-book snapshots),
NOT from OHLC bars. These are the signals discretionary microstructure traders use:

  tape-derived (over a trailing window):
    * signed volume & trade imbalance   (buy vol − sell vol) / total
    * order-flow imbalance run / persistence (consecutive same-side aggression)
    * trade intensity (trades/sec) and large-print ratio (sweep/absorption proxy)
    * VPIN-style toxicity (|buy−sell| volume over volume buckets)
  book-derived (latest snapshot):
    * top-N depth imbalance, micro-price vs mid, spread

Label = triple-barrier DIRECTION over a short forward horizon (does price move +b ticks
before −b ticks). The deep model (reuse DeepConfidence) learns P(up) from a SEQUENCE of
micro-buckets + these scalar features, judged by the SAME walk-forward gate.

Needs accumulated tick data — run the bot (sim) to capture it, or buy Databento MBO for a
historical head start. `main` reports how much you've captured and builds the set when ready.
"""
from __future__ import annotations

import argparse

import numpy as np

from ..config import BotConfig, INSTRUMENTS

N_BUCKETS = 50          # micro-buckets of the recent tape fed to the sequence model
TRADES_PER_BUCKET = 20  # each bucket aggregates this many trades
N_OF_FEAT = 8           # scalar order-flow features


def bucket_tape(prices, vols, aggr, n_buckets=N_BUCKETS, per=TRADES_PER_BUCKET) -> np.ndarray:
    """Most recent n_buckets*per trades → (n_buckets, 4) channels:
    [signed_vol, total_vol, price_change(ticks-normalized), trade_count]."""
    need = n_buckets * per
    p, v, a = prices[-need:], vols[-need:], aggr[-need:]
    out = np.zeros((n_buckets, 4), dtype=np.float32)
    if len(p) < per:
        return out
    # right-align: last bucket is most recent
    for b in range(n_buckets):
        hi = len(p) - (n_buckets - 1 - b) * per
        lo = hi - per
        if lo < 0:
            continue
        pv, vv, av = p[lo:hi], v[lo:hi], a[lo:hi]
        out[b, 0] = float(np.sum(vv * av))
        out[b, 1] = float(np.sum(vv))
        out[b, 2] = float(pv[-1] - pv[0])
        out[b, 3] = float(len(pv))
    # normalize signed/total vol by overall scale; price change z-ish
    scale = out[:, 1].mean() if out[:, 1].mean() > 0 else 1.0
    out[:, 0] /= scale
    out[:, 1] /= scale
    pc = out[:, 2]
    out[:, 2] = pc / (np.std(pc) + 1e-9)
    return out


def of_features(prices, vols, aggr, book_row=None) -> np.ndarray:
    """Scalar order-flow features over the trailing window."""
    v, a = np.asarray(vols), np.asarray(aggr)
    tot = v.sum()
    signed = float((v * a).sum())
    imb = signed / tot if tot > 0 else 0.0
    buy = v[a > 0].sum(); sell = v[a < 0].sum()
    vpin = abs(buy - sell) / tot if tot > 0 else 0.0
    # aggressor run: length of the latest consecutive same-side streak
    run = 0
    if len(a):
        s = a[-1]
        for x in a[::-1]:
            if x == s and s != 0:
                run += 1
            else:
                break
    big = v[v > (v.mean() + 2 * v.std())].sum() / tot if tot > 0 and v.std() > 0 else 0.0
    intensity = float(len(v))
    book_imb = float(book_row[6]) if book_row is not None else 0.0   # imbalance col
    spread = float(book_row[2] - book_row[1]) if book_row is not None else 0.0
    return np.array([imb, vpin, run, big, intensity / 100.0, signed / (tot + 1e-9),
                     book_imb, spread], dtype=np.float32)


def build_from_capture(db: str, instrument: str, horizon_s: float = 30.0,
                       barrier_ticks: float = 4.0, step_trades: int = 10) -> dict:
    import duckdb
    con = duckdb.connect(db, read_only=True)
    tp = con.execute("SELECT ts, price, volume, aggressor FROM tape WHERE instrument=? ORDER BY ts",
                     [instrument]).fetchall()
    con.close()
    if len(tp) < N_BUCKETS * TRADES_PER_BUCKET + 100:
        return dict(n=0, need=N_BUCKETS * TRADES_PER_BUCKET + 100, have=len(tp))
    ts = np.array([r[0] for r in tp]); px = np.array([r[1] for r in tp])
    vol = np.array([r[2] for r in tp]); agg = np.array([r[3] for r in tp])
    tick = INSTRUMENTS[instrument].tick_size
    barrier = barrier_ticks * tick

    Xs, Xf, ys, tss = [], [], [], []
    lo = N_BUCKETS * TRADES_PER_BUCKET
    for i in range(lo, len(tp) - 1, step_trades):
        # forward triple-barrier within horizon_s seconds
        p0, t0 = px[i], ts[i]
        up, dn = p0 + barrier, p0 - barrier
        y = None
        j = i + 1
        while j < len(tp) and ts[j] - t0 <= horizon_s:
            if px[j] >= up:
                y = 1; break
            if px[j] <= dn:
                y = 0; break
            j += 1
        if y is None:
            continue                       # neither barrier within horizon → drop (ambiguous)
        Xs.append(bucket_tape(px[:i + 1], vol[:i + 1], agg[:i + 1]))
        Xf.append(of_features(px[i - lo:i + 1], vol[i - lo:i + 1], agg[i - lo:i + 1]))
        ys.append(y); tss.append(t0)
    if not ys:
        return dict(n=0, have=len(tp), need=0)
    dpp = INSTRUMENTS[instrument].tick_value / tick
    return dict(X_seq=np.array(Xs, np.float32), X_feat=np.array(Xf, np.float32),
                y=np.array(ys, np.int64), ts=np.array(tss, np.float64),
                barrier=np.full(len(ys), barrier * dpp),                 # GROSS $ per correct call
                fee=np.full(len(ys), INSTRUMENTS[instrument].rt_cost),   # round-turn fee, always paid
                n=len(ys))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--horizon-s", type=float, default=30.0)
    ap.add_argument("--barrier-ticks", type=float, default=4.0)
    args = ap.parse_args()
    cfg = BotConfig()
    import duckdb
    con = duckdb.connect(cfg.db_path, read_only=True)
    for k in [i.key for i in cfg.enabled_instruments()]:
        n_tape = con.execute("SELECT COUNT(*) FROM tape WHERE instrument=?", [k]).fetchone()[0]
        n_book = con.execute("SELECT COUNT(*) FROM book WHERE instrument=?", [k]).fetchone()[0]
        print(f"{k}: {n_tape} tape rows, {n_book} book snaps captured")
    con.close()
    d = build_from_capture(cfg.db_path, args.instrument, args.horizon_s, args.barrier_ticks)
    if d["n"] == 0:
        print(f"\nnot enough tape for {args.instrument} yet "
              f"(have {d.get('have',0)}, need ~{d.get('need','?')}). Keep the bot running to "
              f"capture order flow, then re-run. Or load Databento MBO for instant history.")
        return
    print(f"\nbuilt {d['n']} microstructure examples for {args.instrument} "
          f"(up-rate {d['y'].mean():.1%}).")
    if d["n"] < 2000:
        print("→ <2000 examples — keep capturing before trusting a walk-forward. (Each session"
              " adds thousands of trades; a few active days gets you there.)")
        return
    from .directional import walk_forward_dir
    print("[walk-forward] training the microstructure model OOS...")
    rep = walk_forward_dir(d, k=5, n_feat=N_OF_FEAT, n_chan=4)
    print(f"OOS accuracy {rep.get('oos_accuracy', float('nan')):.1%} | "
          f"exp ${rep.get('oos_exp_per_trade', float('nan')):+.2f}/trade | "
          f"CI[{rep.get('boot_ci5','?')},{rep.get('boot_ci95','?')}] | "
          f"{'✅ EDGE' if rep.get('promote') else '❌ no edge yet'}")


if __name__ == "__main__":
    main()
