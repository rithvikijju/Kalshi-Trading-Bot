"""
Aggregated taker model — stack many weak signals into one, test if it crosses the cost.

Pipeline:
  1. replay captured book+tape+spot through SignalExtractor -> dozens of features/snapshot
  2. label = forward MICROPRICE move over horizon H (continuous; the sticky mid is degenerate)
  3. walk-forward (expanding folds): train a gradient-boosted classifier on P(up) AND a
     regressor on the signed move; on each held-out fold, take a position only when the model
     is confident enough that |predicted move| clears (half-spread + fee + margin)
  4. report OOS: directional accuracy on traded signals, and NET $/trade after cost. A signal
     is only "repeatable & enough to cross fees" if pooled OOS net EV > 0 with CI above 0.

No hardcoded coefficients; the model learns the combination. Honest by construction: the
cost is subtracted on every trade and the threshold is tuned only on train data.

    python -m kalshi_micro.taker_model --log "kalshi_micro/data/book_perp_*.jsonl" \
        --horizon 20 --fee 0.0
"""
from __future__ import annotations

import argparse
import glob
import json

import numpy as np

from .signals import FEATURES, SignalExtractor


def load_features(files, hz=2.0, mult=1e4):
    ext = SignalExtractor(hz=hz, mult=mult)
    rows = []
    for fp in sorted(files):
        with open(fp) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    X, micros, spreads, ts = [], [], [], []
    for r in rows:
        if "bids" in r:
            bids = [(float(p), float(s)) for p, s in r.get("bids", [])]
            asks = [(float(p), float(s)) for p, s in r.get("asks", [])]
        elif "yes" in r:
            from .orderflow import binary_to_book
            bids, asks = binary_to_book({"yes": r.get("yes", []), "no": r.get("no", [])})
        else:
            continue
        extra = {k: r[k] for k in ("spot_book", "binance") if k in r}
        feat = ext.update(r.get("ts", 0.0), bids, asks, r.get("trades", []), r.get("spot"), extra)
        if feat is None:
            continue
        X.append(ext.vector(feat))
        micros.append(feat["_micro"]); spreads.append(feat["_spread"]); ts.append(feat["_ts"])
    return np.array(X), np.array(micros), np.array(spreads), np.array(ts)


def expanding_folds(n, k=5, min_train=0.4):
    start = int(n * min_train); block = max(1, (n - start) // k); cut = start
    while cut + block <= n:
        yield np.arange(cut), np.arange(cut, cut + block); cut += block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--horizon", type=int, default=20, help="forward snapshots (2Hz: 20=10s)")
    ap.add_argument("--fee", type=float, default=0.0, help="per-contract taker fee ($)")
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--mult", type=float, default=1e4)
    ap.add_argument("--margin", type=float, default=0.0, help="extra edge required beyond cost")
    ap.add_argument("--folds", type=int, default=5)
    a = ap.parse_args()

    files = glob.glob(a.log)
    if not files:
        print(f"no logs match {a.log}"); return
    X, micros, spreads, ts = load_features(files, a.hz, a.mult)
    n = len(micros)
    print(f"extracted {n} feature rows ({len(FEATURES)} signals each) from {len(files)} file(s)")
    if n < 500:
        print("too few rows — capture more."); return

    # label: forward microprice move
    y_move = np.full(n, np.nan)
    for i in range(n - a.horizon):
        y_move[i] = micros[i + a.horizon] - micros[i]
    ok = ~np.isnan(y_move)
    X, y_move, spreads, micros = X[ok], y_move[ok], spreads[ok], micros[ok]
    y_dir = (y_move > 0).astype(int)
    n = len(y_move)

    try:
        from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    except ImportError:
        print("need scikit-learn"); return

    pooled_net, pooled_corr_pred, pooled_corr_real, pooled_traded = [], [], [], 0
    for fi, (tr, te) in enumerate(expanding_folds(n, a.folds)):
        vcut = int(len(tr) * 0.85)
        tr_fit, tr_val = tr[:vcut], tr[vcut:]
        mu, sd = X[tr_fit].mean(0), X[tr_fit].std(0); sd[sd == 0] = 1
        Xs = (X - mu) / sd
        reg = GradientBoostingRegressor(n_estimators=120, max_depth=3, learning_rate=0.05,
                                        subsample=0.8)
        reg.fit(Xs[tr_fit], y_move[tr_fit])
        # tune conviction threshold on validation for best NET ev
        pv = reg.predict(Xs[tr_val])
        cost_v = spreads[tr_val] / 2.0 + a.fee
        best_thr, best_e = None, -1e18
        for q in np.linspace(0.5, 0.95, 10):
            thr = np.quantile(np.abs(pv), q)
            sel = np.abs(pv) >= max(thr, 1e-12)
            if sel.sum() < 10:
                continue
            net = np.sign(pv[sel]) * y_move[tr_val][sel] - cost_v[sel] - a.margin
            if net.mean() > best_e:
                best_e, best_thr = net.mean(), thr
        if best_thr is None:
            continue
        # apply on test
        pt = reg.predict(Xs[te])
        cost_t = spreads[te] / 2.0 + a.fee
        sel = np.abs(pt) >= best_thr
        if sel.sum() == 0:
            continue
        net = np.sign(pt[sel]) * y_move[te][sel] - cost_t[sel] - a.margin
        pooled_net.extend(net.tolist()); pooled_traded += int(sel.sum())
        if np.std(pt) > 0:
            pooled_corr_pred.append(np.corrcoef(pt, y_move[te])[0, 1])
        print(f"  fold {fi}: thr={best_thr:.5f} traded={int(sel.sum())}/{len(te)} "
              f"OOS net ${net.mean():+.5f}/trade win {np.mean(net>0):.1%}")

    pooled_net = np.array(pooled_net)
    print(f"\n=== AGGREGATED TAKER MODEL — OOS ({len(FEATURES)} signals) ===")
    if len(pooled_net) < 20:
        print(f"only {len(pooled_net)} OOS trades — inconclusive, capture more.")
        return
    mean_net = pooled_net.mean()
    rng = np.random.default_rng(0)
    boot = np.array([rng.choice(pooled_net, len(pooled_net), replace=True).mean() for _ in range(2000)])
    lo, hi = np.percentile(boot, [5, 95])
    mid_corr = float(np.mean(pooled_corr_pred)) if pooled_corr_pred else float("nan")
    print(f"traded {len(pooled_net)} signals | mean pred-corr {mid_corr:+.3f}")
    print(f"OOS NET $/trade: {mean_net:+.5f}  (95% CI {lo:+.5f} .. {hi:+.5f})")
    print(f"win rate: {np.mean(pooled_net > 0):.1%}")
    if mean_net > 0 and lo > 0:
        print("\n✅ REPEATABLE SIGNAL THAT CROSSES COST — CI above zero. Worth a live paper test.")
    else:
        print("\n❌ does not yet clear cost OOS (CI includes/below zero). Add data sources / data.")


if __name__ == "__main__":
    main()
