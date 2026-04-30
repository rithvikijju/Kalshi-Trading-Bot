"""Construct directed graphs from FX snapshots for GNN consumption."""

from __future__ import annotations

import numpy as np

from gnn_arbitrage.data import FXSnapshot


def build_rate_graph(snap: FXSnapshot, ref: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (edge_index [2, E], edge_attr [E, F], node_attr [N, 2]).

    Edges are all ordered pairs (i, j) with i != j. Edge features are the
    per-snapshot centred log-rate, the same scaled to unit standard
    deviation, and the bid-ask spread.

    Node features use a reference currency `ref` to break the permutation
    symmetry that prevents anonymous-node MP-GNNs from scoring edges
    differently in a complete digraph: each node j carries
    [log(rate(ref, j)), log(rate(j, ref))]. These are observable from the
    rate matrix and provide a coherent global frame across snapshots.
    """
    n = snap.n
    pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
    e = len(pairs)
    edge_index = np.array(pairs, dtype=np.int64).T  # [2, E]

    # Live-feed snapshots may have rate=0 for unlisted pairs. Replace the log
    # of 0/non-positive rates with a large negative penalty so the GNN treats
    # them as unusable, but does not blow up on -inf.
    UNLISTED_LOG = -50.0

    def _safe_log(x: float) -> float:
        return float(np.log(x)) if x > 0 else UNLISTED_LOG

    rates_list = [snap.executable_rate(i, j) for i, j in pairs]
    log_rates = np.array([_safe_log(r) for r in rates_list], dtype=np.float32)
    listed_mask = np.array([r > 0 for r in rates_list], dtype=bool)
    if listed_mask.any():
        listed_mean = float(log_rates[listed_mask].mean())
    else:
        listed_mean = 0.0
    log_rates_centered = log_rates - listed_mean
    log_rates_centered[~listed_mask] = UNLISTED_LOG

    spread = np.zeros(len(pairs), dtype=np.float32)
    for idx, (i, j) in enumerate(pairs):
        b = float(snap.rates_bid[i, j]); a = float(snap.rates_ask[i, j])
        spread[idx] = float(np.log(a / b)) if (b > 0 and a > 0) else 0.0

    # Cycle-aware features: best and mean log-profit of triangles through (i, j).
    log_rate_mat = np.full((n, n), UNLISTED_LOG, dtype=np.float64)
    for a in range(n):
        for b in range(n):
            r = snap.executable_rate(a, b)
            if r > 0:
                log_rate_mat[a, b] = float(np.log(r))
    np.fill_diagonal(log_rate_mat, 0.0)
    best_tri = np.zeros(len(pairs), dtype=np.float32)
    mean_tri = np.zeros(len(pairs), dtype=np.float32)
    for idx, (i, j) in enumerate(pairs):
        ks = [k for k in range(n) if k != i and k != j]
        if not ks:
            continue
        cycle_logs = np.array([log_rate_mat[i, j] + log_rate_mat[j, k] + log_rate_mat[k, i] for k in ks])
        best_tri[idx] = float(cycle_logs.max())
        mean_tri[idx] = float(cycle_logs.mean())

    edge_attr = np.stack([log_rates_centered, spread, best_tri, mean_tri], axis=1).astype(np.float32)

    # Reference-currency anchored node features.
    log_in = np.array([_safe_log(snap.rates_bid[ref, j]) if j != ref else 0.0 for j in range(n)],
                      dtype=np.float32)
    log_out = np.array([_safe_log(snap.rates_bid[j, ref]) if j != ref else 0.0 for j in range(n)],
                       dtype=np.float32)
    node_attr = np.stack([log_in, log_out], axis=1)
    return edge_index, edge_attr, node_attr


def edge_index_from_snapshot(snap: FXSnapshot) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Convenience: edge_index plus the list of (i, j) pairs in the same order."""
    n = snap.n
    pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
    edge_index = np.array(pairs, dtype=np.int64).T
    return edge_index, pairs
