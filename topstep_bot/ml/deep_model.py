"""
Deep setup-quality model (PyTorch): a 1D-CNN over the price-pattern sequence fused with
the engineered ICT features, predicting P(win).

Small and heavily regularized on purpose — the dataset is ~10^5 bars / ~10^3-10^4 setups,
which is modest for deep learning, so capacity is kept low (a few conv filters + a small
head), with dropout, weight decay, class-balanced loss, and early stopping on a validation
split. This is the honest way to use DL on limited data: regularize hard, validate OOS.

Used both for offline walk-forward evaluation and, if promoted, for live inference (the
live ConfidenceModel loads deep_model.pt and calls predict_setup with the bar window).
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn

from .dataset import N_CHAN, SEQ_LEN, _normalize_seq
from .features import FEATURE_NAMES, vector
from ..signals.structure import atr


def _device():
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class SetupNet(nn.Module):
    def __init__(self, n_chan=N_CHAN, n_feat=len(FEATURE_NAMES), dropout=0.3):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(n_chan, 32, kernel_size=5, padding=2), nn.ReLU(),
            nn.Conv1d(32, 32, kernel_size=3, padding=1), nn.ReLU(),
            nn.AdaptiveMaxPool1d(1))
        self.feat = nn.Sequential(nn.Linear(n_feat, 32), nn.ReLU(), nn.Dropout(dropout))
        self.head = nn.Sequential(
            nn.Linear(32 + 32, 48), nn.ReLU(), nn.Dropout(dropout), nn.Linear(48, 1))

    def forward(self, seq, feat):
        # seq: (B, L, C) -> (B, C, L) for Conv1d
        c = self.conv(seq.transpose(1, 2)).squeeze(-1)
        f = self.feat(feat)
        return self.head(torch.cat([c, f], dim=1)).squeeze(-1)


class DeepConfidence:
    def __init__(self, path: str | None = None, n_feat: int = len(FEATURE_NAMES),
                 n_chan: int = N_CHAN):
        self.device = _device()
        self.n_feat = n_feat
        self.n_chan = n_chan
        self.net: SetupNet | None = None
        if path:
            try:
                self.load(path)
            except Exception:
                self.net = None

    # ---- training ----
    def fit(self, X_seq, X_feat, y, epochs=60, lr=1e-3, weight_decay=1e-4,
            batch=256, val_frac=0.15, patience=8, verbose=False) -> dict:
        n = len(y)
        cut = int(n * (1 - val_frac))               # temporal val split (last slice)
        dev = self.device
        Xs = torch.tensor(X_seq, dtype=torch.float32)
        Xf = torch.tensor(X_feat, dtype=torch.float32)
        yt = torch.tensor(y, dtype=torch.float32)
        tr = (Xs[:cut], Xf[:cut], yt[:cut])
        va = (Xs[cut:].to(dev), Xf[cut:].to(dev), yt[cut:].to(dev))

        pos = max(1, int(yt[:cut].sum()))
        neg = max(1, cut - pos)
        pos_weight = torch.tensor([neg / pos], device=dev)
        self.net = SetupNet(n_chan=self.n_chan, n_feat=self.n_feat).to(dev)
        opt = torch.optim.Adam(self.net.parameters(), lr=lr, weight_decay=weight_decay)
        loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        best_val, best_state, bad = 1e9, None, 0
        for ep in range(epochs):
            self.net.train()
            perm = torch.randperm(cut)
            for i in range(0, cut, batch):
                idx = perm[i:i + batch]
                xb, fb, yb = tr[0][idx].to(dev), tr[1][idx].to(dev), tr[2][idx].to(dev)
                opt.zero_grad()
                loss = loss_fn(self.net(xb, fb), yb)
                loss.backward()
                opt.step()
            self.net.eval()
            with torch.no_grad():
                vloss = loss_fn(self.net(va[0], va[1]), va[2]).item() if cut < n else 0.0
            if vloss < best_val - 1e-4:
                best_val, best_state, bad = vloss, {k: v.detach().cpu().clone()
                                                    for k, v in self.net.state_dict().items()}, 0
            else:
                bad += 1
                if bad >= patience:
                    break
            if verbose:
                print(f"  epoch {ep:02d} val_loss {vloss:.4f}")
        if best_state:
            self.net.load_state_dict(best_state)
        return dict(epochs_ran=ep + 1, best_val_loss=best_val, n_train=cut, n_val=n - cut)

    # ---- inference ----
    @torch.no_grad()
    def predict_proba(self, X_seq, X_feat) -> np.ndarray:
        if self.net is None:
            return np.full(len(X_seq), 0.5)
        self.net.eval()
        xs = torch.tensor(X_seq, dtype=torch.float32, device=self.device)
        xf = torch.tensor(X_feat, dtype=torch.float32, device=self.device)
        return torch.sigmoid(self.net(xs, xf)).cpu().numpy()

    def predict_setup(self, setup, bars) -> float | None:
        """Live inference from a setup + its bar window. Returns None if not enough bars."""
        if self.net is None or len(bars) < SEQ_LEN:
            return None
        a = atr(bars[-WINDOW_FALLBACK:] if len(bars) >= WINDOW_FALLBACK else bars, 14)
        seq = _normalize_seq(bars[-SEQ_LEN:], setup.entry, a)
        feat = vector(setup)
        return float(self.predict_proba(seq[None, :, :], feat[None, :])[0])

    # ---- persistence ----
    def save(self, path: str):
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.net.state_dict(), path)

    def load(self, path: str):
        self.net = SetupNet(n_chan=self.n_chan, n_feat=self.n_feat).to(self.device)
        self.net.load_state_dict(torch.load(path, map_location=self.device))
        self.net.eval()


WINDOW_FALLBACK = 150
