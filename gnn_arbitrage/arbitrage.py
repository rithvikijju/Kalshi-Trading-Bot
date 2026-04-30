"""Classical arbitrage detection on FX rate graphs.

A triangular arbitrage exists when a closed cycle i -> j -> k -> i has product
of executable rates > 1. Equivalently, on the graph with edge weights
w(i, j) = -log(rate(i, j)), the cycle has negative total weight. This module
provides:

  * find_arbitrage_cycles: enumerate profitable triangles (and arbitrary-length
    cycles up to a cap) for ground-truth labels and exhaustive backtesting.
  * cycle_profit: gross multiplicative profit of executing a cycle at the
    given snapshot's executable rates.
"""

from __future__ import annotations

from itertools import permutations

import numpy as np

from gnn_arbitrage.data import FXSnapshot


def cycle_profit(snap: FXSnapshot, cycle: tuple[int, ...]) -> float:
    """Multiplicative gross return of executing the cycle once.

    A cycle (c0, c1, ..., ck) starts and ends at c0; we walk c0 -> c1 -> ... -> c0.
    """
    if cycle[0] != cycle[-1]:
        cycle = (*cycle, cycle[0])
    p = 1.0
    for a, b in zip(cycle[:-1], cycle[1:]):
        p *= snap.executable_rate(a, b)
    return p


def find_arbitrage_cycles(
    snap: FXSnapshot,
    max_len: int = 3,
    min_profit: float = 1.0,
) -> list[tuple[tuple[int, ...], float]]:
    """Enumerate cycles up to max_len with gross profit > min_profit.

    Triangles dominate practical FX arbitrage; longer cycles are included for
    completeness and capped at max_len to keep enumeration tractable.
    """
    if max_len < 3:
        raise ValueError("max_len must be at least 3 (triangle).")

    n = snap.n
    found: list[tuple[tuple[int, ...], float]] = []

    # Triangles: enumerate ordered (i, j, k) with i < j < k once, check both
    # orientations.
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                for cyc in (
                    (i, j, k),
                    (i, k, j),
                ):
                    p = cycle_profit(snap, cyc)
                    if p > min_profit:
                        found.append((cyc, p))

    # Longer cycles: enumerate distinct permutations rooted at the smallest
    # node id to deduplicate rotations.
    for L in range(4, max_len + 1):
        for combo in _combinations(range(n), L):
            i0 = combo[0]
            rest = combo[1:]
            for perm in permutations(rest):
                cyc = (i0, *perm)
                p = cycle_profit(snap, cyc)
                if p > min_profit:
                    found.append((cyc, p))

    found.sort(key=lambda x: -x[1])
    return found


def _combinations(it, r):
    from itertools import combinations
    return combinations(it, r)


def edge_in_cycle_labels(
    pairs: list[tuple[int, int]],
    cycles: list[tuple[tuple[int, ...], float]],
) -> np.ndarray:
    """Binary label per edge: 1 if it appears in any profitable cycle.

    This is the per-edge supervision target for the GNN.
    """
    on_arb_edge = set()
    for cyc, _ in cycles:
        nodes = list(cyc)
        if nodes[0] != nodes[-1]:
            nodes.append(nodes[0])
        for a, b in zip(nodes[:-1], nodes[1:]):
            on_arb_edge.add((a, b))
    labels = np.array([1.0 if e in on_arb_edge else 0.0 for e in pairs], dtype=np.float32)
    return labels
