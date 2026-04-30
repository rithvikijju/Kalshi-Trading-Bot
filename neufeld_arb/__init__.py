"""Neufeld & Sester 2024 — Neural Networks Can Detect Model-Free Static
Arbitrage Strategies (arXiv:2306.16422v2).

Detects model-free static arbitrage in markets of vanilla call options
written on `d` underlying stocks. A static strategy `(a, h+, h-)` is an
initial cash position plus long/short positions in each option, held to
maturity without rebalancing.

A market `(K, π)` admits a model-free static arbitrage of magnitude ε
when there exists `(a, h)` with:

    1. Payoff I_S(K, a, h) >= 0 for every S in the prediction set
    2. Price f(π, a, h) <= -ε

A single deep MLP is trained per Algorithm 1 to map `(K, π)` to such a
strategy when one exists, using a feasibility penalty and a
sign-correctness penalty referenced against an LSIP-computed target.

Modules:
    payoff.py     - vanilla call payoffs Ψ_i and the static-strategy
                    payoff/price functions I_S and f.
    market.py     - Market and OptionsBundle dataclasses + sample
                    construction (paper Section 3.1.1).
    lsip.py       - LP-based V(K,π) target via scipy.linprog (paper Eq. 2.4).
    data.py       - Real options-chain fetcher via yfinance.
    model.py      - StaticArbDetector NN with bounded output layer.
    training.py   - Algorithm 1 trainer.
    backtester.py - Section 3.1.3-style replay backtester.
    paper.py      - Live paper trading: real chain -> NN -> JSONL log.
"""

from neufeld_arb.payoff import (
    vanilla_call,
    payoff_I_S,
    price_f,
    payoff_I_S_torch,
    price_f_torch,
)
from neufeld_arb.market import Market, OptionsBundle, sample_market, scale_to_unit_spot
from neufeld_arb.lsip import lsip_value, LSIPResult
from neufeld_arb.model import StaticArbDetector
from neufeld_arb.training import TrainConfig, train_detector

__all__ = [
    "vanilla_call",
    "payoff_I_S",
    "price_f",
    "payoff_I_S_torch",
    "price_f_torch",
    "Market",
    "OptionsBundle",
    "sample_market",
    "scale_to_unit_spot",
    "lsip_value",
    "LSIPResult",
    "StaticArbDetector",
    "TrainConfig",
    "train_detector",
]
