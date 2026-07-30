"""
Canonical feature vector for the confidence model.

We fix the ordering here so training data captured today stays compatible with a model
trained next month. Every feature is derived from a Setup's `features` dict (populated by
the signal engine); missing keys default to 0.0.
"""
from __future__ import annotations

import numpy as np

FEATURE_NAMES = [
    "archetype_reversal", "archetype_continuation",
    "trend_up", "trend_down", "bos", "choch",
    "disp_mag", "disp_dir",
    "sweep_rej_atr", "confirm",
    "entry_in_fvg", "entry_in_ob",
    "n_active_fvg", "n_active_ob",
    "body_atr", "range_atr",
    "dist_stop_atr", "dist_target_atr", "rr",
    "in_killzone", "kz_london", "kz_ny_am", "kz_ny_pm",
    "hour_et",
]


def vector(setup) -> np.ndarray:
    f = setup.features
    return np.array([float(f.get(name, 0.0)) for name in FEATURE_NAMES], dtype=float)


def matrix(setups) -> np.ndarray:
    return np.vstack([vector(s) for s in setups]) if setups else np.empty((0, len(FEATURE_NAMES)))
