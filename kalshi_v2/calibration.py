"""Platt-scaling calibration of fair-value P(YES) against settled trade outcomes.

Fits a sigmoid that maps raw model P → calibrated P:
    p_cal = 1 / (1 + exp(-(coef * p + intercept)))

This corrects the ~6c systematic overconfidence we measured in live trade
audits — the empirical bank predicts more confidently than reality bears
out. The original kalshi-bot.ipynb had Platt scaling; v2 dropped it.

Fit is single-file (JSON on disk at ~/.btc_kalshi_bot/v2_calibrator.json)
so it survives kernel restarts. Call `fit_calibrator()` periodically as
more trades settle.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CALIBRATOR_PATH = Path("~/.btc_kalshi_bot/v2_calibrator.json").expanduser()

_CALIBRATOR: Optional[dict] = None
_LOADED_FROM_DISK = False


def _load_from_disk() -> Optional[dict]:
    """Read calibrator from disk into the module-level cache."""
    global _CALIBRATOR, _LOADED_FROM_DISK
    _LOADED_FROM_DISK = True
    try:
        if CALIBRATOR_PATH.exists():
            _CALIBRATOR = json.loads(CALIBRATOR_PATH.read_text())
        else:
            _CALIBRATOR = None
    except Exception:
        _CALIBRATOR = None
    return _CALIBRATOR


def get_calibrator() -> Optional[dict]:
    """Return current calibrator dict (coef, intercept, n, ...) or None."""
    if not _LOADED_FROM_DISK:
        _load_from_disk()
    return _CALIBRATOR


def apply(p: float) -> float:
    """Apply Platt sigmoid:  p_cal = 1 / (1 + exp(-(coef * p + intercept)))."""
    cal = get_calibrator()
    if cal is None:
        return p
    coef = float(cal.get("coef", 0.0))
    intercept = float(cal.get("intercept", 0.0))
    z = coef * p + intercept
    if z > 50:    return 1.0
    if z < -50:   return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def fit_calibrator(min_trades: int = 30, save: bool = True,
                     include_paper: bool = True) -> dict:
    """Fit Platt sigmoid from settled trades in the DB.

    Reads (model_p_yes, did_yes_happen) pairs from settled trades and fits
    logistic regression. Saves the result to disk so future fair_value
    calls pick it up automatically.

    Args:
      min_trades: minimum number of settled trades required to fit.
      include_paper: True to include paper-mode trades. Recommend True
        until you have enough live trades to fit on live-only.

    Returns the fit dict with coef / intercept / Brier scores.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from .paper_db import _conn

    conn = _conn()
    rows = conn.execute(
        "SELECT model_p_yes, side, settle_price, trade_type "
        "FROM trades WHERE settled = 1 "
        "AND model_p_yes IS NOT NULL AND settle_price IS NOT NULL"
    ).fetchall()
    conn.close()

    pairs = []
    for row in rows:
        p, side, settle, tag = row
        if p is None or settle is None or side not in ("yes", "no"):
            continue
        if not include_paper and tag == "v2":
            continue
        # settle_price is from the bot's-side perspective:
        #   YES trade win  → settle 1.00 → YES happened
        #   YES trade loss → settle 0.00 → YES did not happen
        #   NO  trade win  → settle 1.00 → YES did NOT happen
        #   NO  trade loss → settle 0.00 → YES happened
        if side == "yes":
            outcome = 1 if settle >= 0.5 else 0
        else:
            outcome = 0 if settle >= 0.5 else 1
        # Sanity: keep p strictly inside (0,1) to avoid numerical edge cases.
        p = max(0.001, min(0.999, float(p)))
        pairs.append((p, outcome))

    if len(pairs) < min_trades:
        raise ValueError(
            f"need >= {min_trades} settled trades to fit, have {len(pairs)}")

    X = np.array([[p] for p, _ in pairs])
    y = np.array([o for _, o in pairs])

    if y.sum() == 0 or y.sum() == len(y):
        raise ValueError("all outcomes are the same class; can't fit Platt")

    # C=1e3 → near-zero regularization. We want the raw MLE coefficients.
    clf = LogisticRegression(C=1e3, max_iter=1000, solver="lbfgs")
    clf.fit(X, y)
    coef = float(clf.coef_[0][0])
    intercept = float(clf.intercept_[0])

    # Brier scores (before/after) as a sanity diagnostic.
    raw_p = X.flatten()
    cal_p = 1.0 / (1.0 + np.exp(-(coef * raw_p + intercept)))
    raw_brier = float(np.mean((raw_p - y) ** 2))
    cal_brier = float(np.mean((cal_p - y) ** 2))
    raw_mean = float(raw_p.mean())
    realized_yes = float(y.mean())

    result = {
        "coef":           coef,
        "intercept":      intercept,
        "n":              len(pairs),
        "raw_brier":      raw_brier,
        "cal_brier":      cal_brier,
        "raw_mean_p":     raw_mean,
        "realized_yes_rate": realized_yes,
        "calibration_gap_pp": (raw_mean - realized_yes) * 100,
        "fitted_at":      datetime.now(timezone.utc).isoformat(),
    }

    if save:
        CALIBRATOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATOR_PATH.write_text(json.dumps(result, indent=2))

    # Refresh in-memory cache.
    global _CALIBRATOR, _LOADED_FROM_DISK
    _CALIBRATOR = result
    _LOADED_FROM_DISK = True
    return result


def reset_calibrator():
    """Clear the in-memory and on-disk calibrator. Subsequent fair_value
    calls will use raw probabilities until fit_calibrator() runs again."""
    global _CALIBRATOR, _LOADED_FROM_DISK
    _CALIBRATOR = None
    _LOADED_FROM_DISK = True
    try:
        if CALIBRATOR_PATH.exists():
            CALIBRATOR_PATH.unlink()
    except Exception:
        pass


def summary() -> str:
    """Human-readable status of the loaded calibrator."""
    cal = get_calibrator()
    if cal is None:
        return "no calibrator loaded (raw model probabilities in use)"
    lines = [
        f"Platt calibrator (fitted {cal.get('fitted_at', '?')})",
        f"  coef         = {cal['coef']:+.4f}",
        f"  intercept    = {cal['intercept']:+.4f}",
        f"  n trades     = {cal['n']}",
        f"  raw Brier    = {cal.get('raw_brier', float('nan')):.4f}",
        f"  cal Brier    = {cal.get('cal_brier', float('nan')):.4f}",
        f"  raw mean P   = {cal.get('raw_mean_p', float('nan')):.4f}",
        f"  realized YES rate = {cal.get('realized_yes_rate', float('nan')):.4f}",
        f"  calibration gap   = {cal.get('calibration_gap_pp', float('nan')):+.2f} pp",
    ]
    return "\n".join(lines)
