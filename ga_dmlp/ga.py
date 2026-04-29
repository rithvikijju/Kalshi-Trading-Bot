"""Phase GA: optimize RSI thresholds and intervals via genetic algorithm.

The chromosome has 8 genes: (RSIbuy_dn, intBuy_dn, RSIsell_dn, intSell_dn,
RSIbuy_up, intBuy_up, RSIsell_up, intSell_up). Bounds per the paper:
    RSIbuy values  in [5, 40]
    RSIsell values in [60, 95]
    intervals      in [5, 20]

Fitness is the simulated terminal capital from running the chromosome's
threshold rule on the training period:
    in downtrend, buy when RSI(intBuy_dn) < RSIbuy_dn, sell when
    RSI(intSell_dn) > RSIsell_dn; analogously in uptrend with the up genes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Chromosome:
    rsi_buy_dn: float
    int_buy_dn: int
    rsi_sell_dn: float
    int_sell_dn: int
    rsi_buy_up: float
    int_buy_up: int
    rsi_sell_up: float
    int_sell_up: int

    def as_tuple(self) -> tuple:
        return (
            self.rsi_buy_dn, self.int_buy_dn, self.rsi_sell_dn, self.int_sell_dn,
            self.rsi_buy_up, self.int_buy_up, self.rsi_sell_up, self.int_sell_up,
        )


@dataclass
class GAConfig:
    population_size: int = 50
    generations: int = 25
    crossover_rate: float = 0.7
    mutation_rate: float = 0.001
    elitism: int = 2
    initial_capital: float = 10_000.0
    commission: float = 1.0
    seed: int = 0


def _random_chromosome(rng: np.random.Generator) -> Chromosome:
    return Chromosome(
        rsi_buy_dn=float(rng.integers(5, 41)),
        int_buy_dn=int(rng.integers(5, 21)),
        rsi_sell_dn=float(rng.integers(60, 96)),
        int_sell_dn=int(rng.integers(5, 21)),
        rsi_buy_up=float(rng.integers(5, 41)),
        int_buy_up=int(rng.integers(5, 21)),
        rsi_sell_up=float(rng.integers(60, 96)),
        int_sell_up=int(rng.integers(5, 21)),
    )


def simulate_chromosome(
    chrom: Chromosome,
    close: pd.Series,
    rsi_columns: pd.DataFrame,
    trend: pd.Series,
    initial_capital: float = 10_000.0,
    commission: float = 1.0,
) -> float:
    """Run the chromosome's RSI-threshold rule and return terminal equity."""
    cash = initial_capital
    shares = 0.0
    last_action = "hold"

    rsi_dn_buy = rsi_columns[f"rsi_{chrom.int_buy_dn}"].to_numpy()
    rsi_dn_sell = rsi_columns[f"rsi_{chrom.int_sell_dn}"].to_numpy()
    rsi_up_buy = rsi_columns[f"rsi_{chrom.int_buy_up}"].to_numpy()
    rsi_up_sell = rsi_columns[f"rsi_{chrom.int_sell_up}"].to_numpy()
    prices = close.to_numpy()
    trends = trend.to_numpy()

    for i in range(len(prices)):
        price = prices[i]
        if not np.isfinite(price):
            continue
        is_up = trends[i] > 0.5
        if is_up:
            buy_sig = rsi_up_buy[i] < chrom.rsi_buy_up
            sell_sig = rsi_up_sell[i] > chrom.rsi_sell_up
        else:
            buy_sig = rsi_dn_buy[i] < chrom.rsi_buy_dn
            sell_sig = rsi_dn_sell[i] > chrom.rsi_sell_dn

        if buy_sig and shares == 0.0 and last_action != "buy":
            shares = max(0.0, (cash - commission) / price)
            cash = 0.0
            last_action = "buy"
        elif sell_sig and shares > 0.0 and last_action != "sell":
            cash = shares * price - commission
            shares = 0.0
            last_action = "sell"

    if shares > 0.0:
        cash = shares * prices[-1] - commission
    return float(cash)


def _crossover(a: Chromosome, b: Chromosome, rng: np.random.Generator) -> Chromosome:
    """Single-point crossover on the 8-gene tuple."""
    pt = int(rng.integers(1, 8))
    ta, tb = a.as_tuple(), b.as_tuple()
    child = ta[:pt] + tb[pt:]
    return Chromosome(*child)


def _mutate(c: Chromosome, rate: float, rng: np.random.Generator) -> Chromosome:
    genes = list(c.as_tuple())
    bounds = [
        (5, 40), (5, 20), (60, 95), (5, 20),
        (5, 40), (5, 20), (60, 95), (5, 20),
    ]
    for i, (lo, hi) in enumerate(bounds):
        if rng.random() < rate:
            genes[i] = float(rng.integers(lo, hi + 1))
    # Cast intervals back to int.
    genes[1] = int(genes[1]); genes[3] = int(genes[3])
    genes[5] = int(genes[5]); genes[7] = int(genes[7])
    return Chromosome(*genes)


def run_ga(
    close: pd.Series,
    rsi_columns: pd.DataFrame,
    trend: pd.Series,
    config: GAConfig | None = None,
    verbose: bool = False,
) -> tuple[Chromosome, float, list[float]]:
    """Run the GA and return (best_chromosome, best_fitness, fitness_history)."""
    cfg = config or GAConfig()
    rng = np.random.default_rng(cfg.seed)

    pop = [_random_chromosome(rng) for _ in range(cfg.population_size)]
    fitness = np.array(
        [simulate_chromosome(c, close, rsi_columns, trend, cfg.initial_capital, cfg.commission) for c in pop]
    )
    history = [float(fitness.max())]
    if verbose:
        print(f"gen   0  best_fit={history[-1]:.2f}")

    for gen in range(1, cfg.generations + 1):
        # Selection: rank-weighted roulette.
        order = np.argsort(-fitness)
        ranked = [pop[i] for i in order]
        ranked_fit = fitness[order]
        # Elitism: keep top k.
        next_pop = ranked[: cfg.elitism]

        # Build mating pool with rank-proportional weights (linear).
        weights = np.arange(len(ranked), 0, -1, dtype=float)
        weights /= weights.sum()

        while len(next_pop) < cfg.population_size:
            i, j = rng.choice(len(ranked), size=2, replace=False, p=weights)
            a, b = ranked[i], ranked[j]
            if rng.random() < cfg.crossover_rate:
                child = _crossover(a, b, rng)
            else:
                child = a
            child = _mutate(child, cfg.mutation_rate, rng)
            next_pop.append(child)

        pop = next_pop
        fitness = np.array(
            [simulate_chromosome(c, close, rsi_columns, trend, cfg.initial_capital, cfg.commission) for c in pop]
        )
        history.append(float(fitness.max()))
        if verbose:
            print(f"gen {gen:3d}  best_fit={history[-1]:.2f}")

    best_idx = int(np.argmax(fitness))
    return pop[best_idx], float(fitness[best_idx]), history
