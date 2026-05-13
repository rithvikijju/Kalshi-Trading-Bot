"""Variant sweep over the v2 backtest.

Runs `run_backtest` with multiple CFG configurations and emits a single
comparison table sorted by net PnL. Each variant captures one of the
"models we've tried so far" — different combinations of BRTI dampening,
blend, market shrink, NO surcharge, Platt calibration, gate tightness,
TP/SL exits, etc.

Usage
-----
    from pathlib import Path
    from kalshi_v2.backtest_sweep import run_variant_sweep
    sweep = run_variant_sweep(
        duckdb_path=Path("~/.../live_capture_gapless_paused.duckdb"),
        output_dir=Path("./backtest_outputs/sweep"),
    )

Adds two metrics on top of `backtest.run_backtest`:
  - per_trade_sharpe = mean_pnl / std_pnl
  - max_drawdown_dollars (from cumulative PnL walk)
"""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .config import CFG
from .backtest import run_backtest


# ════════════════════════════════════════════════════════════════════════
#  Variant definitions — each is a dict of CFG overrides
# ════════════════════════════════════════════════════════════════════════
DEFAULT_VARIANTS: Dict[str, Dict] = {
    # The pre-sami baseline: raw empirical bank, no awareness corrections,
    # no NO-distance throttle, no calibration.
    "01_baseline_pre_sami": {
        "brti_dampening":                1.0,
        "empirical_blend":               1.0,
        "market_shrink":                 0.0,
        "no_side_edge_surcharge_cents":  0.0,
        "no_near_strike_distance_usd":   0.0,
        "platt_enabled":                 False,
        "robust_min_pass_rate":          1.0,
        "min_edge_cents":                2.5,
        "max_spread_cents":              3,
        "min_entry_price":               0.20,
        "max_entry_price":               0.80,
        "stop_loss_pct":                 0.20,
        "take_profit_cents":             5.0,
    },

    # The current model: sami's awareness layer (gentle), throttle, Platt.
    "02_v2_current_defaults": {
        # Use CFG defaults; this is the "what's running right now" variant.
    },

    # Awareness only (no NO-distance throttle).
    "03_awareness_no_throttle": {
        "no_near_strike_distance_usd": 0.0,
    },

    # NO-distance throttle only (awareness disabled).
    "04_throttle_no_awareness": {
        "brti_dampening":                1.0,
        "empirical_blend":               1.0,
        "market_shrink":                 0.0,
        "no_side_edge_surcharge_cents":  0.0,
        "no_near_strike_distance_usd":   115.0,
    },

    # Sami's tight gates (her published live config): higher edge floor,
    # tighter spread, narrower entry band, strong-prob analog via NO-surcharge.
    "05_sami_tight_gates": {
        "brti_dampening":                0.80,
        "empirical_blend":               0.70,
        "market_shrink":                 0.25,
        "no_side_edge_surcharge_cents":  3.0,
        "min_edge_cents":                8.0,
        "max_spread_cents":              2,
        "min_entry_price":               0.25,
        "max_entry_price":               0.75,
    },

    # Wider take-profit experiment — fix the TP/SL asymmetry we identified.
    "06_wider_tp": {
        "take_profit_cents": 8.0,
    },

    # Even wider TP — let winners run.
    "07_widest_tp": {
        "take_profit_cents": 12.0,
    },

    # Tighter stop-loss instead — limit downside.
    "08_tighter_sl": {
        "stop_loss_pct":     0.12,
        "take_profit_cents": 6.0,
    },

    # No robust filter at all — see what the raw model picks up.
    "09_no_robust_filter": {
        "robust_enabled":         False,
    },

    # Tighter robust filter — require unanimity (was the v2 original).
    "10_unanimous_robust": {
        "robust_min_pass_rate":   1.0,
        "robust_min_mean_edge_c": 2.0,
    },

    # Stronger BRTI dampening (sami's brti_065 research result).
    "11_strong_brti_dampening": {
        "brti_dampening":   0.65,
    },

    # Pure empirical (no lognormal blend).
    "12_pure_empirical": {
        "empirical_blend":  1.0,
    },

    # Pure lognormal (sanity check — should be worse).
    "13_pure_lognormal": {
        "empirical_blend":  0.0,
    },
}


# ════════════════════════════════════════════════════════════════════════
#  CFG override context manager — safe per-variant changes
# ════════════════════════════════════════════════════════════════════════
class _CfgOverride:
    """Temporarily apply overrides to the global CFG dict, restore on exit."""
    def __init__(self, overrides: Dict):
        self.overrides = overrides
        self._snapshot: Dict = {}

    def __enter__(self):
        for k, v in self.overrides.items():
            self._snapshot[k] = CFG.get(k, "__missing__")
            CFG[k] = v
        return self

    def __exit__(self, *args):
        for k, v in self._snapshot.items():
            if v == "__missing__":
                CFG.pop(k, None)
            else:
                CFG[k] = v


# ════════════════════════════════════════════════════════════════════════
#  Per-variant metrics
# ════════════════════════════════════════════════════════════════════════
def _compute_extended_metrics(trades_csv: Path) -> Dict:
    """Read trades.csv from a single variant run; compute Sharpe + max DD."""
    if not trades_csv.exists():
        return {"per_trade_sharpe": 0.0, "max_drawdown": 0.0}
    df = pd.read_csv(trades_csv)
    if len(df) == 0:
        return {"per_trade_sharpe": 0.0, "max_drawdown": 0.0}
    pnls = df["net_pnl"].to_numpy()
    mean = pnls.mean()
    std  = pnls.std(ddof=1) if len(pnls) > 1 else 0.0
    sharpe = (mean / std) if std > 0 else 0.0
    annualized_sharpe = sharpe * math.sqrt(len(pnls))   # rough — uses n trades
    # Max drawdown of cumulative PnL walk
    cum = pnls.cumsum()
    peak = -math.inf
    max_dd = 0.0
    for v in cum:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return {
        "per_trade_sharpe":  float(sharpe),
        "annualized_sharpe": float(annualized_sharpe),
        "max_drawdown":      float(max_dd),
    }


# ════════════════════════════════════════════════════════════════════════
#  Orchestrator
# ════════════════════════════════════════════════════════════════════════
def run_variant_sweep(duckdb_path: Path, output_dir: Path,
                        variants: Optional[Dict[str, Dict]] = None,
                        decision_interval_sec: int = 5,
                        decision_window_min: int = 30) -> pd.DataFrame:
    """Run `run_backtest` for each variant; emit comparison table."""
    if variants is None:
        variants = DEFAULT_VARIANTS

    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    for name, overrides in variants.items():
        print("\n" + "█" * 72)
        print(f"  VARIANT  {name}")
        print(f"  overrides: {overrides if overrides else '(use CFG defaults)'}")
        print("█" * 72)
        variant_dir = output_dir / name
        try:
            with _CfgOverride(overrides):
                s = run_backtest(
                    duckdb_path=duckdb_path,
                    output_dir=variant_dir,
                    decision_interval_sec=decision_interval_sec,
                    decision_window_min=decision_window_min,
                )
        except Exception as e:
            print(f"  ✗ variant {name} failed: {e}")
            continue
        if not s or s.get("n_trades", 0) == 0:
            rows.append({"variant": name, "n": 0, **{k: 0 for k in
                ("net_pnl","gross_pnl","fees","wins","win_rate","mean_pnl",
                 "best","worst","sharpe","max_dd")}})
            continue
        ext = _compute_extended_metrics(variant_dir / "trades.csv")
        rows.append({
            "variant":   name,
            "n":         s["n_trades"],
            "net_pnl":   round(s["total_net_pnl"], 3),
            "gross_pnl": round(s["total_gross_pnl"], 3),
            "fees":      round(s["total_fees"], 3),
            "wins":      s["wins"],
            "losses":    s["losses"],
            "win_rate":  round(s["win_rate"], 3),
            "mean_pnl":  round(s["mean_net"], 4),
            "median":    round(s["median_net"], 4),
            "best":      round(s["best_trade"], 3),
            "worst":     round(s["worst_trade"], 3),
            "std_pnl":   round(s["std_pnl"], 4),
            "sharpe":    round(ext["per_trade_sharpe"], 4),
            "ann_sharpe": round(ext["annualized_sharpe"], 3),
            "max_dd":    round(ext["max_drawdown"], 3),
            "throttled": s.get("throttled_count", 0),
        })

    df = pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True)
    df.to_csv(output_dir / "sweep_results.csv", index=False)

    # Pretty print sorted by net PnL
    print("\n" + "═" * 110)
    print("  VARIANT SWEEP — sorted by net PnL")
    print("═" * 110)
    cols = ["variant","n","net_pnl","win_rate","mean_pnl","sharpe","ann_sharpe","max_dd","worst","throttled"]
    print(df[cols].to_string(index=False))

    # Top-5 summary
    print("\n" + "─" * 70)
    print("  TOP 5 BY NET PnL")
    print("─" * 70)
    for _, r in df.head(5).iterrows():
        print(f"  {r['variant']:30s}  ${r['net_pnl']:+8.2f}  "
              f"WR {r['win_rate']*100:.1f}%  Sharpe {r['sharpe']:+.3f}  "
              f"({r['n']} trades, max DD ${r['max_dd']:.2f})")

    print("\n" + "─" * 70)
    print("  BOTTOM 5 BY NET PnL")
    print("─" * 70)
    for _, r in df.tail(5).iloc[::-1].iterrows():
        print(f"  {r['variant']:30s}  ${r['net_pnl']:+8.2f}  "
              f"WR {r['win_rate']*100:.1f}%  Sharpe {r['sharpe']:+.3f}  "
              f"({r['n']} trades, max DD ${r['max_dd']:.2f})")

    return df
