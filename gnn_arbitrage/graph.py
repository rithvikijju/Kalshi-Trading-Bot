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

    rates = np.array([snap.executable_rate(i, j) for i, j in pairs], dtype=np.float32)
    log_rates = np.log(rates).astype(np.float32)
    log_rates_centered = log_rates - log_rates.mean()
    spread = np.array(
        [np.log(snap.rates_ask[i, j] / snap.rates_bid[i, j]) for i, j in pairs],
        dtype=np.float32,
    )

    # Cycle-aware features: for each directed edge (i, j), the best and mean
    # triangle profit through it. A vanilla MP-GNN cannot recover the
    # existential cycle quantifier on a complete digraph from local edge
    # weights alone; supplying the triangle aggregates as edge features gives
    # the network the right inductive bias and matches real-world arbitrage
    # systems that precompute cycle products.
    log_rate_mat = np.log(np.array([[snap.executable_rate(a, b) for b in range(n)] for a in range(n)],
                                   dtype=np.float64))
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
    log_in = np.array([np.log(snap.rates_bid[ref, j]) if j != ref else 0.0 for j in range(n)],
                      dtype=np.float32)
    log_out = np.array([np.log(snap.rates_bid[j, ref]) if j != ref else 0.0 for j in range(n)],
                       dtype=np.float32)
    node_attr = np.stack([log_in, log_out], axis=1)
    return edge_index, edge_attr, node_attr


def edge_index_from_snapshot(snap: FXSnapshot) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Convenience: edge_index plus the list of (i, j) pairs in the same order."""
    n = snap.n
    pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
    edge_index = np.array(pairs, dtype=np.int64).T
    return edge_index, pairs
