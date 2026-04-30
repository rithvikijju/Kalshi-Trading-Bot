"""Phase 5: financial evaluation per Sezer 2017.

Rules verbatim:
    * Starting capital $10,000.
    * Each transaction uses all available capital.
    * If the same label repeats, only the first triggers a transaction.
    * $1 commission per transaction.
    * 10% stop-loss: if an open long position drops 10% from the entry price,
      sell at that level on the next bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


HOLD, BUY, SELL = 0, 1, 2


@dataclass
class Trade:
    entry_t: int
    exit_t: int
    entry_price: float
    exit_price: float
    shares: float
    reason: str  # "signal" or "stoploss"

    @property
    def return_pct(self) -> float:
        return self.exit_price / self.entry_price - 1.0


@dataclass
class BacktestStats:
    final_capital: float
    annualized_return: float
    n_trades: int
    annual_n_trades: float
    pct_success: float
    avg_profit_per_trade: float
    avg_trade_length: float
    max_profit_per_trade: float
    max_loss_per_trade: float
    max_capital: float
    min_capital: float
    idle_ratio: float
    equity_curve: np.ndarray = field(default_factory=lambda: np.array([]))
    trades: list[Trade] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "final_capital": self.final_capital,
            "annualized_return_pct": self.annualized_return * 100,
            "n_trades": self.n_trades,
            "annualized_trades_per_year": self.annual_n_trades,
            "pct_success": self.pct_success * 100,
            "avg_profit_per_trade_pct": self.avg_profit_per_trade * 100,
            "avg_trade_length_days": self.avg_trade_length,
            "max_profit_per_trade_pct": self.max_profit_per_trade * 100,
            "max_loss_per_trade_pct": self.max_loss_per_trade * 100,
            "max_capital": self.max_capital,
            "min_capital": self.min_capital,
            "idle_ratio_pct": self.idle_ratio * 100,
        }


def financial_evaluation(
    prices: pd.Series,
    signals: np.ndarray,
    initial_capital: float = 10_000.0,
    commission: float = 1.0,
    stop_loss: float = 0.10,
) -> BacktestStats:
    """Run Phase 5 over a series of close prices and discrete trade signals."""
    p = prices.to_numpy()
    n = len(p)
    if signals.shape[0] != n:
        raise ValueError("signals length must match prices length.")

    cash = initial_capital
    shares = 0.0
    last_action = HOLD
    entry_t = -1
    entry_price = 0.0
    equity = np.zeros(n, dtype=float)
    trades: list[Trade] = []
    idle_bars = 0

    for t in range(n):
        price = p[t]
        if not np.isfinite(price):
            equity[t] = cash + shares * (p[t - 1] if t > 0 else 0.0)
            continue

        # Stop loss check while long.
        if shares > 0.0:
            stop_price = entry_price * (1.0 - stop_loss)
            if price <= stop_price:
                cash = shares * price - commission
                trades.append(Trade(entry_t, t, entry_price, price, shares, "stoploss"))
                shares = 0.0
                last_action = SELL
                equity[t] = cash
                continue

        sig = int(signals[t])
        if sig == BUY and last_action != BUY and shares == 0.0 and cash > commission:
            shares = (cash - commission) / price
            cash = 0.0
            entry_t = t
            entry_price = price
            last_action = BUY
        elif sig == SELL and last_action != SELL and shares > 0.0:
            cash = shares * price - commission
            trades.append(Trade(entry_t, t, entry_price, price, shares, "signal"))
            shares = 0.0
            last_action = SELL
        # HOLD or repeated label: do nothing.

        equity[t] = cash + shares * price
        if shares == 0.0:
            idle_bars += 1

    # Force liquidation at end so all metrics are realized.
    if shares > 0.0:
        cash = shares * p[-1] - commission
        trades.append(Trade(entry_t, n - 1, entry_price, p[-1], shares, "signal"))
        shares = 0.0
        equity[-1] = cash

    final = float(equity[-1])
    if n > 1:
        years = n / 252.0
        ann_ret = (final / initial_capital) ** (1.0 / max(years, 1e-9)) - 1.0
    else:
        ann_ret = 0.0

    if trades:
        rets = np.array([tr.return_pct for tr in trades])
        lens = np.array([tr.exit_t - tr.entry_t for tr in trades])
        pct_success = float((rets > 0).mean())
        stats = BacktestStats(
            final_capital=final,
            annualized_return=ann_ret,
            n_trades=len(trades),
            annual_n_trades=len(trades) / max(years, 1e-9),
            pct_success=pct_success,
            avg_profit_per_trade=float(rets.mean()),
            avg_trade_length=float(lens.mean()),
            max_profit_per_trade=float(rets.max()),
            max_loss_per_trade=float(rets.min()),
            max_capital=float(equity.max()),
            min_capital=float(equity[equity > 0].min() if (equity > 0).any() else 0.0),
            idle_ratio=idle_bars / n,
            equity_curve=equity,
            trades=trades,
        )
    else:
        stats = BacktestStats(
            final_capital=final,
            annualized_return=ann_ret,
            n_trades=0,
            annual_n_trades=0.0,
            pct_success=0.0,
            avg_profit_per_trade=0.0,
            avg_trade_length=0.0,
            max_profit_per_trade=0.0,
            max_loss_per_trade=0.0,
            max_capital=float(equity.max()) if n else initial_capital,
            min_capital=initial_capital,
            idle_ratio=1.0,
            equity_curve=equity,
            trades=[],
        )
    return stats


def buy_and_hold(prices: pd.Series, initial_capital: float = 10_000.0, commission: float = 1.0) -> BacktestStats:
    """Reference baseline used in Table 5 of the paper."""
    n = len(prices)
    p0 = float(prices.iloc[0])
    pT = float(prices.iloc[-1])
    shares = (initial_capital - commission) / p0
    final = shares * pT - commission
    years = n / 252.0
    ann = (final / initial_capital) ** (1.0 / max(years, 1e-9)) - 1.0
    eq = (initial_capital - commission) * (prices.to_numpy() / p0)
    return BacktestStats(
        final_capital=final,
        annualized_return=ann,
        n_trades=1,
        annual_n_trades=1.0 / max(years, 1e-9),
        pct_success=1.0 if final > initial_capital else 0.0,
        avg_profit_per_trade=final / initial_capital - 1.0,
        avg_trade_length=float(n),
        max_profit_per_trade=final / initial_capital - 1.0,
        max_loss_per_trade=final / initial_capital - 1.0,
        max_capital=float(eq.max()),
        min_capital=float(eq.min()),
        idle_ratio=0.0,
        equity_curve=eq,
        trades=[],
    )
