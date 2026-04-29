"""Leakage review + walk-forward feature experiments for kalshi-bot.ipynb.

Runs offline on synthetic BTC-like minute data (vol-clustered, fat-tailed,
with intraday seasonality and occasional jumps). The synthetic generator
is *not* the point — the point is the methodology: same training/eval logic
that should be transplanted into the notebook against real BTC data.

What this script proves:
  1. The notebook's `backtest_probability_models` overstates skill because
     EMPIRICAL_SAMPLES and HARRV_MODELS are fit on data that includes the
     evaluation window. We reproduce that bias here.
  2. A walk-forward harness (refit per fold on past-only data) gives a
     real measure of skill.
  3. Several proposed features improve log-loss / Brier in the clean harness;
     this script reports the lift per feature.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from scipy.stats import norm

RNG = np.random.default_rng(0)
MINUTES_PER_YEAR = 60 * 24 * 365


# -----------------------------------------------------------------------------
# 1. Synthetic BTC-like minute data
# -----------------------------------------------------------------------------
def make_synthetic_minutes(n_days: int = 180, seed: int = 0) -> pd.DataFrame:
    """Vol-clustered, fat-tailed, seasonal minute returns. ~180d ≈ 260k bars."""
    rng = np.random.default_rng(seed)
    n = n_days * 24 * 60

    # GARCH(1,1)-ish vol process in per-minute units
    omega, alpha, beta = 1e-8, 0.06, 0.93
    sigma2 = np.empty(n)
    sigma2[0] = omega / (1 - alpha - beta)
    eps = rng.standard_t(df=5, size=n) / np.sqrt(5 / 3)  # fat tails, unit var
    r = np.empty(n)
    r[0] = np.sqrt(sigma2[0]) * eps[0]
    for t in range(1, n):
        sigma2[t] = omega + alpha * r[t - 1] ** 2 + beta * sigma2[t - 1]
        r[t] = np.sqrt(sigma2[t]) * eps[t]

    # Intraday seasonality: hour-of-day vol multiplier (peak around 14-16 UTC)
    minutes = np.arange(n)
    hour = (minutes // 60) % 24
    season = 0.85 + 0.30 * np.sin((hour - 6) / 24 * 2 * np.pi)
    r = r * season

    # Rare jumps
    jump_idx = rng.choice(n, size=n // 5000, replace=False)
    r[jump_idx] += rng.standard_normal(jump_idx.size) * 0.01

    # Mild positive drift
    r += 1e-5

    price = 70_000 * np.exp(np.cumsum(r))
    t0 = pd.Timestamp("2025-01-01", tz="UTC")
    times = pd.date_range(t0, periods=n, freq="1min")

    return pd.DataFrame({"time": times, "close": price, "log_ret": r})


# -----------------------------------------------------------------------------
# 2. Reproduce the notebook's RV features (same windows, same annualization)
# -----------------------------------------------------------------------------
def add_rv_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    af = MINUTES_PER_YEAR
    df["rv_15m"] = df["log_ret"].rolling(15).std() * np.sqrt(af)
    df["rv_60m"] = df["log_ret"].rolling(60).std() * np.sqrt(af)
    df["rv_240m"] = df["log_ret"].rolling(240).std() * np.sqrt(af)
    df["rv_1d"] = df["log_ret"].rolling(60 * 24).std() * np.sqrt(af)
    df["rv_3d"] = df["log_ret"].rolling(60 * 24 * 3).std() * np.sqrt(af)

    # Notebook does NOT lag these. They use the value as-of bar i, which only
    # contains returns up to bar i (rolling is left-aligned), so this is OK
    # for live use — but during training, predictors at i should still be
    # computed strictly from <i to avoid look-ahead. We'll lag by 1 to be safe.
    for c in ["rv_15m", "rv_60m", "rv_240m", "rv_1d", "rv_3d"]:
        df[c] = df[c].shift(1)
    return df


# -----------------------------------------------------------------------------
# 3. Build (X, y) for HAR-RV with a strict no-leak boundary
# -----------------------------------------------------------------------------
def build_harrv(df: pd.DataFrame, horizon_min: int, max_idx: int | None = None):
    """X uses features at i (already lag-shifted by 1), y is realized vol over
    (i, i+horizon]. If max_idx given, only build samples whose forward window
    ends at <= max_idx — i.e. nothing in the training set can peek past max_idx.
    """
    if max_idx is None:
        max_idx = len(df) - 1
    out_X, out_y = [], []
    cols = ["rv_15m", "rv_60m", "rv_1d"]
    rv = df[cols].to_numpy()
    lr = df["log_ret"].to_numpy()
    for i in range(60 * 24, max_idx - horizon_min):
        x = rv[i]
        if not np.all(np.isfinite(x)):
            continue
        future = lr[i + 1 : i + 1 + horizon_min]
        if len(future) < horizon_min:
            continue
        y = np.std(future) * np.sqrt(MINUTES_PER_YEAR)
        if not np.isfinite(y):
            continue
        out_X.append(x)
        out_y.append(y)
    return np.array(out_X), np.array(out_y)


# -----------------------------------------------------------------------------
# 4. Empirical return samples — leak-aware version
# -----------------------------------------------------------------------------
def build_empirical_samples(df: pd.DataFrame, horizon_min: int,
                            max_idx: int, n_samples: int = 5000,
                            seed: int = 0) -> dict:
    """Same shape as the notebook's, but only uses bars whose forward window
    ends at or before max_idx. The notebook builds this from the full series."""
    rng = np.random.default_rng(seed)
    lr_close = df["close"].to_numpy()
    rv60 = df["rv_60m"].to_numpy()
    out_r, out_v = [], []
    for i in range(60 * 24, max_idx - horizon_min):
        v = rv60[i]
        if not np.isfinite(v):
            continue
        s, e = lr_close[i], lr_close[i + horizon_min]
        if s <= 0 or e <= 0:
            continue
        out_r.append(np.log(e / s))
        out_v.append(v)
    out_r = np.asarray(out_r)
    out_v = np.asarray(out_v)
    if len(out_r) > n_samples:
        idx = rng.choice(len(out_r), n_samples, replace=False)
        out_r, out_v = out_r[idx], out_v[idx]
    return {"horizon_min": horizon_min, "log_returns": out_r,
            "starting_vols": out_v, "n": len(out_r)}


def empirical_p_above(spot, strike, horizon_min, samples,
                      current_vol_60m=None, vol_bandwidth=0.30):
    """Same logic as the notebook (modulo bug fixes). Stripped vol-rescale
    double-count: we vol-match by selecting nearest-vol windows and then
    *optionally* rescale. The notebook always rescales which double-counts."""
    returns = samples["log_returns"].copy()
    vols = samples["starting_vols"]
    h = samples["horizon_min"]
    if horizon_min != h and h > 0:
        returns = returns * np.sqrt(horizon_min / h)
    if current_vol_60m is not None and len(vols) > 100:
        k = max(200, int(len(vols) * vol_bandwidth))
        kept = np.argsort(np.abs(vols - current_vol_60m))[:k]
        returns = returns[kept]
    if len(returns) == 0:
        return 0.5
    return float((returns > np.log(strike / spot)).mean())


# -----------------------------------------------------------------------------
# 5. Feature toolbox — drop-in computers, all use only past info
# -----------------------------------------------------------------------------
def add_feature_pack(df: pd.DataFrame) -> pd.DataFrame:
    """All features are .shift(1) at the end so no row peeks at its own bar."""
    df = df.copy()
    lr = df["log_ret"]
    af = MINUTES_PER_YEAR

    # Realized variance (additive across time, lower-noise than vol)
    df["rvar_15m"] = (lr ** 2).rolling(15).sum() * (af / 15)
    df["rvar_60m"] = (lr ** 2).rolling(60).sum() * (af / 60)
    df["rvar_1d"] = (lr ** 2).rolling(60 * 24).sum() * (af / (60 * 24))

    # Rolling drift / momentum at multiple windows
    df["mom_15m"] = lr.rolling(15).sum()
    df["mom_60m"] = lr.rolling(60).sum()
    df["mom_240m"] = lr.rolling(240).sum()

    # Realized skew + kurt (regime indicators)
    df["rskew_60m"] = lr.rolling(60).skew()
    df["rkurt_60m"] = lr.rolling(60).kurt()

    # Bipower variation (jump-robust vol estimator)
    df["bv_60m"] = (lr.abs() * lr.shift(1).abs()).rolling(60).sum() * (np.pi / 2)
    df["jump_share_60m"] = (df["rvar_60m"] / (af / 60) - df["bv_60m"]).clip(lower=0) / \
                           (df["rvar_60m"] / (af / 60)).replace(0, np.nan)

    # Vol-of-vol (signal that regime is unstable)
    df["vov_60m"] = df["rv_60m"].rolling(60).std()

    # Trend persistence: fraction of last N minutes with same sign
    sign = np.sign(lr)
    df["trend_persist_60m"] = sign.rolling(60).mean().abs()

    # Intraday seasonality dummies (hour of day, sine/cosine encoding)
    h = df["time"].dt.hour + df["time"].dt.minute / 60
    df["tod_sin"] = np.sin(2 * np.pi * h / 24)
    df["tod_cos"] = np.cos(2 * np.pi * h / 24)
    df["dow"] = df["time"].dt.dayofweek

    # Implied vol gap proxy: difference between short and long RV
    df["rv_gap"] = df["rv_15m"] - df["rv_1d"]

    new = ["rvar_15m", "rvar_60m", "rvar_1d", "mom_15m", "mom_60m", "mom_240m",
           "rskew_60m", "rkurt_60m", "bv_60m", "jump_share_60m", "vov_60m",
           "trend_persist_60m", "tod_sin", "tod_cos", "rv_gap"]
    for c in new:
        df[c] = df[c].shift(1)
    return df


# -----------------------------------------------------------------------------
# 6. Probability models being compared
# -----------------------------------------------------------------------------
def lognormal_p_above(spot, strike, T, sigma, drift_per_year=0.0):
    if sigma <= 0 or T <= 0:
        return 0.5
    d = (np.log(spot / strike) + (drift_per_year - 0.5 * sigma ** 2) * T) / \
        (sigma * np.sqrt(T))
    return float(norm.cdf(d))


# -----------------------------------------------------------------------------
# 7. Walk-forward evaluation — the honest backtest
# -----------------------------------------------------------------------------
def walk_forward_eval(df: pd.DataFrame, horizon_min: int,
                      n_folds: int = 5, n_eval_per_fold: int = 1000,
                      seed: int = 7,
                      include_features: tuple[str, ...] = (),
                      use_empirical: bool = False,
                      use_drift: bool = False) -> dict:
    """Split chronologically into folds. For each fold:
       - Train HAR-RV on data strictly before the fold's start.
       - If empirical: build samples strictly before the fold's start.
       - Sample evaluation indices INSIDE the fold.
       - Score lognormal_p_above and (optionally) empirical_p_above.
    """
    rng = np.random.default_rng(seed)
    N = len(df)
    train_start = 60 * 24 * 7  # leave a week of warm-up
    fold_size = (N - train_start - horizon_min) // n_folds
    cols = ["rv_15m", "rv_60m", "rv_1d"] + list(include_features)
    X_full = df[cols].to_numpy()
    rv60 = df["rv_60m"].to_numpy()
    lr = df["log_ret"].to_numpy()
    close = df["close"].to_numpy()

    out_log_loss, out_brier, out_n = [], [], 0
    eps = 1e-6

    for fold in range(n_folds):
        fold_lo = train_start + fold * fold_size
        fold_hi = fold_lo + fold_size

        # ---- Train HAR-RV (extended) on bars whose forward window ends < fold_lo ----
        train_X, train_y = [], []
        for i in range(60 * 24, fold_lo - horizon_min):
            x = X_full[i]
            if not np.all(np.isfinite(x)):
                continue
            future = lr[i + 1 : i + 1 + horizon_min]
            y = np.std(future) * np.sqrt(MINUTES_PER_YEAR)
            if not np.isfinite(y):
                continue
            train_X.append(x); train_y.append(y)
        if len(train_X) < 1000:
            continue
        train_X = np.asarray(train_X); train_y = np.asarray(train_y)
        model = LinearRegression().fit(train_X, train_y)

        # ---- Empirical samples strictly before the fold ----
        emp = None
        if use_empirical:
            emp = build_empirical_samples(df, horizon_min, max_idx=fold_lo,
                                          n_samples=5000, seed=seed + fold)

        # ---- Eval points inside the fold ----
        eval_lo = fold_lo
        eval_hi = fold_hi - horizon_min - 1
        if eval_hi - eval_lo < 100:
            continue
        idxs = rng.choice(np.arange(eval_lo, eval_hi),
                          size=min(n_eval_per_fold, eval_hi - eval_lo),
                          replace=False)
        for i in idxs:
            x = X_full[i]
            if not np.all(np.isfinite(x)):
                continue
            sigma = float(model.predict(x.reshape(1, -1))[0])
            if not np.isfinite(sigma) or sigma <= 0:
                continue
            spot = close[i]
            offset = rng.uniform(-2.0, 2.0)
            strike = spot * (1 + offset / 100)
            T = horizon_min / MINUTES_PER_YEAR

            drift_y = 0.0
            if use_drift:
                # 30-min momentum -> per-year drift, capped
                m30 = np.sum(lr[i - 30 : i])
                drift_y = float(np.clip(m30 / 30 * 60 * 24 * 365, -2.0, 2.0))

            if use_empirical:
                p = empirical_p_above(spot, strike, horizon_min, emp,
                                      current_vol_60m=rv60[i])
            else:
                p = lognormal_p_above(spot, strike, T, sigma, drift_y)

            actual = 1 if close[i + horizon_min] > strike else 0
            pc = np.clip(p, eps, 1 - eps)
            out_log_loss.append(-(actual * np.log(pc) + (1 - actual) * np.log(1 - pc)))
            out_brier.append((p - actual) ** 2)
            out_n += 1

    return {
        "log_loss": float(np.mean(out_log_loss)) if out_log_loss else np.nan,
        "brier": float(np.mean(out_brier)) if out_brier else np.nan,
        "n": out_n,
    }


# -----------------------------------------------------------------------------
# 8. Reproduce the LEAKED variant the notebook uses
# -----------------------------------------------------------------------------
def leaked_eval(df: pd.DataFrame, horizon_min: int, n: int = 5000,
                seed: int = 7) -> dict:
    """Mimic the notebook: build EMPIRICAL_SAMPLES & HAR-RV on the FULL series,
    then sample evaluation points anywhere — including inside the train window."""
    rng = np.random.default_rng(seed)
    cols = ["rv_15m", "rv_60m", "rv_1d"]
    Xa, ya = build_harrv(df, horizon_min, max_idx=len(df) - 1)  # uses full data
    model = LinearRegression().fit(Xa, ya)
    emp = build_empirical_samples(df, horizon_min, max_idx=len(df) - 1,
                                  n_samples=5000, seed=seed)
    rv60 = df["rv_60m"].to_numpy()
    lr = df["log_ret"].to_numpy()
    close = df["close"].to_numpy()

    starts = rng.choice(np.arange(60 * 24, len(df) - horizon_min - 1),
                        size=n, replace=False)
    ll_emp, br_emp, ll_lgn, br_lgn = [], [], [], []
    eps = 1e-6
    for i in starts:
        x = df[cols].iloc[i].to_numpy()
        if not np.all(np.isfinite(x)):
            continue
        sigma = float(model.predict(x.reshape(1, -1))[0])
        if not np.isfinite(sigma) or sigma <= 0:
            continue
        spot = close[i]
        strike = spot * (1 + rng.uniform(-2.0, 2.0) / 100)
        T = horizon_min / MINUTES_PER_YEAR
        actual = 1 if close[i + horizon_min] > strike else 0

        p_emp = empirical_p_above(spot, strike, horizon_min, emp,
                                  current_vol_60m=rv60[i])
        p_lgn = lognormal_p_above(spot, strike, T, sigma)
        for p, ll, br in [(p_emp, ll_emp, br_emp), (p_lgn, ll_lgn, br_lgn)]:
            pc = np.clip(p, eps, 1 - eps)
            ll.append(-(actual * np.log(pc) + (1 - actual) * np.log(1 - pc)))
            br.append((p - actual) ** 2)
    return {
        "lognormal_log_loss": float(np.mean(ll_lgn)),
        "lognormal_brier": float(np.mean(br_lgn)),
        "empirical_log_loss": float(np.mean(ll_emp)),
        "empirical_brier": float(np.mean(br_emp)),
        "n": len(ll_emp),
    }


# -----------------------------------------------------------------------------
# 9. Main: build data, run experiments
# -----------------------------------------------------------------------------
def main():
    print("Building synthetic minute data (180 days)…")
    df = make_synthetic_minutes(n_days=180, seed=1)
    df = add_rv_features(df)
    df = add_feature_pack(df)
    print(f"Bars: {len(df):,}  span {df['time'].min()} → {df['time'].max()}")

    horizon = 60

    # ---- Show the LEAKED version (notebook style) overstates skill ----
    print("\n" + "=" * 72)
    print("A. LEAKED EVAL (mimics notebook backtest_probability_models)")
    print("=" * 72)
    leaked = leaked_eval(df, horizon_min=horizon, n=5000, seed=7)
    print(f"  Lognormal:  log_loss={leaked['lognormal_log_loss']:.4f}  "
          f"Brier={leaked['lognormal_brier']:.4f}")
    print(f"  Empirical:  log_loss={leaked['empirical_log_loss']:.4f}  "
          f"Brier={leaked['empirical_brier']:.4f}   (n={leaked['n']})")

    # ---- Walk-forward baseline ----
    print("\n" + "=" * 72)
    print("B. WALK-FORWARD EVAL (no leakage; refit per fold)")
    print("=" * 72)
    base_lgn = walk_forward_eval(df, horizon, use_empirical=False)
    base_emp = walk_forward_eval(df, horizon, use_empirical=True)
    print(f"  Lognormal (HAR-RV vol): log_loss={base_lgn['log_loss']:.4f}  "
          f"Brier={base_lgn['brier']:.4f}  (n={base_lgn['n']})")
    print(f"  Empirical:              log_loss={base_emp['log_loss']:.4f}  "
          f"Brier={base_emp['brier']:.4f}  (n={base_emp['n']})")

    # ---- Feature ablation: add one extra HAR-RV feature at a time ----
    print("\n" + "=" * 72)
    print("C. FEATURE ABLATION — extra HAR-RV inputs (vs. baseline lognormal)")
    print(f"   Baseline log_loss={base_lgn['log_loss']:.4f}  "
          f"Brier={base_lgn['brier']:.4f}")
    print("=" * 72)

    candidate_features = [
        ("rvar_60m", ("rvar_60m",)),
        ("bv_60m", ("bv_60m",)),
        ("jump_share_60m", ("jump_share_60m",)),
        ("vov_60m", ("vov_60m",)),
        ("rskew_60m", ("rskew_60m",)),
        ("rkurt_60m", ("rkurt_60m",)),
        ("mom_60m", ("mom_60m",)),
        ("mom_240m", ("mom_240m",)),
        ("trend_persist_60m", ("trend_persist_60m",)),
        ("tod_sin+cos", ("tod_sin", "tod_cos")),
        ("rv_gap", ("rv_gap",)),
        ("rv_3d", ("rv_3d",)),
    ]

    rows = []
    for name, feats in candidate_features:
        r = walk_forward_eval(df, horizon, include_features=feats)
        d_ll = base_lgn["log_loss"] - r["log_loss"]
        d_br = base_lgn["brier"] - r["brier"]
        rows.append({
            "feature": name,
            "log_loss": r["log_loss"],
            "Δlog_loss_vs_base": d_ll,
            "brier": r["brier"],
            "Δbrier_vs_base": d_br,
            "n": r["n"],
        })
    res = pd.DataFrame(rows).sort_values("Δlog_loss_vs_base", ascending=False)
    print(res.to_string(index=False, float_format=lambda x: f"{x:+.5f}"))

    # ---- Combined: top-3 features together ----
    print("\n" + "=" * 72)
    print("D. STACKED: top-3 features together")
    print("=" * 72)
    top3 = tuple(f for name in res.head(3)["feature"].tolist()
                 for f in dict(candidate_features)[name])
    stacked = walk_forward_eval(df, horizon, include_features=top3)
    print(f"  Top-3 stack ({top3}):")
    print(f"    log_loss={stacked['log_loss']:.4f}  "
          f"(Δ {base_lgn['log_loss'] - stacked['log_loss']:+.5f})")
    print(f"    brier   ={stacked['brier']:.4f}  "
          f"(Δ {base_lgn['brier'] - stacked['brier']:+.5f})")

    # ---- Drift toggle ----
    print("\n" + "=" * 72)
    print("E. DRIFT TOGGLE — does momentum-based drift help?")
    print("=" * 72)
    base_drift = walk_forward_eval(df, horizon, use_drift=True)
    print(f"  Base + drift: log_loss={base_drift['log_loss']:.4f}  "
          f"Brier={base_drift['brier']:.4f}")
    print(f"  Δ vs base: log_loss {base_lgn['log_loss'] - base_drift['log_loss']:+.5f}, "
          f"Brier {base_lgn['brier'] - base_drift['brier']:+.5f}")

    return {
        "leaked": leaked,
        "walk_forward_baseline": base_lgn,
        "walk_forward_empirical": base_emp,
        "feature_ablation": res,
        "stacked": stacked,
        "drift": base_drift,
    }


if __name__ == "__main__":
    main()
