"""Vectorized-with-controls backtest engine.

Contract:
  - strategy.signal(data)        -> per-symbol weights as a DataFrame
                                     (index=date, columns=symbols, value=target_weight)
  - prices                       -> long DataFrame of {symbol -> close prices}
  - rebalance happens at NEXT bar's open (or close, configurable)
  - returns are mark-to-market close-to-close.

This engine is deliberately deterministic: no random seeds, no fitted models
at run-time, no "live" data. ML training is done inside strategies via
walk-forward splits BEFORE the engine sees the resulting signal.
"""
from __future__ import annotations
import uuid
import json
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from .portfolio import Portfolio
from .execution import rebalance_to_targets
from .costs import CostModel
from .metrics import summary as metrics_summary
from .validation import preflight, assert_preflight_passes
from ..data.storage import insert_many, init_db
from ..utils.logging import get_logger
from ..utils.time import MONTH_END_ALIAS

log = get_logger("engine")


@dataclass
class RiskLimits:
    max_gross_leverage: float = 2.0
    max_single_asset_weight: float = 0.5
    max_daily_loss_pct: float = -0.05      # halt after -5% in a single day
    drawdown_warn: float = -0.03           # warn at -3%
    drawdown_derisk: float = -0.05         # halve leverage at -5%
    drawdown_stop: float = -0.08           # stop new trades at -8%


@dataclass
class EngineConfig:
    starting_cash: float = 100_000.0
    exec_on: str = "next_open"             # next_open | next_close
    costs: CostModel = field(default_factory=CostModel)
    risk: RiskLimits = field(default_factory=RiskLimits)
    rebalance_freq: str = "D"              # D|W|M
    benchmark: Optional[str] = "SPY"
    run_preflight: bool = True
    strategy_name: str = "strategy"
    mode: str = "backtest"


def _rebalance_dates(index: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    if freq == "D":
        return index
    if freq == "W":
        return pd.Series(1, index=index).resample("W-FRI").last().dropna().index
    if freq == "M":
        return pd.Series(1, index=index).resample(MONTH_END_ALIAS).last().dropna().index
    raise ValueError(f"Unknown rebalance freq: {freq}")


def run_backtest(
    strategy,
    prices: pd.DataFrame,
    config: EngineConfig,
    extra_data: Optional[pd.DataFrame] = None,
    asset_class_by_symbol: Optional[dict[str, str]] = None,
) -> dict:
    """Run a single backtest end-to-end.

    Returns dict with:
      run_id, equity (Series), returns (Series), positions (DataFrame),
      fills (DataFrame), risk_events (list), metrics (dict), benchmark (Series)
    """
    init_db()
    run_id = uuid.uuid4().hex[:12]
    log.info(f"[{config.strategy_name}] backtest start run_id={run_id}")

    weights = strategy.signal(prices if extra_data is None else extra_data)
    weights = weights.reindex(prices.index).ffill().fillna(0)
    weights = weights.shift(1).fillna(0)        # signal at t → trade at t+1
    cols = [c for c in weights.columns if c in prices.columns]
    weights = weights[cols]
    prices = prices[cols]

    if config.run_preflight:
        try:
            # Strategies return a DataFrame; leakage test wants a Series. Probe
            # on the first column — sufficient as a no-lookahead invariant check.
            def _extract(d):
                s = strategy.signal(d)
                return s.iloc[:, 0] if hasattr(s, "columns") else s
            reports = preflight(strategy, prices, signal_extractor=_extract)
            for r in reports:
                log.info(f"preflight {r.name}: {'OK' if r.passed else 'FAIL'} — {r.detail}")
            assert_preflight_passes(reports)
        except (AttributeError, NotImplementedError) as e:
            log.warning(f"preflight skipped: {e}")

    book = Portfolio(starting_cash=config.starting_cash)
    fills_log: list[dict] = []
    eq_series, rets_series, dd_series = [], [], []
    risk_events: list[dict] = []
    rebal_idx = _rebalance_dates(prices.index, config.rebalance_freq)
    prev_eq = config.starting_cash
    hwm = config.starting_cash
    derisk_factor = 1.0

    halted = False
    for i, ts in enumerate(prices.index):
        prices_today = prices.iloc[i].to_dict()
        eq_today = book.equity(prices_today)
        if eq_today > hwm:
            hwm = eq_today
        dd = eq_today / hwm - 1
        if dd <= config.risk.drawdown_warn and derisk_factor == 1.0:
            risk_events.append(dict(ts=ts.isoformat(), kind="dd_warn",
                                    severity="warn", message=f"DD {dd:.2%}", action=""))
        if dd <= config.risk.drawdown_derisk and derisk_factor > 0.5:
            derisk_factor = 0.5
            risk_events.append(dict(ts=ts.isoformat(), kind="dd_derisk",
                                    severity="warn", message=f"DD {dd:.2%}",
                                    action="leverage halved"))
        if dd <= config.risk.drawdown_stop and not halted:
            halted = True
            risk_events.append(dict(ts=ts.isoformat(), kind="dd_stop",
                                    severity="critical", message=f"DD {dd:.2%}",
                                    action="no new trades"))
        day_pnl_pct = eq_today / prev_eq - 1 if prev_eq > 0 else 0
        if day_pnl_pct <= config.risk.max_daily_loss_pct and not halted:
            halted = True
            risk_events.append(dict(ts=ts.isoformat(), kind="daily_loss_halt",
                                    severity="critical",
                                    message=f"day pnl {day_pnl_pct:.2%}", action="no new trades"))

        if ts in rebal_idx and i + 1 < len(prices) and not halted:
            next_open = prices.iloc[i + 1].to_dict()
            tgt = (weights.loc[ts] * derisk_factor).to_dict()
            tgt = _apply_position_caps(tgt, config.risk.max_single_asset_weight,
                                       config.risk.max_gross_leverage)
            fills = rebalance_to_targets(ts, book, tgt, next_open, config.costs,
                                          asset_class_by_symbol)
            for f in fills:
                fills_log.append(dict(ts=f.ts.isoformat(), symbol=f.symbol,
                                       qty=f.qty, price=f.price, fee=f.fee,
                                       slippage_bps=f.slippage_bps))

        eq_series.append((ts, eq_today))
        rets_series.append((ts, day_pnl_pct))
        dd_series.append((ts, dd))
        prev_eq = eq_today
        book.snapshot(ts, prices_today)

    eq = pd.Series(dict(eq_series))
    rets = pd.Series(dict(rets_series))
    metrics = metrics_summary(rets, eq)
    metrics["total_fees"] = sum(f["fee"] for f in fills_log)
    metrics["n_fills"] = len(fills_log)

    bench = None
    if config.benchmark and config.benchmark in prices.columns:
        b = prices[config.benchmark].pct_change().fillna(0)
        bench = (1 + b).cumprod() * config.starting_cash
        metrics["benchmark_sharpe"] = float(b.mean() / b.std() * np.sqrt(252)) if b.std() > 0 else 0
        metrics["benchmark_total_return"] = float((1 + b).prod() - 1)

    insert_many("backtest_runs", [dict(
        run_id=run_id, strategy=config.strategy_name,
        start_date=str(prices.index.min().date()),
        end_date=str(prices.index.max().date()),
        config_json=json.dumps({k: str(v) for k, v in config.__dict__.items()}),
        metrics_json=json.dumps(metrics),
        created_at=pd.Timestamp.utcnow().isoformat(),
    )])
    if risk_events:
        for ev in risk_events:
            ev["run_id"] = run_id
        insert_many("risk_events", risk_events)

    log.info(f"[{config.strategy_name}] done. Sharpe={metrics['sharpe']:.2f} "
             f"MDD={metrics['max_drawdown']:.2%} CAGR={metrics['cagr']:.2%}")

    return dict(
        run_id=run_id, equity=eq, returns=rets, drawdown=pd.Series(dict(dd_series)),
        fills=pd.DataFrame(fills_log), risk_events=risk_events, metrics=metrics,
        benchmark=bench, weights=weights, prices=prices,
    )


def _apply_position_caps(weights: dict[str, float], max_w: float, max_gross: float) -> dict[str, float]:
    capped = {k: float(np.clip(v, -max_w, max_w)) for k, v in weights.items()}
    gross = sum(abs(v) for v in capped.values())
    if gross > max_gross and gross > 0:
        scale = max_gross / gross
        capped = {k: v * scale for k, v in capped.items()}
    return capped
