"""Phases 2-4: build training data, train the deep MLP, voting prediction.

Per the paper:
    * Topology (3, 20, 10, 8, 6, 5, 3): three input features (RSI value,
      RSI interval, trend direction), output is buy(1)/sell(2)/hold(0).
    * 200 epochs, batch size 128.
    * Phase 4 voting: for each test bar we run all 20 RSI intervals through
      the model and take the class with count > 14 (otherwise hold).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from ga_dmlp.ga import Chromosome


HOLD, BUY, SELL = 0, 1, 2


@dataclass
class MLPConfig:
    layers: tuple[int, ...] = (3, 20, 10, 8, 6, 5, 3)
    epochs: int = 200
    batch_size: int = 128
    lr: float = 1e-3
    seed: int = 1234
    device: str = "cpu"


class DeepMLP(nn.Module):
    def __init__(self, layers: tuple[int, ...]):
        super().__init__()
        mods: list[nn.Module] = []
        for i in range(len(layers) - 1):
            mods.append(nn.Linear(layers[i], layers[i + 1]))
            if i < len(layers) - 2:
                mods.append(nn.ReLU())
        self.net = nn.Sequential(*mods)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def build_training_data(
    chrom: Chromosome,
    rsi_columns: pd.DataFrame,
    trend: pd.Series,
    hold_rsi_low: float = 41.0,
    hold_rsi_high: float = 59.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Phase 2: assemble (features, labels) training set.

    Buy rows: every bar where the chromosome's buy condition fires (separately
    for downtrend and uptrend), labeled with the chromosome's buy threshold and
    interval.
    Sell rows: analogous.
    Hold rows: every bar at which RSI(14) sits in (hold_rsi_low, hold_rsi_high)
    is added with the actual RSI value and interval=14, label=HOLD. This
    matches the spirit of the "in-between values" hold rows in Table 1.
    """
    rows_x: list[list[float]] = []
    rows_y: list[int] = []

    rsi_buy_dn = rsi_columns[f"rsi_{chrom.int_buy_dn}"].to_numpy()
    rsi_sell_dn = rsi_columns[f"rsi_{chrom.int_sell_dn}"].to_numpy()
    rsi_buy_up = rsi_columns[f"rsi_{chrom.int_buy_up}"].to_numpy()
    rsi_sell_up = rsi_columns[f"rsi_{chrom.int_sell_up}"].to_numpy()
    trends = trend.to_numpy()

    # For buy/sell rows we store the actual RSI value at the bar where the
    # chromosome rule fired (paired with the chromosome's interval). This
    # preserves the smooth decision boundary the MLP needs to generalise to
    # new bars whose RSI values don't sit exactly at the chromosome's
    # threshold.
    for i in range(len(trends)):
        is_up = trends[i] > 0.5
        if is_up:
            if rsi_buy_up[i] < chrom.rsi_buy_up:
                rows_x.append([float(rsi_buy_up[i]), float(chrom.int_buy_up), 1.0])
                rows_y.append(BUY)
            if rsi_sell_up[i] > chrom.rsi_sell_up:
                rows_x.append([float(rsi_sell_up[i]), float(chrom.int_sell_up), 1.0])
                rows_y.append(SELL)
        else:
            if rsi_buy_dn[i] < chrom.rsi_buy_dn:
                rows_x.append([float(rsi_buy_dn[i]), float(chrom.int_buy_dn), 0.0])
                rows_y.append(BUY)
            if rsi_sell_dn[i] > chrom.rsi_sell_dn:
                rows_x.append([float(rsi_sell_dn[i]), float(chrom.int_sell_dn), 0.0])
                rows_y.append(SELL)

        # Hold rows: any RSI(14) value sitting in the in-between range, paired
        # with interval 14 and the actual trend.
        if "rsi_14" in rsi_columns.columns:
            v = rsi_columns["rsi_14"].iloc[i]
        else:
            v = rsi_columns.iloc[i, len(rsi_columns.columns) // 2]
        if hold_rsi_low <= v <= hold_rsi_high:
            rows_x.append([float(v), 14.0, float(trends[i])])
            rows_y.append(HOLD)

    if not rows_x:
        return np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.int64)
    return np.array(rows_x, dtype=np.float32), np.array(rows_y, dtype=np.int64)


def train_mlp(
    X: np.ndarray,
    y: np.ndarray,
    config: MLPConfig | None = None,
    verbose: bool = False,
) -> DeepMLP:
    cfg = config or MLPConfig()
    torch.manual_seed(cfg.seed)
    model = DeepMLP(cfg.layers).to(cfg.device)
    if X.shape[0] == 0:
        return model

    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr)
    loss_fn = nn.CrossEntropyLoss()

    Xt = torch.from_numpy(X).to(cfg.device)
    yt = torch.from_numpy(y).to(cfg.device)
    n = X.shape[0]

    for epoch in range(cfg.epochs):
        perm = torch.randperm(n)
        running = 0.0
        for s in range(0, n, cfg.batch_size):
            idx = perm[s : s + cfg.batch_size]
            xb = Xt[idx]; yb = yt[idx]
            logits = model(xb)
            loss = loss_fn(logits, yb)
            opt.zero_grad(); loss.backward(); opt.step()
            running += float(loss.item()) * xb.size(0)
        if verbose and (epoch % max(1, cfg.epochs // 10) == 0 or epoch == cfg.epochs - 1):
            print(f"epoch {epoch:3d}  loss={running / n:.4f}")
    return model


def predict_with_voting(
    model: DeepMLP,
    rsi_columns: pd.DataFrame,
    trend: pd.Series,
    intervals: range = range(1, 21),
    vote_threshold: int = 14,
    device: str = "cpu",
) -> np.ndarray:
    """Phase 4 voting: for each timestep, query the model on every RSI
    interval and return the class with count > vote_threshold (else HOLD).
    """
    model.eval()
    n = len(trend)
    counts = np.zeros((n, 3), dtype=np.int32)
    trend_arr = trend.to_numpy().astype(np.float32)

    with torch.no_grad():
        for p in intervals:
            col = rsi_columns[f"rsi_{p}"].to_numpy().astype(np.float32)
            X = np.stack([col, np.full(n, float(p), dtype=np.float32), trend_arr], axis=1)
            logits = model(torch.from_numpy(X).to(device))
            preds = logits.argmax(dim=1).cpu().numpy()
            for label_idx in (HOLD, BUY, SELL):
                counts[:, label_idx] += (preds == label_idx).astype(np.int32)

    final = np.full(n, HOLD, dtype=np.int64)
    for label_idx in (BUY, SELL):
        final[counts[:, label_idx] > vote_threshold] = label_idx
    return final
