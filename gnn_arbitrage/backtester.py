"""Backtester for GNN-driven triangular arbitrage strategy.

Trading rule:
    1. At each snapshot, build the rate graph and predict per-edge arbitrage
       probabilities with the GNN.
    2. Enumerate candidate triangles whose edges all exceed `edge_threshold`.
    3. Compute the gross multiplicative profit of each triangle at executable
       (bid) rates, subtract a per-leg fee, and rank by net profit.
    4. Execute the top triangle if net profit > `min_net_edge` (in bps),
       sized at `trade_notional`.
    5. Track PnL, win rate, hit rate against ground truth, and a precision
       metric (was the GNN signal a real arbitrage?).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from typing import Iterable

import numpy as np

from gnn_arbitrage.arbitrage import cycle_profit, find_arbitrage_cycles
from gnn_arbitrage.data import FXSnapshot
from gnn_arbitrage.graph import build_rate_graph
from gnn_arbitrage.gnn_model import EdgeArbitrageGNN, predict_edge_scores


@dataclass
class Trade:
    t: int
    cycle: tuple[int, ...]
    gross_return: float
    net_return: float
    pnl_usd: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)
    missed_arbs: int = 0
    false_signals: int = 0
    true_signals: int = 0

    @property
    def total_return(self) -> float:
        if not self.equity_curve:
            return 0.0
        return self.equity_curve[-1] / self.equity_curve[0] - 1.0

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for tr in self.trades if tr.net_return > 1.0)
        return wins / len(self.trades)

    @property
    def precision(self) -> float:
        denom = self.true_signals + self.false_signals
        if denom == 0:
            return float("nan")
        return self.true_signals / denom

    def summary(self) -> dict:
        if not self.trades:
            avg_net = 0.0
        else:
            avg_net = float(np.mean([tr.net_return - 1.0 for tr in self.trades]))
        return {
            "n_trades": len(self.trades),
            "total_return": self.total_return,
            "final_equity": self.equity_curve[-1] if self.equity_curve else 0.0,
            "win_rate": self.win_rate,
            "avg_net_return_per_trade_bps": avg_net * 1e4,
            "precision_vs_truth": self.precision,
            "missed_arbs": self.missed_arbs,
        }


class Backtester:
    def __init__(
        self,
        model: EdgeArbitrageGNN | None,
        starting_equity: float = 100_000.0,
        trade_notional: float = 10_000.0,
        fee_bps_per_leg: float = 1.0,
        slippage_bps_per_leg: float = 0.5,
        edge_threshold: float = 0.5,
        min_net_edge_bps: float = 1.0,
        max_cycle_len: int = 3,
        device: str = "cpu",
    ):
        self.model = model
        self.starting_equity = starting_equity
        self.trade_notional = trade_notional
        self.fee = fee_bps_per_leg * 1e-4
        self.slip = slippage_bps_per_leg * 1e-4
        self.edge_threshold = edge_threshold
        self.min_net_edge = min_net_edge_bps * 1e-4
        self.max_cycle_len = max_cycle_len
        self.device = device

    def _candidate_cycles(
        self,
        n: int,
        edge_score: dict[tuple[int, int], float],
    ) -> Iterable[tuple[int, ...]]:
        """Yield triangles whose three directed edges all clear edge_threshold."""
        nodes = list(range(n))
        for i in nodes:
            for j in nodes:
                if j == i:
                    continue
                if edge_score.get((i, j), 0.0) < self.edge_threshold:
                    continue
                for k in nodes:
                    if k in (i, j):
                        continue
                    if edge_score.get((j, k), 0.0) < self.edge_threshold:
                        continue
                    if edge_score.get((k, i), 0.0) < self.edge_threshold:
                        continue
                    yield (i, j, k)

    def _execute_cycle(self, snap: FXSnapshot, cyc: tuple[int, ...]) -> tuple[float, float]:
        """Return (gross_return, net_return) for cycling once through cyc.

        Net return applies (fee + slippage) per leg multiplicatively.
        """
        gross = cycle_profit(snap, cyc)
        legs = len(cyc)
        cost_factor = (1.0 - self.fee - self.slip) ** legs
        net = gross * cost_factor
        return gross, net

    def run(self, snapshots: list[FXSnapshot]) -> BacktestResult:
        result = BacktestResult()
        equity = self.starting_equity
        result.equity_curve.append(equity)
        result.timestamps.append(snapshots[0].t if snapshots else 0)

        for snap in snapshots:
            # Ground truth arbitrage cycles (for precision metric).
            truths = find_arbitrage_cycles(snap, max_len=self.max_cycle_len, min_profit=1.0)
            truth_cycles = {tuple(c) for c, _ in truths}

            # GNN edge scores -> candidate cycles.
            edge_score: dict[tuple[int, int], float] = {}
            chosen_cycle = None
            best_net = 1.0
            best_gross = 1.0

            if self.model is not None:
                ei, ea, x = build_rate_graph(snap)
                scores = predict_edge_scores(self.model, x, ei, ea, device=self.device)
                for idx, (a, b) in enumerate(zip(ei[0], ei[1])):
                    edge_score[(int(a), int(b))] = float(scores[idx])

                for cyc in self._candidate_cycles(snap.n, edge_score):
                    gross, net = self._execute_cycle(snap, cyc)
                    if net > best_net + self.min_net_edge:
                        best_net = net
                        best_gross = gross
                        chosen_cycle = cyc

            # Execute chosen cycle (if any). All-in single-leg notional.
            if chosen_cycle is not None:
                pnl = self.trade_notional * (best_net - 1.0)
                equity += pnl
                result.trades.append(
                    Trade(
                        t=snap.t,
                        cycle=chosen_cycle,
                        gross_return=best_gross,
                        net_return=best_net,
                        pnl_usd=pnl,
                    )
                )
                # Precision: did the gross gross-rate-only cycle truly arb?
                if _rotations(chosen_cycle) & truth_cycles:
                    result.true_signals += 1
                else:
                    result.false_signals += 1

            # Missed: there was a profitable arb but we didn't trade it.
            if truths and chosen_cycle is None:
                result.missed_arbs += 1

            result.equity_curve.append(equity)
            result.timestamps.append(snap.t)

        return result


def _rotations(cyc: tuple[int, ...]) -> set[tuple[int, ...]]:
    """All rotations of a cycle, treated as equivalent."""
    L = len(cyc)
    return {tuple(cyc[i:] + cyc[:i]) for i in range(L)}


def baseline_optimal_run(
    snapshots: list[FXSnapshot],
    fee_bps_per_leg: float = 1.0,
    slippage_bps_per_leg: float = 0.5,
    starting_equity: float = 100_000.0,
    trade_notional: float = 10_000.0,
    max_cycle_len: int = 3,
) -> BacktestResult:
    """Oracle baseline: at each step, take the most profitable known cycle.

    Useful as an upper bound on what the GNN strategy could achieve under the
    same fee model.
    """
    result = BacktestResult()
    equity = starting_equity
    result.equity_curve.append(equity)
    result.timestamps.append(snapshots[0].t if snapshots else 0)

    fee = fee_bps_per_leg * 1e-4
    slip = slippage_bps_per_leg * 1e-4

    for snap in snapshots:
        cycles = find_arbitrage_cycles(snap, max_len=max_cycle_len, min_profit=1.0)
        if cycles:
            cyc, gross = cycles[0]
            net = gross * (1.0 - fee - slip) ** len(cyc)
            if net > 1.0:
                pnl = trade_notional * (net - 1.0)
                equity += pnl
                result.trades.append(
                    Trade(t=snap.t, cycle=cyc, gross_return=gross, net_return=net, pnl_usd=pnl)
                )
                result.true_signals += 1
        result.equity_curve.append(equity)
        result.timestamps.append(snap.t)
    return result
