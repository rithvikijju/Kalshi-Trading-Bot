"""Backtester for the Neufeld-Sester static-arbitrage detector.

Replays a list of held-out markets through the trained NN and reports:

    * fraction of markets where the NN proposes a strategy with f < 0
      (i.e. a putative arbitrage)
    * fraction of those whose realised payoff exceeds 0 across an
      out-of-sample S grid (true feasibility check, not training grid)
    * net profit distribution I_S(K, a, h) - f(π, a, h) over S samples,
      mirroring Table 1 / Figure 2 of the paper
    * confusion against the LSIP target's sign (precision/recall)

Section 3.1.3 of the paper backtests on real chains observed across 33
trading days for AAPL/GOOG/MSFT/META/(?). The same `replay` function
handles either: pass it a list of `Market`s built from real chains and
optionally per-market terminal-price realisations to get the realised PnL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from neufeld_arb.lsip import lsip_value
from neufeld_arb.market import Market
from neufeld_arb.model import StaticArbDetector
from neufeld_arb.payoff import payoff_I_S, price_f


@dataclass
class BacktestRow:
    market_idx: int
    pred_a: float
    pred_price: float
    pred_min_payoff: float    # min I_S over the eval grid
    pred_mean_profit: float
    target_price: float
    target_is_arb: bool
    pred_is_arb: bool
    realised_profit: float | None = None


@dataclass
class BacktestSummary:
    rows: list[BacktestRow] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.rows)

    def confusion(self) -> dict:
        tp = sum(1 for r in self.rows if r.pred_is_arb and r.target_is_arb)
        tn = sum(1 for r in self.rows if not r.pred_is_arb and not r.target_is_arb)
        fp = sum(1 for r in self.rows if r.pred_is_arb and not r.target_is_arb)
        fn = sum(1 for r in self.rows if not r.pred_is_arb and r.target_is_arb)
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        return {"tp": tp, "tn": tn, "fp": fp, "fn": fn,
                "precision": precision, "recall": recall, "f1": f1,
                "accuracy": (tp + tn) / max(self.n, 1)}

    def profit_stats(self) -> dict:
        vals = np.array([r.pred_mean_profit for r in self.rows])
        if vals.size == 0:
            return {}
        return {
            "count": int(vals.size),
            "mean": float(vals.mean()),
            "std": float(vals.std()),
            "min": float(vals.min()),
            "p25": float(np.percentile(vals, 25)),
            "p50": float(np.percentile(vals, 50)),
            "p75": float(np.percentile(vals, 75)),
            "max": float(vals.max()),
        }

    def feasibility_violation_rate(self) -> float:
        return float(sum(1 for r in self.rows if r.pred_min_payoff < -1e-6) / max(self.n, 1))


def replay(
    model: StaticArbDetector,
    markets: list[Market],
    eval_grid_size: int = 1024,
    s_lo: float = 0.0,
    s_hi: float = 2.0,
    compute_lsip_target: bool = True,
    realised_S: np.ndarray | None = None,
    rng: np.random.Generator | None = None,
    device: str = "cpu",
) -> BacktestSummary:
    """Run each market through the NN, compute summary stats.

    `realised_S[i]` (if provided) is the per-market terminal underlying
    realisation used to compute Section 3.1.3-style realised PnL.
    """
    if rng is None:
        rng = np.random.default_rng(0)
    summary = BacktestSummary()
    model.eval()
    for i, m in enumerate(markets):
        a, hl, hs = model.propose(m.feature_vector())
        pred_price = price_f(a, hl, hs, m.pi_ask, m.pi_bid)

        S_grid = rng.uniform(s_lo, s_hi, size=(eval_grid_size, m.d))
        I = payoff_I_S(S_grid, m.K, m.asset_idx, a, hl, hs)
        # Net profit = payoff - price; mean over the eval grid.
        profit = (I - pred_price)
        target_price = 0.0
        target_is_arb = False
        if compute_lsip_target:
            res = lsip_value(m, grid_size=eval_grid_size, s_lo=s_lo, s_hi=s_hi, rng=rng)
            target_price = res.value
            target_is_arb = res.is_arbitrage

        realised_profit = None
        if realised_S is not None:
            S_real = np.asarray(realised_S[i], dtype=np.float64).reshape(1, -1)
            I_real = payoff_I_S(S_real, m.K, m.asset_idx, a, hl, hs)
            realised_profit = float(I_real[0] - pred_price)

        summary.rows.append(BacktestRow(
            market_idx=i,
            pred_a=a,
            pred_price=pred_price,
            pred_min_payoff=float(I.min()),
            pred_mean_profit=float(profit.mean()),
            target_price=target_price,
            target_is_arb=target_is_arb,
            pred_is_arb=pred_price < 0,
            realised_profit=realised_profit,
        ))
    return summary
