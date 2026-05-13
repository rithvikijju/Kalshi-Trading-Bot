"""Fair value model.

Two complementary fair-value estimators for Kalshi BTC strikes:

  1. Empirical sample bank (drift-removed). Bootstrapped from real BTC
     minute returns over the configured horizon, vol+kurt joint matching.
     This is the model that achieved 70% hit-rate on the original
     kalshi-bot.ipynb settled paper trades.

  2. Lognormal Φ((ln(s/K) + (μ - 0.5σ²)T) / (σ√T)).  Falls back to
     this when the empirical bank lacks coverage.

Both expose the same interface: fair_p_yes(spot, strike, ttl_min, ...) → P.
For bucket markets (B-prefix tickers), use fair_p_in_bucket.
"""
from __future__ import annotations
import numpy as np
from scipy.stats import norm
from typing import Dict, Optional


# ════════════════════════════════════════════════════════════════════════
#  Empirical sample bank (drift-removed, vol+kurt matched)
# ════════════════════════════════════════════════════════════════════════
def build_empirical_bank(btc_1m, horizon_min: int, max_idx: Optional[int] = None,
                          n_samples: int = 5000, seed: int = 0,
                          demean: bool = True) -> dict:
    """Build a sample bank of historical log-returns over `horizon_min`.

    Each sample also stores the starting vol (rv_60m) and kurtosis (rkurt_60m)
    so we can do joint nearest-neighbour matching at inference time.

    Args:
        btc_1m: DataFrame with columns close, log_ret, rv_60m, rkurt_60m.
                rkurt_60m is computed below if absent.
        horizon_min: forward window length in minutes
        max_idx: only sample from rows whose forward window ends ≤ max_idx
                 (set to len(btc_1m) for live; lower for walk-forward)
        n_samples: target sub-sample size
        demean: subtract the sample mean (drift removal). Default True.
                Drift bleed-through caused the v2 model to be NO-biased
                during the up-trend regime; demean=True fixes that.
    """
    if max_idx is None:
        max_idx = len(btc_1m)
    rng = np.random.default_rng(seed)
    close = btc_1m["close"].to_numpy()
    rv60  = btc_1m["rv_60m"].to_numpy()

    # Compute rkurt_60m if not present
    if "rkurt_60m" in btc_1m.columns:
        rk60 = btc_1m["rkurt_60m"].to_numpy()
    else:
        rk60 = btc_1m["log_ret"].rolling(60).kurt().to_numpy()

    R, V, K = [], [], []
    for i in range(60 * 24, max_idx - horizon_min):
        v, k = rv60[i], rk60[i]
        if not (np.isfinite(v) and np.isfinite(k)): continue
        s, e = close[i], close[i + horizon_min]
        if s <= 0 or e <= 0: continue
        R.append(np.log(e / s)); V.append(v); K.append(k)

    R = np.asarray(R); V = np.asarray(V); K = np.asarray(K)
    if len(R) > n_samples:
        idx = rng.choice(len(R), n_samples, replace=False)
        R, V, K = R[idx], V[idx], K[idx]
    if demean and len(R) > 0:
        R = R - R.mean()        # drift removal — see comment above

    return {
        "horizon_min": horizon_min,
        "log_returns": R,
        "starting_vols": V,
        "starting_kurts": K,
        "v_mean": float(V.mean()) if len(V) else 0.0,
        "v_std":  float(V.std() + 1e-9),
        "k_mean": float(K.mean()) if len(K) else 0.0,
        "k_std":  float(K.std() + 1e-9),
        "n": len(R),
    }


def empirical_p_above(spot: float, strike: float, horizon_min: float,
                       bank: dict, vol: float, kurt: float = 0.0,
                       keep_frac: float = 0.30,
                       brti_dampening: float = 1.0) -> float:
    """P(BTC_T > strike) from the empirical bank, vol+kurt matched.

    brti_dampening: scales log-return samples by this factor before
    computing P. Use < 1.0 to correct for Kalshi's settlement basis
    (CF Benchmarks BRTI 60-second pre-expiry average is smoother than
    minute-close spot — extreme moves in the last 60s get averaged away,
    so actual BRTI moves are systematically smaller than Coinbase
    minute-close moves). Sami's research uses 0.80.
    """
    R = bank["log_returns"]
    V = bank["starting_vols"]
    K = bank["starting_kurts"]
    h = bank["horizon_min"]
    if len(R) == 0: return 0.5
    R = R * float(brti_dampening)
    if horizon_min != h and h > 0:
        R = R * np.sqrt(horizon_min / h)
    vz = (V - bank["v_mean"]) / bank["v_std"]
    kz = (K - bank["k_mean"]) / bank["k_std"]
    tv = (vol  - bank["v_mean"]) / bank["v_std"]
    tk = (kurt - bank["k_mean"]) / bank["k_std"] if np.isfinite(kurt) else 0.0
    dist = np.sqrt((vz - tv)**2 + (kz - tk)**2)
    keep_n = max(200, int(len(R) * keep_frac))
    keep = np.argsort(dist)[:keep_n]
    R = R[keep]
    return float((R > np.log(strike / spot)).mean())


def empirical_p_in_bucket(spot: float, floor_k: float, cap_k: float,
                            horizon_min: float, bank: dict,
                            vol: float, kurt: float = 0.0,
                            brti_dampening: float = 1.0) -> float:
    p_above_floor = empirical_p_above(spot, floor_k, horizon_min, bank, vol, kurt,
                                         brti_dampening=brti_dampening)
    p_above_cap   = empirical_p_above(spot, cap_k,   horizon_min, bank, vol, kurt,
                                         brti_dampening=brti_dampening)
    return max(0.0, p_above_floor - p_above_cap)


# ════════════════════════════════════════════════════════════════════════
#  Lognormal closed-form (fallback)
# ════════════════════════════════════════════════════════════════════════
def lognormal_p_above(spot: float, strike: float, T_years: float,
                       sigma: float, mu: float = 0.0) -> Optional[float]:
    if T_years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return None
    d = (np.log(spot / strike) + (mu - 0.5 * sigma**2) * T_years) / (sigma * np.sqrt(T_years))
    return float(norm.cdf(d))


def lognormal_p_in_bucket(spot: float, floor_k: float, cap_k: float,
                            T_years: float, sigma: float, mu: float = 0.0) -> Optional[float]:
    p_above_floor = lognormal_p_above(spot, floor_k, T_years, sigma, mu)
    p_above_cap   = lognormal_p_above(spot, cap_k, T_years, sigma, mu)
    if p_above_floor is None or p_above_cap is None:
        return None
    return max(0.0, p_above_floor - p_above_cap)


# ════════════════════════════════════════════════════════════════════════
#  Unified fair-value: dispatches by ticker type, prefers empirical bank
# ════════════════════════════════════════════════════════════════════════
def detect_market_type(ticker: str) -> str:
    if "-T" in ticker: return "cumulative"
    if "-B" in ticker: return "bucket"
    return "unknown"


def fair_value(ticker: str, spot: float, floor: float, cap: Optional[float],
                ttl_min: float, sigma: float, kurt: float = 0.0,
                empirical_bank: Optional[dict] = None,
                mu: float = 0.0,
                brti_dampening: Optional[float] = None,
                empirical_blend: Optional[float] = None) -> Optional[float]:
    """Single source of truth for fair P(YES). Returns None if unable to price.

    brti_dampening: scales empirical-bank log-return samples (default
        reads CFG['brti_dampening'], 0.80 from sami's research, to correct
        for Kalshi's CF Benchmarks BRTI 60-second pre-expiry settlement).
    empirical_blend: weight on empirical estimate vs lognormal closed-form
        (default reads CFG['empirical_blend'], 0.70). Final P =
            blend × empirical + (1-blend) × lognormal,
        unless the lognormal is non-finite (in which case empirical-only).

    After the blend, if a Platt calibrator is loaded AND
    CFG['platt_enabled'] is True, the probability is passed through the
    sigmoid before being returned. This corrects the ~6c systematic
    overconfidence we measured in live trade audits.
    """
    from .config import CFG
    if brti_dampening is None:
        brti_dampening = float(CFG.get("brti_dampening", 1.0))
    if empirical_blend is None:
        empirical_blend = float(CFG.get("empirical_blend", 1.0))

    mtype = detect_market_type(ticker)
    if floor is None or floor <= 0 or spot <= 0:
        return None
    T_years = ttl_min / (60 * 24 * 365)

    def _finalize(p: Optional[float]) -> Optional[float]:
        """Apply Platt calibration if enabled + loaded, else passthrough."""
        if p is None:
            return None
        if bool(CFG.get("platt_enabled", True)):
            from .calibration import apply as _platt_apply
            return _platt_apply(float(p))
        return float(p)

    # Bucket: P(floor ≤ S < cap)
    if mtype == "bucket":
        cap_v = float(cap) if (cap is not None and cap > floor) else (floor + 100.0)
        p_emp = None
        if empirical_bank is not None and empirical_bank["n"] > 100:
            p_emp = empirical_p_in_bucket(spot, floor, cap_v, ttl_min,
                                              empirical_bank, sigma, kurt,
                                              brti_dampening=brti_dampening)
        p_log = lognormal_p_in_bucket(spot, floor, cap_v, T_years, sigma, mu)
        if p_emp is not None and p_log is not None:
            return _finalize(empirical_blend * p_emp + (1.0 - empirical_blend) * p_log)
        return _finalize(p_emp if p_emp is not None else p_log)

    # Cumulative: P(S > K)
    p_emp = None
    if empirical_bank is not None and empirical_bank["n"] > 100:
        p_emp = empirical_p_above(spot, floor, ttl_min, empirical_bank,
                                      sigma, kurt, brti_dampening=brti_dampening)
    p_log = lognormal_p_above(spot, floor, T_years, sigma, mu)
    if p_emp is not None and p_log is not None:
        return _finalize(empirical_blend * p_emp + (1.0 - empirical_blend) * p_log)
    return _finalize(p_emp if p_emp is not None else p_log)
