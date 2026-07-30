"""
Calibrate + validate the order-flow fair-value model from a captured book log.

This is the honest test of your thesis: does microprice + OFI + imbalance predict the next
mid move on Kalshi, and does the prediction survive the spread? We:
  1. replay the captured book, computing the order-flow features at each snapshot,
  2. label each with the realized forward mid change over H snapshots,
  3. fit beta by least squares on a TRAIN split, then on the held-out TEST split report:
       - out-of-sample correlation(predicted move, realized move),
       - directional accuracy,
       - a taker EV check net of the spread/fee (the thing that killed the perp taker before).
  4. save the betas to fv_coefs_<venue>.json for the live MM to use.

No hardcoded coefficients — they come from the data, and we report whether they generalize.

    python -m kalshi_micro.calibrate --log kalshi_micro/data/book_perp_KXBTCPERP_*.jsonl --horizon 5
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np

from .orderflow import (FEATURES, OFIState, best_bid_ask, binary_to_book, features, microprice, mid)


def _load(path):
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def build_series(rows):
    ofi = OFIState()
    X, mids, micros, spreads, bids_, asks_ = [], [], [], [], [], []
    for r in rows:
        if "bids" in r:                                  # perp
            bids = [(float(p), float(s)) for p, s in r.get("bids", [])]
            asks = [(float(p), float(s)) for p, s in r.get("asks", [])]
        elif "yes" in r:                                 # binary
            bids, asks = binary_to_book({"yes": r.get("yes", []), "no": r.get("no", [])})
        else:
            continue
        if not bids or not asks:
            continue
        bb, bbs, ba, bas = best_bid_ask(bids, asks)
        inc = ofi.update(bb, bbs, ba, bas)
        feat = features(bids, asks, inc, r.get("trades", []))
        X.append([feat[k] for k in FEATURES])
        mids.append(feat["mid"]); micros.append(microprice(bids, asks))
        spreads.append(feat["spread"]); bids_.append(bb); asks_.append(ba)
    return (np.array(X), np.array(mids), np.array(micros), np.array(spreads),
            np.array(bids_), np.array(asks_))


def label(series, horizon):
    y = np.full(len(series), np.nan)
    for i in range(len(series) - horizon):
        y[i] = series[i + horizon] - series[i]
    return y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="path or glob to a book_*.jsonl")
    ap.add_argument("--horizon", type=int, default=20, help="forward snapshots for the label")
    ap.add_argument("--target", choices=["micro", "mid"], default="micro",
                    help="predict change in microprice (continuous) or mid (sticky/quantized)")
    ap.add_argument("--fee", type=float, default=0.0, help="per-contract taker fee ($)")
    a = ap.parse_args()
    files = sorted(glob.glob(a.log))
    if not files:
        print(f"no logs match {a.log}"); return
    rows = []
    for fp in files:
        rows += _load(fp)
    print(f"loaded {len(rows)} snapshots from {len(files)} file(s)")
    Xa, mids_a, micros_a, spreads_a, bids_a, asks_a = build_series(rows)
    target_series = micros_a if a.target == "micro" else mids_a
    ya = label(target_series, a.horizon)
    ok = ~np.isnan(ya)
    X, y, mids, spreads = Xa[ok], ya[ok], mids_a[ok], spreads_a[ok]
    if len(y) < 200:
        print(f"only {len(y)} usable snapshots — capture more (run kalshi_micro.capture longer).")
        return
    move_rate = float(np.mean(np.abs(y) > 1e-9))
    print(f"target={a.target} | nonzero-move rate: {move_rate:.1%}"
          + ("  ⚠️ mostly flat — longer horizon / more data needed" if move_rate < 0.3 else ""))

    n = len(y); cut = int(n * 0.7)
    Xtr, ytr = X[:cut], y[:cut]
    Xte, yte = X[cut:], y[cut:]
    # standardize on train
    mu, sd = Xtr.mean(0), Xtr.std(0); sd[sd == 0] = 1
    Xtr_s = (Xtr - mu) / sd; Xte_s = (Xte - mu) / sd
    # least-squares fit (with intercept)
    A = np.column_stack([Xtr_s, np.ones(len(Xtr_s))])
    beta, *_ = np.linalg.lstsq(A, ytr, rcond=None)
    pred_te = np.column_stack([Xte_s, np.ones(len(Xte_s))]) @ beta

    # report
    print(f"\nusable snapshots {n} | train {cut} test {n-cut} | horizon {a.horizon} snaps")
    print("\nfitted betas (standardized) — how each signal moves fair value:")
    for k, b in zip(FEATURES, beta[:-1]):
        print(f"  {k:10s} {b:+.5f}")
    oos_corr = np.corrcoef(pred_te, yte)[0, 1] if np.std(pred_te) > 0 else 0.0
    dir_acc = float(np.mean(np.sign(pred_te) == np.sign(yte)))
    print(f"\nOOS corr(pred, realized move): {oos_corr:+.3f}")
    print(f"OOS directional accuracy:      {dir_acc:.1%}")

    # taker EV net of spread: when predicted move > half-spread+fee, cross and hold H
    half = spreads[cut:] / 2.0
    edge = pred_te
    take = np.abs(edge) > (half + a.fee)
    if take.sum() >= 20:
        sgn = np.sign(edge[take])
        gross = sgn * yte[take]
        net = gross - half[take] - a.fee          # pay half-spread to enter + fee
        print(f"\ntaker check: {int(take.sum())} signals cleared spread | "
              f"net ${net.mean():+.5f}/trade | win {np.mean(net>0):.1%}")
        print("  -> " + ("positive after spread — worth a live paper test" if net.mean() > 0
                         else "negative after spread (taker dead, as expected — maker is the play)"))
    else:
        print("\ntaker check: too few signals cleared the spread (maker-side is the play here).")

    coefs = {k: float(b * 1.0) for k, b in zip(FEATURES, beta[:-1])}
    # de-standardize so serving can apply to raw features
    raw = {k: float(beta[i] / sd[i]) for i, k in enumerate(FEATURES)}
    venue = "perp" if "perp" in files[0] else "binary"
    out = Path(__file__).resolve().parent / f"fv_coefs_{venue}.json"
    out.write_text(json.dumps({"raw": raw, "horizon": a.horizon,
                               "oos_corr": round(float(oos_corr), 4),
                               "dir_acc": round(dir_acc, 4)}, indent=2))
    print(f"\nsaved calibrated coefs -> {out.name} (the MM loads these; nothing hardcoded)")


if __name__ == "__main__":
    main()
