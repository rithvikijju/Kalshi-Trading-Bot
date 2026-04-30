"""LSIP target V(K, π) via scipy.linprog (paper Eq. 2.4).

V(K, π) := inf_{(a, h) ∈ Γ(K)} f(π, a, h)

The semi-infinite constraint Γ(K) = { I_S(·) ≥ 0 for all S ∈ S } is
handled by sampling a finite grid of S values from the prediction set
and turning each into a linear inequality. Refining the grid drives the
linear program's optimum toward the true LSIP value (Neufeld et al.
2023). This is the supervised target Y_{i,b} in Algorithm 1.

Decision variable layout: x = [a, h+ (length N), h- (length N)] with
bounds a ∈ [a_min, ∞), h ∈ [0, H].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from neufeld_arb.market import Market


@dataclass
class LSIPResult:
    a: float
    h_long: np.ndarray
    h_short: np.ndarray
    value: float        # f(π, a, h) at the optimum
    success: bool
    message: str = ""

    @property
    def is_arbitrage(self) -> bool:
        return self.value < 0.0


def _payoff_matrix(market: Market, S_grid: np.ndarray) -> np.ndarray:
    """Per-(grid point, option) call payoff: shape [G, N]."""
    Si = S_grid[:, market.asset_idx]
    return np.maximum(Si - market.K[None, :], 0.0)


def lsip_value(
    market: Market,
    S_grid: np.ndarray | None = None,
    grid_size: int = 256,
    a_min: float = -1.0,
    H: float = 1.0,
    rng: np.random.Generator | None = None,
    s_lo: float = 0.0,
    s_hi: float = 2.0,
) -> LSIPResult:
    """Solve V(K, π) on a sampled grid of terminal scenarios.

    `grid_size` random uniform draws from [s_lo, s_hi]^d give the LP its
    constraints. A larger grid better approximates the true semi-infinite
    feasible set; with as few as 256 points the LP returns a strategy
    whose worst-case violation across the prediction set is small.
    """
    if rng is None:
        rng = np.random.default_rng()
    d = market.d
    N = market.N

    if S_grid is None:
        # Always include the corners (paper's [0, 2]^d limits) so extremes
        # are covered; pad to grid_size with random samples.
        corners = []
        # 2^d corner enumeration is fine for d <= 6; cap to limit explosion.
        if d <= 6:
            for mask in range(1 << d):
                corners.append([s_hi if (mask >> k) & 1 else s_lo for k in range(d)])
        corners = np.array(corners, dtype=np.float64) if corners else np.zeros((0, d))
        n_random = max(grid_size - corners.shape[0], 0)
        rand = rng.uniform(s_lo, s_hi, size=(n_random, d))
        S_grid = np.vstack([corners, rand]) if corners.size else rand

    payoffs = _payoff_matrix(market, S_grid)        # [G, N]

    # Variables: x = [a, h+ (N), h- (N)]; 1 + 2N total.
    n_vars = 1 + 2 * N
    # Objective: minimize a + π+ . h+ - π- . h-
    c = np.concatenate([[1.0], market.pi_ask, -market.pi_bid])

    # Constraint: I_S = a + (h+ - h-) . payoff(S) >= 0, i.e.
    # -a - h+ . payoff + h- . payoff <= 0
    G = payoffs.shape[0]
    A_ub = np.zeros((G, n_vars), dtype=np.float64)
    A_ub[:, 0] = -1.0
    A_ub[:, 1:1 + N] = -payoffs
    A_ub[:, 1 + N:] = +payoffs
    b_ub = np.zeros(G)

    bounds = [(a_min, None)] + [(0.0, H)] * (2 * N)

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success:
        return LSIPResult(
            a=0.0, h_long=np.zeros(N), h_short=np.zeros(N),
            value=0.0, success=False, message=res.message,
        )
    x = res.x
    a = float(x[0])
    h_long = x[1:1 + N].copy()
    h_short = x[1 + N:].copy()
    value = float(c @ x)
    return LSIPResult(a=a, h_long=h_long, h_short=h_short, value=value, success=True)
