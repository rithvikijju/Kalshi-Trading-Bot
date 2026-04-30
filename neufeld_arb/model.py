"""StaticArbDetector: deep MLP per Algorithm 1 of Neufeld-Sester 2024.

Maps `(K, π) ∈ R^{3N}` to a static strategy `(a, h+, h-) ∈ R^{1+2N}`.
The output layer is bounded so all proposals respect:

    a ∈ [a_min, ∞)        (paper uses a_min = -1)
    h+, h- ∈ [0, H]       (paper uses H = 1)

The unbounded `a` is handled by `a_min + softplus(·)` so the output is
strictly above `a_min`; `h+/h-` use `H * sigmoid(·)`.

Architecture (Section 3.1.1): 5 hidden layers, 1024 neurons each, ReLU.
"""

from __future__ import annotations

import torch
from torch import nn


class StaticArbDetector(nn.Module):
    def __init__(
        self,
        N: int,
        hidden_dim: int = 1024,
        num_hidden: int = 5,
        a_min: float = -1.0,
        H: float = 1.0,
    ):
        super().__init__()
        self.N = N
        self.a_min = float(a_min)
        self.H = float(H)
        self.input_dim = 3 * N
        self.output_dim = 1 + 2 * N

        layers: list[nn.Module] = []
        in_dim = self.input_dim
        for _ in range(num_hidden):
            layers.append(nn.Linear(in_dim, hidden_dim))
            layers.append(nn.ReLU())
            in_dim = hidden_dim
        layers.append(nn.Linear(in_dim, self.output_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """x: [B, 3N]. Returns (a [B], h_long [B, N], h_short [B, N])."""
        out = self.net(x)
        raw_a, raw_long, raw_short = out[:, :1], out[:, 1:1 + self.N], out[:, 1 + self.N:]
        a = self.a_min + torch.nn.functional.softplus(raw_a).squeeze(-1)
        h_long = self.H * torch.sigmoid(raw_long)
        h_short = self.H * torch.sigmoid(raw_short)
        return a, h_long, h_short

    @torch.no_grad()
    def propose(self, x_np) -> tuple[float, "np.ndarray", "np.ndarray"]:
        import numpy as np
        x = torch.from_numpy(x_np.astype("float32")).unsqueeze(0).to(next(self.parameters()).device)
        a, hl, hs = self.forward(x)
        return float(a.item()), hl.squeeze(0).cpu().numpy(), hs.squeeze(0).cpu().numpy()
