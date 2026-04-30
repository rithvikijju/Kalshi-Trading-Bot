"""Sezer/Ozbayoglu/Dogdu (2017) GA + Deep MLP stock trading system.

Implements the six-phase pipeline from "A Deep Neural-Network Based Stock
Trading System Based on Evolutionary Optimized Technical Analysis Parameters"
(Procedia Computer Science 114 (2017) 473-480):

    Phase 0 - adjust OHLC by adjusted-close ratio
    Phase 1 - RSI for intervals 1..20, SMA-50 / SMA-200 trend direction
    Phase GA - 8-gene chromosome (RSIbuy, intBuy, RSIsell, intSell) x {down, up}
               population=50, crossover=0.7, mutation=0.001, fitness=training profit
    Phase 2 - build MLP training set (buy/sell from best chromosome, hold from
              in-between RSI)
    Phase 3 - MLP topology (3, 20, 10, 8, 6, 5, 3), 200 epochs, input=
              (RSI value, RSI interval, trend direction), output=buy/sell/hold
    Phase 4 - voting across the 20 RSI intervals; if any class count > 14, that
              is the final label, otherwise hold
    Phase 5 - financial evaluation: $10k starting capital, all-in per trade,
              $1 commission, 10% stop loss, ignore repeated labels
"""

from ga_dmlp.indicators import compute_rsi, compute_sma, adjust_ohlc
from ga_dmlp.ga import Chromosome, GAConfig, run_ga
from ga_dmlp.mlp import MLPConfig, build_training_data, train_mlp, predict_with_voting
from ga_dmlp.backtester import financial_evaluation, BacktestStats

__all__ = [
    "compute_rsi",
    "compute_sma",
    "adjust_ohlc",
    "Chromosome",
    "GAConfig",
    "run_ga",
    "MLPConfig",
    "build_training_data",
    "train_mlp",
    "predict_with_voting",
    "financial_evaluation",
    "BacktestStats",
]
