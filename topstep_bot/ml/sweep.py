"""
Config sweep to find a POSITIVE-EV setup — attacks the -EV at its source (payoff structure).

The 1-min / nearest-pool config was robustly -EV (small targets + fees). This sweeps the two
levers that change the payoff:
  * timeframe   — higher TF → larger moves vs the fixed per-trade fee (beats the cost floor)
  * min_target_r — push targets to a min R-multiple of risk (bigger winners; also a fee filter,
                   since it forces reward >> cost)

Every config is judged by the SAME walk-forward OOS gate. We pick the best OOS config and ship
it ONLY if it clears the gate (mean OOS PnL>0 and bootstrap CI low>0). Honest caveat: picking
best-of-N by OOS mildly inflates significance — the winner must still hold up on the forward
data the live bot keeps capturing (that's what deep_train --loop re-checks).

    .venv/bin/python -m topstep_bot.ml.sweep
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import BotConfig
from .dataset import build_all
from .deep_model import DeepConfidence
from .walkforward import walk_forward

HIST_DB = "topstep_bot/data/history.duckdb"
MODEL_PT = "topstep_bot/ml/deep_model.pt"
META_JSON = "topstep_bot/ml/deep_meta.json"

TIMEFRAMES = [3, 5, 15]
TARGET_RS = [2.0, 3.0]


def main():
    cfg = BotConfig()
    instruments = [i.key for i in cfg.enabled_instruments()]
    results = []
    for tf in TIMEFRAMES:
        for r in TARGET_RS:
            tag = f"tf={tf}m R>={r}"
            try:
                data = build_all(HIST_DB, instruments, min_rr=0.0,
                                 tf_min=tf, min_target_r=r)
            except Exception as e:
                print(f"[{tag}] dataset error: {e}")
                continue
            n = len(data["y"])
            if n < 300:
                print(f"[{tag}] only {n} setups — skip")
                continue
            base_exp = float(data["pnl"].mean())
            rep = walk_forward(data, k=5, verbose=False)
            rep.update(tf=tf, min_target_r=r, n_setups=n, base_exp=base_exp,
                       data_ref=(data if rep.get("promote") else None))
            results.append(rep)
            we = rep.get("oos_exp_per_trade", float("nan"))
            print(f"[{tag}] setups={n:5d} base=${base_exp:+.2f} | "
                  f"OOS win={rep.get('oos_win_rate', 0):.1%} exp=${we:+.2f} "
                  f"CI[{rep.get('boot_ci5','?')},{rep.get('boot_ci95','?')}] "
                  f"n_oos={rep.get('n_oos',0)} → {'PROMOTE' if rep['promote'] else 'hold'}")

    print("\n=== SWEEP SUMMARY (sorted by OOS expectancy) ===")
    ranked = sorted([r for r in results if "oos_exp_per_trade" in r],
                    key=lambda x: x["oos_exp_per_trade"], reverse=True)
    for r in ranked:
        print(f"  tf={r['tf']}m R>={r['min_target_r']}: OOS exp ${r['oos_exp_per_trade']:+.2f}/trade "
              f"win {r['oos_win_rate']:.1%} CI[{r['boot_ci5']},{r['boot_ci95']}] "
              f"{'✅' if r['promote'] else '❌'}")

    winners = [r for r in ranked if r["promote"]]
    if not winners:
        print("\n❌ No config cleared the OOS gate. The strategy is still -EV on this data.")
        print("   Next levers if you want to keep going: partial-profit/trailing exits, a")
        print("   different setup family, or session/regime filters. Not shipping a loser.")
        return

    best = winners[0]
    print(f"\n✅ BEST OOS-POSITIVE: tf={best['tf']}m R>={best['min_target_r']} "
          f"(${best['oos_exp_per_trade']:+.2f}/trade). Retraining final + shipping.")
    final = DeepConfidence()
    final.fit(best["data_ref"]["X_seq"], best["data_ref"]["X_feat"], best["data_ref"]["y"])
    final.save(MODEL_PT)
    Path(META_JSON).write_text(json.dumps({
        "bar_minutes": best["tf"], "min_target_r": best["min_target_r"],
        "best_oos_exp": best["oos_exp_per_trade"], "oos_win_rate": best["oos_win_rate"],
        "n_oos": best["n_oos"]}, indent=2))
    print(f"   saved {MODEL_PT} + {META_JSON}. The live bot reads these on startup so it trades")
    print(f"   the SAME timeframe ({best['tf']}m) and target rule (R>={best['min_target_r']}).")


if __name__ == "__main__":
    main()
