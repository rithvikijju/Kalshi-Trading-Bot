"""Payoff and pricing functions for Neufeld-Sester 2024.

For an option of type i, payoff written on underlying value S_i is
Ψ_i(S_i, K_{i,j}). The paper's example (Section 3.1) uses vanilla
calls, where the underlying itself is treated as a call with strike 0.

The static strategy `(a, h+, h-)` payoff at maturity is

    I_S(K, a, h) = a + Σ_{i,j} (h+_{i,j} - h-_{i,j}) · Ψ_i(S_i, K_{i,j}),

and its price at inception (using bid for shorts you sold and ask for
longs you bought) is

    f(π, a, h) = a + Σ_{i,j} (h+_{i,j} · π+_{i,j} - h-_{i,j} · π-_{i,j}).

Both functions have a NumPy variant for offline LSIP work and a Torch
variant the NN trainer differentiates through.
"""

from __future__ import annotations

import numpy as np
import torch


def vanilla_call(S: np.ndarray, K: float | np.ndarray) -> np.ndarray:
    """Vanilla European call payoff max(S - K, 0).

    S can be a scalar terminal value or a vector/matrix; K is the strike
    or a vector of strikes broadcast against S along the last axis.
    """
    return np.maximum(S - K, 0.0)


def payoff_I_S(
    S: np.ndarray,        # [d] or [B, d]: terminal underlying values per stock
    strikes: np.ndarray,  # [N]: option strikes flattened across stocks
    asset_idx: np.ndarray,  # [N]: which underlying (in 0..d-1) each option is on
    a: float,
    h_long: np.ndarray,   # [N]
    h_short: np.ndarray,  # [N]
) -> np.ndarray:
    """Static-strategy payoff I_S(K, a, h)."""
    if S.ndim == 1:
        S = S[None, :]
    # Per-option underlying value, broadcast.
    Si = S[:, asset_idx]                          # [B, N]
    payoffs = np.maximum(Si - strikes[None, :], 0.0)
    return a + ((h_long - h_short)[None, :] * payoffs).sum(axis=-1)


def price_f(
    a: float,
    h_long: np.ndarray,
    h_short: np.ndarray,
    pi_ask: np.ndarray,    # [N]
    pi_bid: np.ndarray,    # [N]
) -> float:
    """Strategy price f(π, a, h). h_long pays ask; h_short receives bid."""
    return float(a + (h_long * pi_ask - h_short * pi_bid).sum())


# ---------------------- Torch versions -----------------------------


def _broadcast_S(S: torch.Tensor, asset_idx: torch.Tensor) -> torch.Tensor:
    if S.ndim == 1:
        S = S.unsqueeze(0)
    return S.index_select(dim=-1, index=asset_idx)


def payoff_I_S_torch(
    S: torch.Tensor,        # [SB, d]
    strikes: torch.Tensor,  # [B, N] or [N]
    asset_idx: torch.Tensor,  # [N]
    a: torch.Tensor,        # [B] or scalar
    h_long: torch.Tensor,   # [B, N]
    h_short: torch.Tensor,  # [B, N]
) -> torch.Tensor:
    """Vectorised payoff for a batch of markets and a shared S sample.

    Returns tensor of shape [B, SB] with the strategy's payoff under each
    sampled terminal value for each market in the batch.
    """
    if strikes.ndim == 1:
        strikes = strikes.unsqueeze(0)             # [1, N]
    # Per-option terminal underlying value: [SB, N]
    Si = S.index_select(dim=-1, index=asset_idx)
    # Strikes per (B, SB, N)
    payoffs = torch.clamp(Si.unsqueeze(0) - strikes.unsqueeze(1), min=0.0)
    coef = (h_long - h_short).unsqueeze(1)          # [B, 1, N]
    return a.unsqueeze(-1) + (coef * payoffs).sum(dim=-1)


def price_f_torch(
    a: torch.Tensor,          # [B]
    h_long: torch.Tensor,     # [B, N]
    h_short: torch.Tensor,    # [B, N]
    pi_ask: torch.Tensor,     # [B, N]
    pi_bid: torch.Tensor,     # [B, N]
) -> torch.Tensor:
    """Torch version of f. Returns [B]."""
    return a + (h_long * pi_ask - h_short * pi_bid).sum(dim=-1)
