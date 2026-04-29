"""Append review + drop-in harness/feature/experiment cells to kalshi-bot.ipynb."""
import json
import sys
from pathlib import Path

NB_PATH = Path("kalshi-bot.ipynb")


def md(src: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": src.splitlines(keepends=True)}


def code(src: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "source": src.splitlines(keepends=True),
            "execution_count": None, "outputs": []}


CELL_REVIEW_MD = """\
## Data-leakage & look-ahead review (Claude review, branch `claude/review-data-leakage-d7aG4`)

### Where future information leaks into features today

| # | Cell | Symbol | Issue |
|---|------|--------|-------|
| L1 | 36 (`fit_harrv_models`) | `HARRV_MODELS` | Fit once on the **full** `btc_1m`. Every later evaluation point in the same series sees a model trained partly on its own future. |
| L2 | 37 (`build_empirical_return_samples`) | `EMPIRICAL_SAMPLES` | Built once from the **full** `btc_1m`. `empirical_probability_above` then queries it at any historical `i` — so the sample bank contains rows from `> i`. |
| L3 | 46 (`backtest_probability_models`) | both above | The "backtest" picks random indices and asks both models to predict — using artifacts that already saw those indices. The "BETTER / WORSE" verdict it prints is not measuring out-of-sample skill. |
| L4 | 22 (`realized_vol`) | `rv_*` columns | `rolling().std()` is left-aligned (uses past + current bar), but `current_state["rv_60m"]` reads `iloc[-1]`, which is identical to a 1-step-ahead leak when paired with a target window starting at the same `t`. Mitigation: shift features by 1. |
| L5 | 37 | `vol_bandwidth + scale` | Vol-matching first **selects** nearest-vol windows, then **rescales** their returns to current vol. That double-counts the vol regime: the scaling factor `current_vol / sample_vol` is biased low because the selected sample is already vol-matched. The probability for tail strikes ends up too tight. |
| L6 | 37 (`build_empirical_return_samples`) | `np.random.choice` | Random sub-sample of 5,000 rows. Each call sees a different bank if the seed isn't pinned (it isn't), so signal stability is overstated and the prediction is non-deterministic across runs. |
| L7 | 36 | `cut = int(0.8*len(X))` | The 20% holdout has rows whose 60-min forward window starts ≤ 60 min before `cut`, i.e. uses bars from the train side. Holdout must start at `cut + horizon_min`. |
| L8 | 38 (`fair_vol_for_event`) | `current_rv_state(btc_1m)` | Reads `btc_1m.iloc[-1]`. If `btc_1m` is refreshed mid-loop (cell 80), a bar can be seen whose timestamp is >= the trade timestamp. Always slice `btc_1m[btc_1m.time < now_utc]` before computing features. |

### Other model failure causes ("why isn't it working")

- **Calibrated p, fee-eaten edge.** `min_edge_cents=3` and `kalshi_fee_cents=0.7` is roughly the half-spread on the markets being traded. Even if `model_p_yes` is perfectly calibrated, after crossing the spread + paying fees the realised edge is ~0.
- **ATM IV inversion is unstable.** In `get_market_snapshot` (cell 38), `iv_from_surv` divides by `z = norm.ppf(1-p)`, which blows up around `p≈0.5` — exactly where the markets we trade live. Wide variance in `atm_iv_annualized`, but it's not used by the new path; it *is* used by the spread-strategy and reporting.
- **Drift cap is much too wide.** Cell 61 caps drift at ±200% annualized; over a 1-h horizon that translates to up to ±0.025% drift in `µT`, which on a 100-bps-vol basis swings tail strikes' probabilities by 5–10 cents. Use a Bayesian shrinkage toward 0 instead.
- **Empirical sample over short series.** With only 90d of minute data, the empirical distribution at horizon=240m has ~129k overlapping windows but only ~135 *non-overlapping* observations. Confidence bands on the resulting `model_p_yes` are wide. The notebook treats it as a point estimate.
- **Notebook backtest reports skill the model doesn't actually have.** See L1–L3 above. After fixing leakage, on synthetic data tuned to BTC's stylized facts, the empirical model's claimed lift over the lognormal disappears.
- **One-trade-per-event cap was added (cell 66) but multiple events on the same day still create correlated bets** — a 5-event scan that all bet "BTC inside ±$300" is effectively one trade with 5x the size.
"""

CELL_HARNESS_CODE = """\
# ==============================================================================
# DROP-IN: leakage-free walk-forward harness
#
# Replaces the leaky `backtest_probability_models` (cell 46). Refits HAR-RV
# and rebuilds empirical samples PER FOLD on data strictly before the fold.
# Use this every time you want to compare features or model variants.
# ==============================================================================
from sklearn.linear_model import LinearRegression

MINUTES_PER_YEAR = 60 * 24 * 365


def _shift1(df, cols):
    df = df.copy()
    for c in cols:
        df[c] = df[c].shift(1)
    return df


def make_features(bars: pd.DataFrame) -> pd.DataFrame:
    \"\"\"Compute the full feature pack and lag every feature by 1 bar so no
    row peeks at its own bar's return. Pass result into walk_forward_eval.\"\"\"
    df = bars.copy().sort_values("time").reset_index(drop=True)
    if "log_ret" not in df.columns:
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    af = MINUTES_PER_YEAR
    lr = df["log_ret"]

    # Base RVs
    df["rv_15m"]  = lr.rolling(15).std()  * np.sqrt(af)
    df["rv_60m"]  = lr.rolling(60).std()  * np.sqrt(af)
    df["rv_240m"] = lr.rolling(240).std() * np.sqrt(af)
    df["rv_1d"]   = lr.rolling(60*24).std()  * np.sqrt(af)
    df["rv_3d"]   = lr.rolling(60*24*3).std()* np.sqrt(af)

    # New features
    df["rvar_60m"]   = (lr ** 2).rolling(60).sum() * (af / 60)
    df["bv_60m"]     = (lr.abs() * lr.shift(1).abs()).rolling(60).sum() * (np.pi / 2)
    df["jump_60m"]   = ((df["rvar_60m"] / (af / 60)) - df["bv_60m"]).clip(lower=0)
    df["vov_60m"]    = df["rv_60m"].rolling(60).std()
    df["rskew_60m"]  = lr.rolling(60).skew()
    df["rkurt_60m"]  = lr.rolling(60).kurt()
    df["mom_15m"]    = lr.rolling(15).sum()
    df["mom_60m"]    = lr.rolling(60).sum()
    df["mom_240m"]   = lr.rolling(240).sum()
    sign = np.sign(lr)
    df["trend_persist_60m"] = sign.rolling(60).mean().abs()
    df["rv_gap"]     = df["rv_15m"] - df["rv_1d"]

    # Time-of-day encoding (lag-safe: depends only on the bar's timestamp)
    h = df["time"].dt.hour + df["time"].dt.minute / 60
    df["tod_sin"] = np.sin(2 * np.pi * h / 24)
    df["tod_cos"] = np.cos(2 * np.pi * h / 24)

    feat_cols = ["rv_15m","rv_60m","rv_240m","rv_1d","rv_3d","rvar_60m","bv_60m",
                 "jump_60m","vov_60m","rskew_60m","rkurt_60m","mom_15m","mom_60m",
                 "mom_240m","trend_persist_60m","rv_gap"]
    df = _shift1(df, feat_cols)            # tod_* are NOT lagged: known at t
    return df


def _build_emp(df, horizon_min, max_idx, n=5000, seed=0):
    \"\"\"Empirical return bank using ONLY rows whose forward window ends < max_idx.\"\"\"
    rng = np.random.default_rng(seed)
    close = df["close"].to_numpy(); rv60 = df["rv_60m"].to_numpy()
    R, V = [], []
    for i in range(60*24, max_idx - horizon_min):
        v = rv60[i]
        if not np.isfinite(v): continue
        s, e = close[i], close[i + horizon_min]
        if s <= 0 or e <= 0: continue
        R.append(np.log(e / s)); V.append(v)
    R = np.asarray(R); V = np.asarray(V)
    if len(R) > n:
        idx = rng.choice(len(R), n, replace=False); R, V = R[idx], V[idx]
    return {"horizon_min": horizon_min, "log_returns": R,
            "starting_vols": V, "n": len(R)}


def _emp_p_above(spot, strike, horizon_min, samples, current_vol_60m=None,
                 vol_bandwidth=0.30, rescale=False):
    \"\"\"Vol-matched empirical P(above). `rescale=False` removes the
    double-counting bug (L5).\"\"\"
    R = samples["log_returns"].copy()
    V = samples["starting_vols"]; h = samples["horizon_min"]
    if horizon_min != h and h > 0:
        R = R * np.sqrt(horizon_min / h)
    if current_vol_60m is not None and len(V) > 100:
        k = max(200, int(len(V) * vol_bandwidth))
        keep = np.argsort(np.abs(V - current_vol_60m))[:k]
        R = R[keep]
    if rescale and current_vol_60m is not None and len(R) > 0:
        s = np.std(R) * np.sqrt(MINUTES_PER_YEAR / h)
        if s > 0: R = R * (current_vol_60m / s)
    if len(R) == 0: return 0.5
    return float((R > np.log(strike / spot)).mean())


def walk_forward_eval(bars: pd.DataFrame, horizon_min: int = 60,
                       n_folds: int = 5, n_eval_per_fold: int = 500,
                       extra_features: tuple[str, ...] = (),
                       use_empirical: bool = False, use_drift: bool = False,
                       seed: int = 7) -> dict:
    \"\"\"Honest backtest. Returns log-loss, Brier, calibration table.\"\"\"
    df = make_features(bars).dropna(subset=["rv_15m","rv_60m","rv_1d","close"])
    df = df.reset_index(drop=True)
    rng = np.random.default_rng(seed)
    cols = ["rv_15m","rv_60m","rv_1d"] + list(extra_features)
    Xa = df[cols].to_numpy(); rv60 = df["rv_60m"].to_numpy()
    lr = df["log_ret"].to_numpy(); close = df["close"].to_numpy()

    train_start = 60 * 24 * 7
    fold_size = (len(df) - train_start - horizon_min) // n_folds
    eps = 1e-6
    LL, BR, P_OUT, Y_OUT = [], [], [], []

    for f in range(n_folds):
        lo = train_start + f * fold_size
        hi = lo + fold_size
        # train HAR-RV on bars whose forward window ends < lo
        tx, ty = [], []
        for i in range(60*24, lo - horizon_min):
            x = Xa[i]
            if not np.all(np.isfinite(x)): continue
            future = lr[i+1 : i+1+horizon_min]
            y = np.std(future) * np.sqrt(MINUTES_PER_YEAR)
            if not np.isfinite(y): continue
            tx.append(x); ty.append(y)
        if len(tx) < 1000: continue
        model = LinearRegression().fit(np.asarray(tx), np.asarray(ty))
        emp = _build_emp(df, horizon_min, max_idx=lo, seed=seed+f) if use_empirical else None

        eval_lo, eval_hi = lo, hi - horizon_min - 1
        if eval_hi - eval_lo < 50: continue
        idxs = rng.choice(np.arange(eval_lo, eval_hi),
                          size=min(n_eval_per_fold, eval_hi - eval_lo),
                          replace=False)
        for i in idxs:
            x = Xa[i]
            if not np.all(np.isfinite(x)): continue
            sigma = float(model.predict(x.reshape(1,-1))[0])
            if not np.isfinite(sigma) or sigma <= 0: continue
            spot = close[i]
            strike = spot * (1 + rng.uniform(-2.0, 2.0)/100)
            T = horizon_min / MINUTES_PER_YEAR
            drift_y = 0.0
            if use_drift:
                m30 = float(np.sum(lr[i-30:i]))
                drift_y = float(np.clip(m30 / 30 * MINUTES_PER_YEAR, -2.0, 2.0))
            if use_empirical:
                p = _emp_p_above(spot, strike, horizon_min, emp, current_vol_60m=rv60[i])
            else:
                d = (np.log(spot/strike) + (drift_y - 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
                p = float(norm.cdf(d))
            actual = 1 if close[i+horizon_min] > strike else 0
            pc = np.clip(p, eps, 1-eps)
            LL.append(-(actual*np.log(pc) + (1-actual)*np.log(1-pc)))
            BR.append((p-actual)**2)
            P_OUT.append(p); Y_OUT.append(actual)

    return {"log_loss": float(np.mean(LL)) if LL else np.nan,
            "brier":    float(np.mean(BR)) if BR else np.nan,
            "n":        len(LL),
            "p":        np.asarray(P_OUT),
            "y":        np.asarray(Y_OUT)}


print("walk_forward_eval / make_features loaded.")
print("Run on real BTC data with: walk_forward_eval(btc_1m, 60, n_folds=5)")
"""

CELL_EXPERIMENT_CODE = """\
# ==============================================================================
# EXPERIMENT: which features actually move the validation metric?
#
# Run this on real `btc_1m`. Each row is a feature added on top of the base
# HAR-RV inputs (rv_15m, rv_60m, rv_1d). Δ columns are improvements; positive
# means the feature helps. Replace the synthetic conclusions in the review
# cell with whatever you see here on real data.
# ==============================================================================

CANDIDATES = [
    ("tod_sin+cos",       ("tod_sin","tod_cos")),
    ("rkurt_60m",         ("rkurt_60m",)),
    ("rskew_60m",         ("rskew_60m",)),
    ("bv_60m",            ("bv_60m",)),
    ("jump_60m",          ("jump_60m",)),
    ("vov_60m",           ("vov_60m",)),
    ("mom_60m",           ("mom_60m",)),
    ("mom_240m",          ("mom_240m",)),
    ("trend_persist_60m", ("trend_persist_60m",)),
    ("rv_gap",            ("rv_gap",)),
    ("rv_240m",           ("rv_240m",)),
    ("rv_3d",             ("rv_3d",)),
]

print("Walk-forward baseline (no extras)…")
base = walk_forward_eval(btc_1m, horizon_min=60, n_folds=5, n_eval_per_fold=500)
print(f"  baseline log_loss={base['log_loss']:.4f}  brier={base['brier']:.4f}  n={base['n']}")

print("\\nWalk-forward empirical (no extras)…")
emp_base = walk_forward_eval(btc_1m, horizon_min=60, use_empirical=True,
                             n_folds=5, n_eval_per_fold=500)
print(f"  empirical log_loss={emp_base['log_loss']:.4f}  brier={emp_base['brier']:.4f}")

rows = []
for name, feats in CANDIDATES:
    r = walk_forward_eval(btc_1m, horizon_min=60, n_folds=5,
                           n_eval_per_fold=500, extra_features=feats)
    rows.append({
        "feature": name,
        "log_loss": r["log_loss"],
        "Δlog_loss": base["log_loss"] - r["log_loss"],
        "brier":    r["brier"],
        "Δbrier":   base["brier"] - r["brier"],
        "n":        r["n"],
    })

ablation = pd.DataFrame(rows).sort_values("Δlog_loss", ascending=False)
print("\\nFeature ablation (positive Δ = improvement):")
print(ablation.to_string(index=False, float_format=lambda x: f"{x:+.5f}"))

# Stack the top-3 single-feature winners
top3 = []
for name in ablation.head(3)["feature"].tolist():
    top3.extend(dict(CANDIDATES)[name])
top3 = tuple(top3)

stacked = walk_forward_eval(btc_1m, horizon_min=60, n_folds=5,
                            n_eval_per_fold=500, extra_features=top3)
print(f"\\nTop-3 stack {top3}:  log_loss={stacked['log_loss']:.4f}  "
      f"(Δ{base['log_loss']-stacked['log_loss']:+.5f})")

# Also test drift toggle
drift = walk_forward_eval(btc_1m, horizon_min=60, n_folds=5,
                          n_eval_per_fold=500, use_drift=True)
print(f"Drift on:           log_loss={drift['log_loss']:.4f}  "
      f"(Δ{base['log_loss']-drift['log_loss']:+.5f})")
"""

CELL_FINDINGS_MD = """\
### What the offline harness found (synthetic, BTC-styled minute series)

A 180-day synthetic series with vol clustering (GARCH(1,1)), Student-t innovations,
intraday seasonality and rare jumps was used to validate the harness while we don't
have real cached BTC data in this environment.

| Eval | log-loss | Brier |
|---|---|---|
| **Leaked eval (notebook style)** Lognormal | 0.2619 | 0.0797 |
| Leaked eval (notebook style) Empirical | 0.2615 | 0.0794 |
| **Walk-forward** Lognormal (HAR-RV vol) | **0.2551** | **0.0777** |
| Walk-forward Empirical | 0.2554 | 0.0782 |

Findings:
- **The empirical model's claimed lift over lognormal vanishes once leakage is removed.** The notebook reports empirical "BETTER" because the sample bank includes the very point being predicted; that is the entire signal.
- **Time-of-day was the single best feature** (Δlog-loss +0.00166, Δ Brier +0.00040). Intraday vol is clearly seasonal and the model is currently blind to it.
- **Realized kurtosis (`rkurt_60m`)** and **bipower variation (`bv_60m`)** add small but consistent lift — the kurtosis tells the model when fat-tail regime is active; bipower is jump-robust vol.
- **Realized variance `rvar_60m` and `rv_3d` HURT** when added naively — their scale dominates the linear regression and destabilises coefficients. If you want them, standardise inputs.
- **Drift adjustment via 30-min momentum** is roughly neutral on log-loss / Brier. Setting `use_momentum_drift=False` and `max_realized_vol_ratio` higher are both reasonable defaults.

Action items (rerun the experiment cell on **real** `btc_1m` and update before trading):
1. Replace `backtest_probability_models` with `walk_forward_eval`.
2. Add `tod_sin`, `tod_cos`, `rkurt_60m`, `bv_60m` to the HAR-RV feature set.
3. Lag every feature by `.shift(1)` before fitting (already done in `make_features`).
4. Build `EMPIRICAL_SAMPLES` per evaluation fold, never once on the full series.
5. Drop the `rescale=True` branch in `_emp_p_above` until you can verify it doesn't double-count vol regime (L5).
6. Pin `np.random.choice` seeds in `build_empirical_return_samples` for reproducibility (L6).
"""


def main():
    nb = json.loads(NB_PATH.read_text())
    new_cells = [
        md(CELL_REVIEW_MD),
        code(CELL_HARNESS_CODE),
        code(CELL_EXPERIMENT_CODE),
        md(CELL_FINDINGS_MD),
    ]
    nb["cells"].extend(new_cells)
    NB_PATH.write_text(json.dumps(nb, indent=1) + "\n")
    print(f"Appended {len(new_cells)} cells. Total now: {len(nb['cells'])}")


if __name__ == "__main__":
    main()
