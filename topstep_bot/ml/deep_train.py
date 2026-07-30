"""
Train / evaluate / continuously improve the deep setup model.

Single cycle:
  build dataset from history.duckdb (+ live capture) → walk-forward OOS eval → if (and only
  if) OOS edge is confirmed, retrain on all data and promote to deep_model.pt for the live
  bot. Otherwise report honestly and leave the live model untouched.

Continuous (--loop): repeat forever while the bot runs. Each cycle pulls the latest few days
of history, merges newly-captured live bars, rebuilds, re-evaluates, and keeps the model with
the best OOS expectancy. This is the "keep improving as it runs" loop — but the bar to ship a
model is always out-of-sample-positive, never in-sample-tuned.

    .venv/bin/python -m topstep_bot.ml.deep_train                 # one cycle
    .venv/bin/python -m topstep_bot.ml.deep_train --loop --every 60   # continuous, every 60 min
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..config import BotConfig
from .dataset import build_all
from .deep_model import DeepConfidence
from .walkforward import walk_forward

HIST_DB = "topstep_bot/data/history.duckdb"
MODEL_PT = "topstep_bot/ml/deep_model.pt"
META_JSON = "topstep_bot/ml/deep_meta.json"


def _load_meta() -> dict:
    p = Path(META_JSON)
    return json.loads(p.read_text()) if p.exists() else {"best_oos_exp": None}


def _save_meta(d: dict):
    Path(META_JSON).write_text(json.dumps(d, indent=2))


def one_cycle(cfg: BotConfig, instruments, k=5, refresh_days=0) -> dict:
    if refresh_days:
        from .history import backfill
        print(f"[refresh] pulling last {refresh_days}d + merging live capture...")
        backfill(cfg, instruments, refresh_days)

    print("[dataset] building labelled examples from history...")
    data = build_all(HIST_DB, instruments, min_rr=0.0)
    n, wins = len(data["y"]), int(data["y"].sum())
    print(f"[dataset] {n} setups | base win rate {wins/n:.1%} | "
          f"avg pnl/trade ${data['pnl'].mean():+.2f} (BEFORE model selection)")

    print("[walk-forward] evaluating out-of-sample...")
    report = walk_forward(data, k=k)
    print("\n=== OOS REPORT ===")
    print(f"selected OOS trades: {report['n_oos']}")
    if "oos_win_rate" in report:
        print(f"OOS win rate       : {report['oos_win_rate']:.1%}")
        print(f"OOS exp / trade    : ${report['oos_exp_per_trade']:+.3f} "
              f"(95% CI ${report['boot_ci5']:+.2f} .. ${report['boot_ci95']:+.2f})")
        print(f"pooled OOS net PnL : ${report['pooled_total_pnl']:+.2f}")
    print(f"DECISION: {'✅ PROMOTE' if report['promote'] else '❌ HOLD'} — {report['reason']}")

    if report["promote"]:
        meta = _load_meta()
        prev = meta.get("best_oos_exp")
        if prev is None or report["oos_exp_per_trade"] > prev:
            print("[promote] OOS-positive and best so far — retraining on all data + shipping.")
            final = DeepConfidence()
            final.fit(data["X_seq"], data["X_feat"], data["y"])
            final.save(MODEL_PT)
            _save_meta({"best_oos_exp": report["oos_exp_per_trade"],
                        "oos_win_rate": report["oos_win_rate"], "n_oos": report["n_oos"],
                        "promoted_at": time.strftime("%Y-%m-%d %H:%M")})
            print(f"[promote] saved {MODEL_PT}. Live bot will use it on next (re)start.")
        else:
            print(f"[promote] OOS-positive but not better than stored ${prev:+.3f} — keeping current.")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--every", type=int, default=60, help="minutes between cycles in --loop")
    ap.add_argument("--refresh-days", type=int, default=0,
                    help="re-pull this many recent days each cycle (grows the dataset)")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    cfg = BotConfig()
    instruments = [i.key for i in cfg.enabled_instruments()]

    if not args.loop:
        one_cycle(cfg, instruments, k=args.folds, refresh_days=args.refresh_days)
        return
    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'='*60}\n[cycle {cycle}] {time.strftime('%Y-%m-%d %H:%M')}\n{'='*60}")
        try:
            one_cycle(cfg, instruments, k=args.folds,
                      refresh_days=args.refresh_days or 10)
        except Exception as e:
            print(f"[cycle {cycle}] error: {e}")
        print(f"[cycle {cycle}] sleeping {args.every} min...")
        time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
