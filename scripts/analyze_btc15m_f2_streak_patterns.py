#!/usr/bin/env python3
"""Diagnose BTC15M F2 win/loss streak structure without touching holdouts.

This is not a strategy search.  It uses Apr1-14 as the diagnostic/training
window and Apr15-30 as validation for any simple gate/model derived from the
diagnostic window.  Jan/live websocket data should stay external unless passed
explicitly for a read-only external check.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_f2_streak_patterns_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


NUMERIC_FEATURES = [
    "entry_price",
    "side_fair_p",
    "fair_edge_cents",
    "ttl_min",
    "spread_cents",
    "visible_qty",
    "quote_speed_cents",
    "book_imbalance",
    "btc_ret_3m_bps",
    "rv_60m",
    "distance_bps",
    "yes_mid_chg_2_0m",
    "yes_mid_chg_3_0m",
    "prev_win",
    "prev_pnl_stress",
    "prior_2_win_rate",
    "prior_3_win_rate",
    "prior_5_win_rate",
    "prev_loss_run",
    "prev_win_run",
    "hour_utc",
]

CATEGORICAL_FEATURES = ["side"]


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    path: Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--train-trades", type=Path, required=True)
    p.add_argument("--val-trades", type=Path, required=True)
    p.add_argument("--external-trades", type=Path, default=None)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--permutations", type=int, default=20000)
    return p.parse_args()


def markdown_table(df: pd.DataFrame, floatfmt: str = ".4f") -> str:
    if df.empty:
        return ""
    work = df.copy()
    for col in work.columns:
        if pd.api.types.is_float_dtype(work[col]):
            work[col] = work[col].map(lambda x: "" if pd.isna(x) else format(float(x), floatfmt))
        else:
            work[col] = work[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(str(col)), int(work[col].astype(str).map(len).max())) for col in work.columns}
    header = "| " + " | ".join(str(col).ljust(widths[col]) for col in work.columns) + " |"
    sep = "| " + " | ".join("-" * widths[col] for col in work.columns) + " |"
    lines = [header, sep]
    for _, row in work.iterrows():
        lines.append("| " + " | ".join(str(row[col]).ljust(widths[col]) for col in work.columns) + " |")
    return "\n".join(lines)


def read_trades(path: Path, split_name: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["split_src"] = split_name
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True)
    df["win"] = df["win"].astype(bool)
    for col in ["pnl_stress", "entry_price", "premium_stress", "visible_qty", "ttl_min"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values(["strategy", "available_at", "market_ticker"]).reset_index(drop=True)


def add_causal_lags(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for strategy, g in df.sort_values(["strategy", "available_at"]).groupby("strategy", sort=False):
        g = g.copy().reset_index(drop=True)
        g["prev_win"] = g["win"].shift(1).astype("float")
        g["prev_pnl_stress"] = g["pnl_stress"].shift(1)
        for n in (2, 3, 5):
            g[f"prior_{n}_win_rate"] = g["win"].shift(1).rolling(n, min_periods=1).mean()
        prev_loss_run = []
        prev_win_run = []
        loss_run = 0
        win_run = 0
        for i in range(len(g)):
            prev_loss_run.append(loss_run)
            prev_win_run.append(win_run)
            if bool(g.loc[i, "win"]):
                win_run += 1
                loss_run = 0
            else:
                loss_run += 1
                win_run = 0
        g["prev_loss_run"] = prev_loss_run
        g["prev_win_run"] = prev_win_run
        g["hour_utc"] = g["available_at"].dt.hour
        out.append(g)
    return pd.concat(out, ignore_index=True)


def run_lengths(win: np.ndarray) -> tuple[list[int], list[int]]:
    win_runs: list[int] = []
    loss_runs: list[int] = []
    if len(win) == 0:
        return win_runs, loss_runs
    current = bool(win[0])
    length = 1
    for item in win[1:]:
        item = bool(item)
        if item == current:
            length += 1
        else:
            (win_runs if current else loss_runs).append(length)
            current = item
            length = 1
    (win_runs if current else loss_runs).append(length)
    return win_runs, loss_runs


def streak_stats(g: pd.DataFrame, rng: np.random.Generator, permutations: int) -> dict[str, float | int]:
    win = g["win"].to_numpy(dtype=bool)
    n = len(win)
    wins = int(win.sum())
    wr = wins / n if n else 0.0
    win_runs, loss_runs = run_lengths(win)
    obs_max_win = max(win_runs) if win_runs else 0
    obs_max_loss = max(loss_runs) if loss_runs else 0
    p_after_win = float(np.mean(win[1:][win[:-1]])) if np.any(win[:-1]) else math.nan
    p_after_loss = float(np.mean(win[1:][~win[:-1]])) if np.any(~win[:-1]) else math.nan
    trans_diff = p_after_win - p_after_loss if not (math.isnan(p_after_win) or math.isnan(p_after_loss)) else math.nan
    fisher_p = math.nan
    if n >= 4:
        ww = int(np.sum(win[1:] & win[:-1]))
        wl = int(np.sum((~win[1:]) & win[:-1]))
        lw = int(np.sum(win[1:] & (~win[:-1])))
        ll = int(np.sum((~win[1:]) & (~win[:-1])))
        try:
            fisher_p = float(stats.fisher_exact([[ww, wl], [lw, ll]])[1])
        except Exception:
            fisher_p = math.nan
    perm_max_wins = []
    perm_max_losses = []
    for _ in range(permutations):
        shuffled = win.copy()
        rng.shuffle(shuffled)
        wruns, lruns = run_lengths(shuffled)
        perm_max_wins.append(max(wruns) if wruns else 0)
        perm_max_losses.append(max(lruns) if lruns else 0)
    p_max_win = float((np.asarray(perm_max_wins) >= obs_max_win).mean()) if permutations else math.nan
    p_max_loss = float((np.asarray(perm_max_losses) >= obs_max_loss).mean()) if permutations else math.nan
    return {
        "trades": n,
        "pnl": float(g["pnl_stress"].sum()),
        "win_rate": wr,
        "max_win_streak": obs_max_win,
        "max_loss_streak": obs_max_loss,
        "p_win_after_win": p_after_win,
        "p_win_after_loss": p_after_loss,
        "transition_diff": trans_diff,
        "fisher_transition_p": fisher_p,
        "perm_p_max_win_streak": p_max_win,
        "perm_p_max_loss_streak": p_max_loss,
    }


def eval_subset(df: pd.DataFrame, mask: pd.Series) -> dict[str, float | int]:
    s = df.loc[mask].copy()
    if s.empty:
        return {"trades": 0, "pnl": 0.0, "win_rate": 0.0, "max_dd": 0.0}
    pnl = s["pnl_stress"].astype(float)
    equity = pnl.cumsum()
    max_dd = float((equity - equity.cummax()).min())
    return {
        "trades": int(len(s)),
        "pnl": float(pnl.sum()),
        "win_rate": float(s["win"].mean()),
        "max_dd": max_dd,
    }


def train_selected_median_gates(train: pd.DataFrame, val: pd.DataFrame, strategy: str) -> pd.DataFrame:
    rows = []
    tr = train[train.strategy == strategy].copy()
    va = val[val.strategy == strategy].copy()
    if tr.empty or va.empty:
        return pd.DataFrame()
    for col in NUMERIC_FEATURES:
        if col not in tr.columns:
            continue
        tr_col = pd.to_numeric(tr[col], errors="coerce")
        med = float(tr_col.median()) if tr_col.notna().any() else math.nan
        if math.isnan(med):
            continue
        high = tr_col >= med
        low = tr_col < med
        if high.sum() < 5 or low.sum() < 5:
            continue
        high_pnl = float(tr.loc[high, "pnl_stress"].sum())
        low_pnl = float(tr.loc[low, "pnl_stress"].sum())
        direction = ">=" if high_pnl >= low_pnl else "<"
        train_mask = high if direction == ">=" else low
        val_col = pd.to_numeric(va[col], errors="coerce")
        val_mask = val_col >= med if direction == ">=" else val_col < med
        row = {
            "strategy": strategy,
            "feature": col,
            "threshold": med,
            "direction": direction,
            **{f"train_{k}": v for k, v in eval_subset(tr, train_mask).items()},
            **{f"val_{k}": v for k, v in eval_subset(va, val_mask).items()},
        }
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["val_pnl", "val_trades"], ascending=[False, False])


def apply_gates_to_external(gates: pd.DataFrame, external: pd.DataFrame | None) -> pd.DataFrame:
    """Apply train-selected gates to an untouched external trade file."""
    if external is None or external.empty or gates.empty:
        return pd.DataFrame()
    rows = []
    for _, gate in gates.iterrows():
        strategy = str(gate["strategy"])
        feature = str(gate["feature"])
        if feature not in external.columns:
            continue
        ex = external[external.strategy == strategy].copy()
        if ex.empty:
            continue
        threshold = float(gate["threshold"])
        values = pd.to_numeric(ex[feature], errors="coerce")
        if str(gate["direction"]) == ">=":
            mask = values >= threshold
        else:
            mask = values < threshold
        row = {
            "strategy": strategy,
            "feature": feature,
            "direction": str(gate["direction"]),
            "threshold": threshold,
            "train_trades": gate.get("train_trades"),
            "train_pnl": gate.get("train_pnl"),
            "val_trades": gate.get("val_trades"),
            "val_pnl": gate.get("val_pnl"),
            **{f"external_{k}": v for k, v in eval_subset(ex, mask).items()},
        }
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["external_pnl", "val_pnl"], ascending=[False, False])


def ml_eval(train: pd.DataFrame, val: pd.DataFrame, strategy: str, model_name: str) -> dict[str, float | int | str]:
    tr = train[train.strategy == strategy].copy()
    va = val[val.strategy == strategy].copy()
    if len(tr) < 30 or len(va) < 15 or tr["win"].nunique() < 2 or va["win"].nunique() < 2:
        return {"strategy": strategy, "model": model_name, "status": "too_few_rows"}
    features = [c for c in NUMERIC_FEATURES + CATEGORICAL_FEATURES if c in tr.columns]
    num = [c for c in NUMERIC_FEATURES if c in features]
    cat = [c for c in CATEGORICAL_FEATURES if c in features]
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), num),
            ("cat", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("oh", OneHotEncoder(handle_unknown="ignore"))]), cat),
        ],
        remainder="drop",
    )
    if model_name == "logistic":
        clf = LogisticRegression(max_iter=1000, C=0.25, class_weight="balanced", random_state=1337)
    elif model_name == "hgb":
        clf = HistGradientBoostingClassifier(max_iter=50, learning_rate=0.05, l2_regularization=1.0, random_state=1337)
    else:
        raise ValueError(model_name)
    pipe = Pipeline([("pre", pre), ("clf", clf)])
    pipe.fit(tr[features], tr["win"].astype(int))
    tr_prob = pipe.predict_proba(tr[features])[:, 1]
    va_prob = pipe.predict_proba(va[features])[:, 1]
    threshold = float(np.quantile(tr_prob, 0.50))
    va_mask = pd.Series(va_prob >= threshold, index=va.index)
    out = {
        "strategy": strategy,
        "model": model_name,
        "status": "ok",
        "train_auc": float(roc_auc_score(tr["win"].astype(int), tr_prob)),
        "val_auc": float(roc_auc_score(va["win"].astype(int), va_prob)),
        "threshold_train_median": threshold,
        **{f"val_selected_{k}": v for k, v in eval_subset(va, va_mask).items()},
        **{f"val_all_{k}": v for k, v in eval_subset(va, pd.Series(True, index=va.index)).items()},
    }
    return out


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    train = add_causal_lags(read_trades(args.train_trades, "train_apr1_14"))
    val = add_causal_lags(read_trades(args.val_trades, "val_apr15_30"))
    external = add_causal_lags(read_trades(args.external_trades, "external")) if args.external_trades else None

    split_rows = []
    for split_name, df in [("train_apr1_14", train), ("val_apr15_30", val)] + ([] if external is None else [("external", external)]):
        for strategy, g in df.groupby("strategy", sort=False):
            row = {"split": split_name, "strategy": strategy}
            row.update(streak_stats(g.sort_values("available_at"), rng, args.permutations))
            split_rows.append(row)
    streak_df = pd.DataFrame(split_rows)
    streak_df.to_csv(args.out_dir / "streak_stats.csv", index=False)

    gate_frames = []
    for strategy in sorted(set(train.strategy) & set(val.strategy)):
        gate_frames.append(train_selected_median_gates(train, val, strategy))
    gates = pd.concat([g for g in gate_frames if g is not None and not g.empty], ignore_index=True) if gate_frames else pd.DataFrame()
    gates.to_csv(args.out_dir / "train_selected_median_gates.csv", index=False)
    external_gate_checks = apply_gates_to_external(gates, external)
    if not external_gate_checks.empty:
        external_gate_checks.to_csv(args.out_dir / "external_gate_checks.csv", index=False)

    ml_rows = []
    for strategy in sorted(set(train.strategy) & set(val.strategy)):
        for model_name in ("logistic", "hgb"):
            ml_rows.append(ml_eval(train, val, strategy, model_name))
    ml_df = pd.DataFrame(ml_rows)
    ml_df.to_csv(args.out_dir / "ml_sanity.csv", index=False)

    report = []
    report.append("# BTC15M F2 Streak/Pattern Diagnostic")
    report.append("")
    report.append("Rules: Apr1-14 is diagnostic/training only; Apr15-30 is validation. Jan/live websocket remain external checks.")
    report.append("")
    report.append("## Streak Statistics")
    cols = [
        "split",
        "strategy",
        "trades",
        "pnl",
        "win_rate",
        "max_win_streak",
        "max_loss_streak",
        "p_win_after_win",
        "p_win_after_loss",
        "transition_diff",
        "fisher_transition_p",
        "perm_p_max_loss_streak",
    ]
    report.append(markdown_table(streak_df[cols], floatfmt=".4f"))
    report.append("")
    report.append("## Best Train-Selected Median Gates By Validation PnL")
    if gates.empty:
        report.append("No usable gates.")
    else:
        show = gates.sort_values(["val_pnl", "val_trades"], ascending=[False, False]).head(20)
        report.append(markdown_table(show[[
            "strategy",
            "feature",
            "direction",
            "threshold",
            "train_trades",
            "train_pnl",
            "val_trades",
            "val_pnl",
            "val_win_rate",
            "val_max_dd",
        ]], floatfmt=".4f"))
    report.append("")
    report.append("## ML Sanity Check")
    report.append(markdown_table(ml_df, floatfmt=".4f"))
    report.append("")
    if not external_gate_checks.empty:
        report.append("## External Check For Train-Selected Gates")
        show = external_gate_checks.sort_values(["external_pnl", "val_pnl"], ascending=[False, False]).head(20)
        report.append(markdown_table(show[[
            "strategy",
            "feature",
            "direction",
            "threshold",
            "train_trades",
            "train_pnl",
            "val_trades",
            "val_pnl",
            "external_trades",
            "external_pnl",
            "external_win_rate",
            "external_max_dd",
        ]], floatfmt=".4f"))
        report.append("")
    report.append("## Interpretation Guardrails")
    report.append("- Transition/streak p-values are descriptive; they do not prove an exploitable filter.")
    report.append("- Median gates are selected only on Apr1-14 and reported on Apr15-30, but there are many gates, so treat winners as hypotheses.")
    report.append("- ML results are sanity checks. A model needs Jan/live websocket validation before promotion.")
    (args.out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    meta = {
        "train_trades": str(args.train_trades),
        "val_trades": str(args.val_trades),
        "external_trades": str(args.external_trades) if args.external_trades else None,
        "seed": args.seed,
        "permutations": args.permutations,
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(streak_df[cols].to_string(index=False))
    print("\\nML:")
    print(ml_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
