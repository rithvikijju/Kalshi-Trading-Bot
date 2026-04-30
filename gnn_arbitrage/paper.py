"""Paper-trading broker: never sends real orders. Records intended cycle
executions to a JSONL file with the quotes used and the simulated PnL.

A cycle of length L incurs L taker fees. We use either a single global
fee (`fee_bps`) or per-leg fees from the live feed when available. The
broker enforces a daily-loss kill switch and a hard cap on the number of
trades per day so a misconfigured run can't do unbounded damage when you
later wire it to real execution.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from gnn_arbitrage.data import FXSnapshot


@dataclass
class PaperTrade:
    iso_time: str
    cycle_currencies: tuple[str, ...]
    notional_base: float
    base_currency: str
    legs: list[dict]  # per-leg: from, to, symbol, rate
    gross_return: float
    net_return: float
    pnl_base: float
    equity_after: float

    def to_dict(self) -> dict:
        return {
            "iso_time": self.iso_time,
            "cycle": list(self.cycle_currencies),
            "notional_base": self.notional_base,
            "base_currency": self.base_currency,
            "legs": self.legs,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "pnl_base": self.pnl_base,
            "equity_after": self.equity_after,
        }


@dataclass
class RiskCaps:
    max_daily_loss_base: float = 500.0
    max_trades_per_day: int = 200
    max_notional_base: float = 1_000.0


class PaperBroker:
    """Simulated execution against a static snapshot.

    Each `execute_cycle` call computes the cycle's gross product from the
    snapshot's executable rates, subtracts per-leg fees (from `fees` map or
    `fee_bps` fallback), updates equity, and appends a JSONL row.
    """

    def __init__(
        self,
        base_currency: str = "USDT",
        starting_equity: float = 10_000.0,
        fee_bps: float = 26.0,
        fees: dict[tuple[int, int], float] | None = None,
        log_path: str | Path = "paper_trades.jsonl",
        risk: RiskCaps | None = None,
    ):
        self.base_currency = base_currency
        self.equity = float(starting_equity)
        self.starting_equity = float(starting_equity)
        self.fee_default = fee_bps * 1e-4
        self.fees = fees or {}
        self.log_path = Path(log_path)
        self.risk = risk or RiskCaps()
        self.trades: list[PaperTrade] = []
        self._day = date.today()
        self._day_pnl = 0.0
        self._day_trade_count = 0
        # Truncate log so each session is self-contained.
        self.log_path.write_text("")

    # Risk gate ----------------------------------------------------------

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._day_pnl = 0.0
            self._day_trade_count = 0

    def can_trade(self) -> tuple[bool, str]:
        self._roll_day_if_needed()
        if self._day_trade_count >= self.risk.max_trades_per_day:
            return False, "daily_trade_cap"
        if -self._day_pnl >= self.risk.max_daily_loss_base:
            return False, "daily_loss_cap"
        return True, ""

    # Execution ----------------------------------------------------------

    def _leg_fee(self, i: int, j: int) -> float:
        return self.fees.get((i, j), self.fee_default)

    def cycle_gross_and_net(
        self,
        snap: FXSnapshot,
        cycle_idx: tuple[int, ...],
    ) -> tuple[float, float, list[dict]]:
        nodes = list(cycle_idx)
        if nodes[0] != nodes[-1]:
            nodes.append(nodes[0])
        gross = 1.0
        net = 1.0
        legs: list[dict] = []
        for a, b in zip(nodes[:-1], nodes[1:]):
            r = float(snap.rates_bid[a, b])
            f = self._leg_fee(a, b)
            gross *= r
            net *= r * (1.0 - f)
            legs.append(
                {
                    "from": snap.currencies[a],
                    "to": snap.currencies[b],
                    "rate": r,
                    "fee": f,
                }
            )
        return gross, net, legs

    def execute_cycle(
        self,
        snap: FXSnapshot,
        cycle_idx: tuple[int, ...],
        notional_base: float,
    ) -> PaperTrade | None:
        ok, reason = self.can_trade()
        if not ok:
            print(f"[PaperBroker] gated: {reason}")
            return None
        notional = min(float(notional_base), self.risk.max_notional_base)
        gross, net, legs = self.cycle_gross_and_net(snap, cycle_idx)
        if net <= 1.0:
            return None  # don't book negative-edge fills
        pnl = notional * (net - 1.0)
        self.equity += pnl
        self._day_pnl += pnl
        self._day_trade_count += 1
        cycle_ccys = tuple(snap.currencies[i] for i in cycle_idx)
        tr = PaperTrade(
            iso_time=datetime.now(timezone.utc).isoformat(),
            cycle_currencies=cycle_ccys,
            notional_base=notional,
            base_currency=self.base_currency,
            legs=legs,
            gross_return=gross,
            net_return=net,
            pnl_base=pnl,
            equity_after=self.equity,
        )
        self.trades.append(tr)
        with self.log_path.open("a") as f:
            f.write(json.dumps(tr.to_dict()) + "\n")
        return tr

    def summary(self) -> dict:
        return {
            "n_trades": len(self.trades),
            "starting_equity": self.starting_equity,
            "final_equity": self.equity,
            "total_return": self.equity / self.starting_equity - 1.0,
            "day_pnl": self._day_pnl,
            "day_trade_count": self._day_trade_count,
            "log_path": str(self.log_path),
        }
