"""
Fast setup-quality / confidence model — the "AI" in position sizing.

Output: a probability-of-win in [0,1] for a Setup, computed in microseconds. The risk
engine sets the HARD contract cap; this score only scales size DOWN within that cap
(size = round(cap * scale(confidence))). It can never increase risk past the deterministic
ceiling — that's the safety contract.

Two regimes:
  * COLD START (no/insufficient training data): a transparent, hand-weighted logistic
    PRIOR over the strongest features (confirmation, confluence, killzone, R:R). This is
    an honest prior, NOT discovered alpha — it just keeps sizing sane while we capture a
    tape. Until validated, treat every "confidence" as a prior.
  * TRAINED: once enough labelled outcomes are captured (win/loss per setup), we fit a
    scikit-learn classifier (logistic regression, calibrated) on the canonical feature
    vector and persist it. Inference stays sub-ms.
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path

import numpy as np

from .features import FEATURE_NAMES, vector

MIN_TRAIN_SAMPLES = 200            # don't trust a fit below this; stay on the prior
DEEP_PATH = "topstep_bot/ml/deep_model.pt"   # promoted deep model (if it exists)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


# Hand-set prior weights (log-odds contributions). Deliberately conservative.
# Index by FEATURE_NAMES; unlisted features get 0 weight in the prior.
_PRIOR_BIAS = -0.4
_PRIOR_W = {
    "confirm": 0.9,            # CHoCH/displacement confirmation matters most
    "entry_in_fvg": 0.5,
    "entry_in_ob": 0.5,
    "sweep_rej_atr": 0.6,      # cleaner stop-grab rejection → better reversal
    "in_killzone": 0.7,        # time-of-day is core ICT
    "bos": 0.4,
    "rr": 0.25,                # higher planned R:R, modest bump
    "disp_mag": 0.15,
    "n_active_fvg": -0.05,     # too many open gaps = messy tape
}


class ConfidenceModel:
    def __init__(self, path: str | None = None):
        self.path = path
        self.clf = None            # sklearn estimator once trained
        self.scaler_mean = None
        self.scaler_std = None
        self.trained = False
        self.n_train = 0
        self.deep = None               # lazy: None=untried, False=unavailable, else DeepConfidence
        if path and Path(path).exists():
            self.load(path)

    # ---- deep model (preferred when promoted) ----
    def _deep_ok(self) -> bool:
        if self.deep is False:
            return False
        if self.deep is None:
            if not Path(DEEP_PATH).exists():
                self.deep = False
                return False
            try:
                from .deep_model import DeepConfidence
                d = DeepConfidence(DEEP_PATH)
                self.deep = d if d.net is not None else False
            except Exception:
                self.deep = False
        return self.deep is not False

    def mode(self) -> str:
        if self._deep_ok():
            return "DEEP"
        return "LOGISTIC" if self.trained else "PRIOR"

    # ---- prediction ----
    def predict(self, setup, bars=None) -> float:
        # prefer the promoted deep model when we have the bar window to feed it
        if bars is not None and self._deep_ok():
            p = self.deep.predict_setup(setup, bars)
            if p is not None:
                return p
        x = vector(setup)
        if self.trained and self.clf is not None:
            xs = (x - self.scaler_mean) / self.scaler_std
            try:
                return float(self.clf.predict_proba(xs.reshape(1, -1))[0, 1])
            except Exception:
                pass  # fall through to prior on any inference hiccup
        return self._prior(x)

    def _prior(self, x: np.ndarray) -> float:
        idx = {n: i for i, n in enumerate(FEATURE_NAMES)}
        z = _PRIOR_BIAS
        for name, w in _PRIOR_W.items():
            z += w * x[idx[name]]
        return _sigmoid(z)

    # ---- sizing helper ----
    @staticmethod
    def scale(confidence: float, floor: float = 0.0, lo: float = 0.55, hi: float = 0.80) -> float:
        """Map confidence → fraction of the risk cap to use. Below `lo` → 0 (skip);
        ramps linearly to 1.0 at `hi` and above. Keeps sizing monotone and bounded."""
        if confidence < lo:
            return floor
        if confidence >= hi:
            return 1.0
        return (confidence - lo) / (hi - lo)

    # ---- training ----
    def train(self, X: np.ndarray, y: np.ndarray) -> bool:
        """Fit on captured (features, win) pairs. Returns True if a model was trained."""
        if len(y) < MIN_TRAIN_SAMPLES or len(set(y.tolist())) < 2:
            return False
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            return False
        self.scaler_mean = X.mean(axis=0)
        self.scaler_std = X.std(axis=0)
        self.scaler_std[self.scaler_std == 0] = 1.0
        Xs = (X - self.scaler_mean) / self.scaler_std
        clf = LogisticRegression(max_iter=1000, C=0.5, class_weight="balanced")
        clf.fit(Xs, y)
        self.clf = clf
        self.trained = True
        self.n_train = len(y)
        if self.path:
            self.save(self.path)
        return True

    # ---- persistence ----
    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(dict(clf=self.clf, mean=self.scaler_mean, std=self.scaler_std,
                             n_train=self.n_train), f)

    def load(self, path: str):
        with open(path, "rb") as f:
            d = pickle.load(f)
        self.clf = d["clf"]
        self.scaler_mean = d["mean"]
        self.scaler_std = d["std"]
        self.n_train = d.get("n_train", 0)
        self.trained = self.clf is not None
