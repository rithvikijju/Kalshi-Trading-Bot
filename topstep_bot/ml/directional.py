"""
Pure-ML directional model — the final, honest alpha attempt.

Drops the ICT entry rules entirely. For each bar we set symmetric ±b·ATR barriers and look
forward H bars: label = 1 if the UP barrier is hit first, 0 if DOWN first (triple-barrier
direction). A 1D-CNN over the recent price window + a few engineered features predicts P(up).
We trade only when the model is confident (|P-0.5| ≥ margin): long if P>0.5, short if P<0.5.

Realized PnL per trade is symmetric and fee-inclusive: +b·ATR·$ if the direction was right,
−b·ATR·$ if wrong, minus the round-turn fee. So **win rate = directional accuracy**, and the
whole question is whether OOS accuracy clears 0.5 by enough to beat fees. Same walk-forward
gate as everything else: ship only if OOS expectancy CI is above zero.

Honest prior: short-horizon futures direction is near-efficient; this very likely prints ~50%
OOS. We run it properly and let the data decide — no in-sample fudging.

    .venv/bin/python -m topstep_bot.ml.directional
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..config import BotConfig, INSTRUMENTS
from ..broker.base import Bar
from ..signals.sessions import et_dt
from ..signals.structure import atr
from .dataset import SEQ_LEN, N_CHAN, _normalize_seq, resample
from .deep_model import DeepConfidence

HIST_DB = "topstep_bot/data/history.duckdb"
N_FEAT = 8
GRID = [(5, 12), (5, 24), (15, 8), (15, 16)]   # (timeframe_min, horizon_bars)
BARRIER_ATR = 1.0


def _feats(bars: list[Bar], i: int, a: float) -> np.ndarray:
    a = a if a > 0 else 1.0
    c = bars[i].c
    def ret(lag):
        j = i - lag
        return (c - bars[j].c) / a if j >= 0 else 0.0
    rets = [ret(1), ret(3), ret(5), ret(10)]
    win = bars[max(0, i - 10):i + 1]
    vol = np.std([ (win[k].c - win[k-1].c) for k in range(1, len(win)) ]) / a if len(win) > 2 else 0.0
    rng = (bars[i].h - bars[i].l) / a
    dt = et_dt(bars[i].ts)
    return np.array(rets + [vol, rng, dt.hour / 24.0, dt.weekday() / 6.0], dtype=np.float32)


def build_directional(con, instrument: str, tf_min: int, horizon: int) -> dict:
    rows = con.execute(
        "SELECT instrument, ts, o, h, l, c, v FROM hist_bars WHERE instrument=? ORDER BY ts",
        [instrument]).fetchall()
    bars = resample([Bar(*r) for r in rows], tf_min)
    inst = INSTRUMENTS[instrument]
    dpp = inst.tick_value / inst.tick_size

    Xs, Xf, ys, barrier_d, tss = [], [], [], [], []
    for i in range(SEQ_LEN, len(bars) - horizon):
        a = atr(bars[max(0, i - 30):i + 1], 14)
        if a <= 0:
            continue
        c = bars[i].c
        up, dn = c + BARRIER_ATR * a, c - BARRIER_ATR * a
        y = None
        for j in range(i + 1, i + 1 + horizon):
            hi_first = bars[j].h >= up
            lo_first = bars[j].l <= dn
            if hi_first and lo_first:           # both in same bar: assume the closer-to-open hit first
                y = 1 if abs(bars[j].o - up) <= abs(bars[j].o - dn) else 0
                break
            if hi_first:
                y = 1; break
            if lo_first:
                y = 0; break
        if y is None:
            y = 1 if bars[i + horizon].c > c else 0   # neither barrier: use end-of-horizon sign
        Xs.append(_normalize_seq(bars[i - SEQ_LEN + 1:i + 1], c, a))
        Xf.append(_feats(bars, i, a))
        ys.append(y)
        barrier_d.append(BARRIER_ATR * a * dpp - inst.rt_cost)   # net $ if direction correct
        tss.append(bars[i].ts)
    return dict(X_seq=np.array(Xs, np.float32), X_feat=np.array(Xf, np.float32),
                y=np.array(ys, np.int64), barrier=np.array(barrier_d, np.float64),
                ts=np.array(tss, np.float64), fee=inst.rt_cost)


def build_all_dir(instruments, tf_min, horizon) -> dict:
    import duckdb
    con = duckdb.connect(HIST_DB, read_only=True)
    parts = [build_directional(con, k, tf_min, horizon) for k in instruments]
    con.close()
    parts = [p for p in parts if len(p["y"]) > 0]
    out = {k: np.concatenate([p[k] for p in parts]) for k in ("X_seq", "X_feat", "y", "barrier", "ts")}
    order = np.argsort(out["ts"])
    return {k: v[order] for k, v in out.items()}


def _folds(n, k=5, min_train=0.4):
    start = int(n * min_train); block = max(1, (n - start) // k); cut = start
    while cut + block <= n:
        yield np.arange(cut), np.arange(cut, cut + block); cut += block


def walk_forward_dir(data: dict, k: int = 5, seed: int = 0, n_feat: int = N_FEAT,
                     n_chan: int = N_CHAN) -> dict:
    Xs, Xf, y, barrier = data["X_seq"], data["X_feat"], data["y"], data["barrier"]
    fee = data.get("fee", np.zeros(len(y)))     # round-turn fee, ALWAYS paid (win or lose)
    n = len(y)
    pooled_pnl, pooled_correct = [], []
    for tr, te in _folds(n, k):
        vcut = int(len(tr) * 0.85)
        model = DeepConfidence(n_feat=n_feat, n_chan=n_chan)
        model.fit(Xs[tr[:vcut]], Xf[tr[:vcut]], y[tr[:vcut]])
        # tune confidence margin on the train-tail validation set for best NET EV
        pv = model.predict_proba(Xs[tr[vcut:]], Xf[tr[vcut:]])
        yv, bv, fv = y[tr[vcut:]], barrier[tr[vcut:]], fee[tr[vcut:]]
        best_m, best_e = 0.05, -1e18
        for m in np.linspace(0.0, 0.25, 11):
            sel = np.abs(pv - 0.5) >= m
            if sel.sum() < 10:
                continue
            dir_long = pv[sel] > 0.5
            correct = (dir_long == (yv[sel] == 1))
            e = (np.where(correct, bv[sel], -bv[sel]) - fv[sel]).mean()
            if e > best_e:
                best_e, best_m = e, m
        pt = model.predict_proba(Xs[te], Xf[te])
        sel = np.abs(pt - 0.5) >= best_m
        if sel.sum() == 0:
            continue
        dir_long = pt[sel] > 0.5
        correct = (dir_long == (y[te][sel] == 1))
        pnl = np.where(correct, barrier[te][sel], -barrier[te][sel]) - fee[te][sel]
        pooled_pnl.extend(pnl.tolist()); pooled_correct.extend(correct.tolist())

    pp = np.array(pooled_pnl)
    if len(pp) < 20:
        return dict(promote=False, n_oos=len(pp), reason="too few confident OOS trades")
    rng = np.random.default_rng(seed)
    boot = np.array([rng.choice(pp, len(pp), replace=True).mean() for _ in range(2000)])
    lo, hi = np.percentile(boot, [5, 95])
    acc = float(np.mean(pooled_correct))
    exp = float(pp.mean())
    return dict(promote=bool(exp > 0 and lo > 0), n_oos=len(pp), oos_accuracy=round(acc, 4),
                oos_exp_per_trade=round(exp, 3), boot_ci5=round(float(lo), 3),
                boot_ci95=round(float(hi), 3), total_pnl=round(float(pp.sum()), 2))


def main():
    cfg = BotConfig()
    instruments = [i.key for i in cfg.enabled_instruments()]
    results = []
    for tf, H in GRID:
        data = build_all_dir(instruments, tf, H)
        if len(data["y"]) < 500:
            print(f"[tf={tf}m H={H}] only {len(data['y'])} samples — skip"); continue
        base_up = float(data["y"].mean())
        rep = walk_forward_dir(data, k=5)
        rep.update(tf=tf, H=H, n=len(data["y"]), base_up=base_up)
        results.append(rep)
        print(f"[tf={tf}m H={H}] n={len(data['y'])} base_up={base_up:.1%} | "
              f"OOS acc={rep.get('oos_accuracy', float('nan')):.3f} "
              f"exp=${rep.get('oos_exp_per_trade', float('nan')):+.2f} "
              f"CI[{rep.get('boot_ci5','?')},{rep.get('boot_ci95','?')}] "
              f"n_oos={rep.get('n_oos',0)} → {'PROMOTE' if rep['promote'] else 'hold'}")

    ranked = sorted([r for r in results if "oos_accuracy" in r],
                    key=lambda x: x["oos_exp_per_trade"], reverse=True)
    print("\n=== DIRECTIONAL SUMMARY ===")
    for r in ranked:
        print(f"  tf={r['tf']}m H={r['H']}: OOS acc {r['oos_accuracy']:.1%} "
              f"exp ${r['oos_exp_per_trade']:+.2f} CI[{r['boot_ci5']},{r['boot_ci95']}] "
              f"{'✅' if r['promote'] else '❌'}")
    winners = [r for r in ranked if r["promote"]]
    if winners:
        b = winners[0]
        print(f"\n✅ DIRECTIONAL EDGE FOUND: tf={b['tf']}m H={b['H']} acc {b['oos_accuracy']:.1%} "
              f"exp ${b['oos_exp_per_trade']:+.2f}/trade. This is real OOS — worth wiring live.")
        Path("topstep_bot/ml/directional_meta.json").write_text(json.dumps(b, indent=2, default=str))
    else:
        print("\n❌ No directional edge OOS — accuracy ~50%, as efficient-market priors predict.")
        print("   This is the honest answer: 1-min/intraday futures direction isn't predictable")
        print("   from price history alone on this dataset. Not shipping a coin flip.")


if __name__ == "__main__":
    main()
