"""Edge-classifying GNN for triangular arbitrage detection.

Architecture: a small message-passing network where node embeddings are
updated by aggregating messages from incoming edges (concat of source-node
embedding and edge features). After K rounds, an edge classifier scores each
directed edge from the concatenated embeddings of its endpoints plus the edge
features. The output is a per-edge probability that the edge participates in
a profitable cycle in the current snapshot.

This is implemented in plain PyTorch (no torch_geometric dependency) using
scatter-add via index_add_ for message aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn


class MessagePassingLayer(nn.Module):
    """Mean-aggregating MP layer with LayerNorm.

    Node features start identical (the graph is a complete digraph with
    anonymous nodes), so the only signal is in edge features. We use mean
    aggregation to be robust to node degree, and LayerNorm after the update
    so that the constant component of h does not dominate the per-node
    variation that actually carries the cycle signal.
    """

    def __init__(self, node_dim: int, edge_dim: int, hidden_dim: int):
        super().__init__()
        self.msg = nn.Sequential(
            nn.Linear(node_dim + edge_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.update = nn.Sequential(
            nn.Linear(node_dim + hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, node_dim),
        )
        self.norm = nn.LayerNorm(node_dim)

    def forward(
        self,
        x: torch.Tensor,        # [N, node_dim]
        edge_index: torch.Tensor,  # [2, E] long
        edge_attr: torch.Tensor,   # [E, edge_dim]
    ) -> torch.Tensor:
        src, dst = edge_index[0], edge_index[1]
        msg_input = torch.cat([x[src], edge_attr], dim=-1)
        m = self.msg(msg_input)  # [E, H]

        sum_agg = torch.zeros(x.size(0), m.size(1), device=x.device, dtype=m.dtype)
        sum_agg.index_add_(0, dst, m)
        # Per-node count of incoming edges (== N-1 for a complete digraph,
        # but written generically).
        ones = torch.ones(m.size(0), device=x.device, dtype=m.dtype)
        cnt = torch.zeros(x.size(0), device=x.device, dtype=m.dtype)
        cnt.index_add_(0, dst, ones)
        agg = sum_agg / cnt.clamp_min(1.0).unsqueeze(-1)

        out = self.update(torch.cat([x, agg], dim=-1))
        return self.norm(out)


class EdgeArbitrageGNN(nn.Module):
    """Edge-classifier that combines a message-passing GNN trunk with a
    raw-edge-feature skip connection.

    The skip path makes optimisation robust: even before the GNN learns
    useful node embeddings, the head can rank edges by their raw
    cycle-aware features, escaping the optimal-constant trap that hurts
    pure-GNN heads on imbalanced edge-classification tasks.
    """

    def __init__(
        self,
        node_in_dim: int = 2,
        edge_in_dim: int = 4,
        hidden_dim: int = 32,
        num_layers: int = 3,
    ):
        super().__init__()
        self.node_embed = nn.Linear(node_in_dim, hidden_dim)
        self.edge_embed = nn.Linear(edge_in_dim, hidden_dim)
        self.layers = nn.ModuleList(
            [MessagePassingLayer(hidden_dim, hidden_dim, hidden_dim) for _ in range(num_layers)]
        )
        head_in = 2 * hidden_dim + hidden_dim + edge_in_dim
        self.edge_head = nn.Sequential(
            nn.Linear(head_in, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> torch.Tensor:
        h = self.node_embed(x)
        e = self.edge_embed(edge_attr)
        for layer in self.layers:
            h = layer(h, edge_index, e)
        src, dst = edge_index[0], edge_index[1]
        edge_repr = torch.cat([h[src], h[dst], e, edge_attr], dim=-1)
        logits = self.edge_head(edge_repr).squeeze(-1)
        return logits


@dataclass
class TrainingSample:
    x: np.ndarray
    edge_index: np.ndarray
    edge_attr: np.ndarray
    edge_label: np.ndarray


def train_gnn(
    samples: list[TrainingSample],
    val_samples: list[TrainingSample] | None = None,
    epochs: int = 30,
    lr: float = 1e-3,
    hidden_dim: int = 32,
    num_layers: int = 3,
    pos_weight: float | None = None,
    device: str = "cpu",
    verbose: bool = True,
) -> EdgeArbitrageGNN:
    """Train an EdgeArbitrageGNN on per-edge arbitrage labels.

    Class imbalance is severe (most edges are not on a profitable cycle), so
    the BCE loss uses pos_weight; when not provided we estimate it from the
    training labels.
    """
    if not samples:
        raise ValueError("No training samples provided.")

    node_in = samples[0].x.shape[1]
    edge_in = samples[0].edge_attr.shape[1]
    model = EdgeArbitrageGNN(node_in, edge_in, hidden_dim, num_layers).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    # An aggressive pos_weight tends to push the model into the optimal-
    # constant trap on imbalanced edge classification. We default to a mild
    # square-root rebalance, which keeps the positive class influential
    # without dominating the loss landscape.
    if pos_weight is None:
        total = sum(s.edge_label.size for s in samples)
        pos = sum(float(s.edge_label.sum()) for s in samples)
        neg = total - pos
        pos_weight = float(np.sqrt(max(1.0, neg / max(pos, 1.0))))
    pw = torch.tensor([pos_weight], dtype=torch.float32, device=device)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pw)

    for epoch in range(epochs):
        model.train()
        running = 0.0
        for s in samples:
            x = torch.from_numpy(s.x).to(device)
            ei = torch.from_numpy(s.edge_index).to(device)
            ea = torch.from_numpy(s.edge_attr).to(device)
            y = torch.from_numpy(s.edge_label).to(device)
            logits = model(x, ei, ea)
            loss = loss_fn(logits, y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            running += float(loss.item())
        if verbose and (epoch % max(1, epochs // 10) == 0 or epoch == epochs - 1):
            msg = f"epoch {epoch:3d}  train_loss={running / len(samples):.4f}"
            if val_samples:
                msg += f"  val_auc={_eval_auc(model, val_samples, device):.3f}"
            print(msg)
    return model


def _eval_auc(model: EdgeArbitrageGNN, samples: list[TrainingSample], device: str) -> float:
    model.eval()
    ys, ps = [], []
    with torch.no_grad():
        for s in samples:
            x = torch.from_numpy(s.x).to(device)
            ei = torch.from_numpy(s.edge_index).to(device)
            ea = torch.from_numpy(s.edge_attr).to(device)
            logits = model(x, ei, ea).cpu().numpy()
            ys.append(s.edge_label)
            ps.append(logits)
    y = np.concatenate(ys)
    p = np.concatenate(ps)
    return _auc(y, p)


def _auc(y: np.ndarray, score: np.ndarray) -> float:
    """Mann-Whitney U AUC with tie-correct (average) ranks."""
    pos = score[y > 0.5]
    neg = score[y <= 0.5]
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    combined = np.concatenate([pos, neg])
    order = np.argsort(combined, kind="stable")
    sorted_vals = combined[order]
    n = combined.size
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i + 1
        while j < n and sorted_vals[j] == sorted_vals[i]:
            j += 1
        avg = (i + j + 1) / 2.0
        ranks[order[i:j]] = avg
        i = j
    pos_ranks = ranks[: pos.size].sum()
    return float((pos_ranks - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def predict_edge_scores(
    model: EdgeArbitrageGNN,
    x: np.ndarray,
    edge_index: np.ndarray,
    edge_attr: np.ndarray,
    device: str = "cpu",
) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        logits = model(
            torch.from_numpy(x).to(device),
            torch.from_numpy(edge_index).to(device),
            torch.from_numpy(edge_attr).to(device),
        )
        return torch.sigmoid(logits).cpu().numpy()
