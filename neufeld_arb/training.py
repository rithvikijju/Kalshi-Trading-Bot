"""Algorithm 1 trainer for the static-arbitrage detector.

For each iteration:

    1. Sample a batch of B markets `(K_b, π_b)` from a pool of bundles.
    2. Pre-compute the LSIP target Y_b = V(K_b, π_b) and the binary
       indicator Y~_b = -1 if Y_b < 0 else 0.
    3. Sample SB scenarios `S_{b,j}` from the prediction set [s_lo, s_hi]^d.
    4. Forward the NN, compute:

         loss_b = f(π_b, NN_a, NN_h)
                + γ · (1/SB) · Σ_j max(0, -I_S(NN))^2
                + γ · max(0, -(Y~_b + 0.5) · f(π_b, NN_a, NN_h))

    5. Average over the batch, step Adam.

`γ` ramps from γ_min at iteration 0 to γ_max by `iters` (paper's curriculum).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import torch
from torch import nn

from neufeld_arb.lsip import lsip_value
from neufeld_arb.market import Market, OptionsBundle, sample_market
from neufeld_arb.model import StaticArbDetector
from neufeld_arb.payoff import payoff_I_S_torch, price_f_torch


@dataclass
class TrainConfig:
    iters: int = 20_000
    batch_size: int = 512        # paper: B = 512
    s_batch_size: int = 32       # paper: SB = 32
    lr: float = 1e-4
    gamma_min: float = 1.0
    gamma_max: float = 10_000.0
    a_min: float = -1.0
    H: float = 1.0
    s_lo: float = 0.0            # paper: prediction set [0, 2]^d
    s_hi: float = 2.0
    d: int = 5
    n_per_asset: int = 11
    lsip_grid_size: int = 256
    seed: int = 42
    device: str = "cpu"
    log_every: int = 100
    # Pre-compute LSIP targets for a fixed pool of markets up front (much
    # cheaper than re-sampling every iter and re-solving).
    pretrained_pool_size: int = 5_000


@dataclass
class _Sample:
    market: Market
    Y_target: float
    Y_tilde: float


def _build_sample_pool(
    bundles: Sequence[OptionsBundle],
    cfg: TrainConfig,
    rng: np.random.Generator,
) -> list[_Sample]:
    """Materialise `cfg.pretrained_pool_size` markets and their LSIP targets.

    This mirrors paper Section 3.1.1's offline construction of 50 000
    samples: each sample mixes 5 random S&P constituents into one market.
    """
    pool: list[_Sample] = []
    fail = 0
    for i in range(cfg.pretrained_pool_size):
        m = sample_market(bundles, d=cfg.d, n_per_asset=cfg.n_per_asset, rng=rng)
        res = lsip_value(
            m,
            grid_size=cfg.lsip_grid_size,
            a_min=cfg.a_min,
            H=cfg.H,
            s_lo=cfg.s_lo,
            s_hi=cfg.s_hi,
            rng=rng,
        )
        if not res.success:
            fail += 1
            continue
        Y_tilde = -1.0 if res.value < 0 else 0.0
        pool.append(_Sample(market=m, Y_target=res.value, Y_tilde=Y_tilde))
        if (i + 1) % max(1, cfg.pretrained_pool_size // 20) == 0:
            n_arb = sum(1 for s in pool if s.Y_target < 0)
            print(f"[train.pool] {i+1}/{cfg.pretrained_pool_size}  arb so far: {n_arb}  fail: {fail}")
    print(f"[train.pool] built {len(pool)} samples; arb rate {sum(1 for s in pool if s.Y_target < 0)/max(len(pool),1):.2%}")
    return pool


def _gamma(cfg: TrainConfig, step: int) -> float:
    if cfg.iters <= 1:
        return cfg.gamma_max
    t = step / max(cfg.iters - 1, 1)
    # Multiplicative ramp from gamma_min to gamma_max (matches paper's
    # γ-schedule that scales with iteration count).
    return float(cfg.gamma_min * (cfg.gamma_max / cfg.gamma_min) ** t)


def train_detector(
    bundles: Sequence[OptionsBundle],
    cfg: TrainConfig,
    val_pool: list[_Sample] | None = None,
) -> tuple[StaticArbDetector, dict]:
    """Train one StaticArbDetector per Algorithm 1.

    Returns (model, history) where history has 'loss', 'price_mean',
    'feasibility_mean', 'sign_acc' arrays sampled every cfg.log_every steps.
    """
    rng = np.random.default_rng(cfg.seed)
    torch.manual_seed(cfg.seed)

    pool = _build_sample_pool(bundles, cfg, rng)
    if not pool:
        raise RuntimeError("LSIP pool is empty; cannot train.")

    # Resolve N from the first sample (all samples share the same N by construction).
    N = pool[0].market.N
    d = pool[0].market.d
    model = StaticArbDetector(
        N=N, num_hidden=5, hidden_dim=1024, a_min=cfg.a_min, H=cfg.H,
    ).to(cfg.device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr)

    # Pre-stack pool tensors once to avoid Python-side per-iter conversion.
    feat = torch.tensor(np.stack([s.market.feature_vector() for s in pool]),
                        dtype=torch.float32, device=cfg.device)            # [P, 3N]
    K_pool = torch.tensor(np.stack([s.market.K for s in pool]),
                          dtype=torch.float32, device=cfg.device)          # [P, N]
    pi_ask = torch.tensor(np.stack([s.market.pi_ask for s in pool]),
                          dtype=torch.float32, device=cfg.device)
    pi_bid = torch.tensor(np.stack([s.market.pi_bid for s in pool]),
                          dtype=torch.float32, device=cfg.device)
    asset_idx = torch.tensor(pool[0].market.asset_idx,
                             dtype=torch.long, device=cfg.device)          # [N], shared
    Y_tilde = torch.tensor(np.array([s.Y_tilde for s in pool]),
                           dtype=torch.float32, device=cfg.device)         # [P]

    history = {"loss": [], "price_mean": [], "feasibility_mean": [],
               "sign_acc": [], "step": []}

    P = len(pool)
    for step in range(cfg.iters):
        idx = torch.from_numpy(rng.integers(0, P, size=cfg.batch_size).astype(np.int64)).to(cfg.device)
        x = feat.index_select(0, idx)
        K_b = K_pool.index_select(0, idx)
        ask_b = pi_ask.index_select(0, idx)
        bid_b = pi_bid.index_select(0, idx)
        y_b = Y_tilde.index_select(0, idx)

        a, hl, hs = model(x)
        f_pred = price_f_torch(a, hl, hs, ask_b, bid_b)              # [B]

        # Sample fresh S grid per step.
        S_np = rng.uniform(cfg.s_lo, cfg.s_hi, size=(cfg.s_batch_size, d)).astype(np.float32)
        S = torch.from_numpy(S_np).to(cfg.device)
        # NB: payoff_I_S_torch needs strikes per-batch.
        I = payoff_I_S_torch(S, K_b, asset_idx, a, hl, hs)            # [B, SB]
        feasibility = torch.relu(-I).pow(2).mean(dim=1)               # [B]

        # Sign-correctness penalty.
        sign_term = torch.relu(-(y_b + 0.5) * f_pred)                 # [B]

        gamma = _gamma(cfg, step)
        loss_per = f_pred + gamma * feasibility + gamma * sign_term
        loss = loss_per.mean()

        opt.zero_grad()
        loss.backward()
        opt.step()

        if step % cfg.log_every == 0 or step == cfg.iters - 1:
            with torch.no_grad():
                # Sign accuracy: predicted sign of f vs Y_tilde.
                pred_sign = (f_pred < 0).float() * -1.0  # -1 if f<0 else 0
                sign_acc = (pred_sign == y_b).float().mean().item()
            history["step"].append(step)
            history["loss"].append(float(loss.item()))
            history["price_mean"].append(float(f_pred.mean().item()))
            history["feasibility_mean"].append(float(feasibility.mean().item()))
            history["sign_acc"].append(sign_acc)
            print(
                f"[train] step {step:6d}  loss={loss.item():.4f}  "
                f"price_mean={f_pred.mean().item():+.4f}  feas={feasibility.mean().item():.4f}  "
                f"sign_acc={sign_acc:.3f}  γ={gamma:.1f}"
            )

    return model, history
