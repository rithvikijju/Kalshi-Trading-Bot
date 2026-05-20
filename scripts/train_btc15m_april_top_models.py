"""Train the top BTC15M April model candidates on a chronological 75/12.5/12.5 split.

Data source:
    backtest_outputs/btc15m_fee_edge_april_validation_latest/
    april_side_candidates_fee_features.parquet

The input file is the April Predexon BTC15M side-candidate table already built
by validate_btc15m_fee_edge_april.py.  Rows are causal candidate-time features:
no future quote highs/lows, no result-derived features except the target fields
that are explicitly excluded from the feature set.

Split:
    train:      2026-04-01 00:00 UTC <= close_time < 2026-04-23 12:00 UTC
    validation: 2026-04-23 12:00 UTC <= close_time < 2026-04-27 06:00 UTC
    test:       2026-04-27 06:00 UTC <= close_time < 2026-05-01 00:00 UTC

Validation is used only to choose the trade gate for each trained model.  The
test split is untouched until final scoring.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_INPUT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_fee_edge_april_validation_latest"
    / "april_side_candidates_fee_features.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_april_top_models_latest"

APR_START = pd.Timestamp("2026-04-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2026-04-23T12:00:00Z")
VALIDATION_END = pd.Timestamp("2026-04-27T06:00:00Z")
APR_END = pd.Timestamp("2026-05-01T00:00:00Z")

LEAK_COLUMNS = {
    "provider",
    "fidelity",
    "market_ticker",
    "event_ticker",
    "series_ticker",
    "timestamp_utc",
    "available_at",
    "open_time",
    "close_time",
    "btc_time",
    "result",
    "status",
    "win",
    "pnl",
    "pnl_2c",
    "eval_split",
    "day",
    "entry_bucket",
    "yes_bid_levels",
    "yes_ask_levels",
}

BASE_FEATURES = [
    "entry_price",
    "entry_fee",
    "premium",
    "rr",
    "spread_cents",
    "visible_qty",
    "opp_visible_qty",
    "visible_ratio",
    "ttl_min",
    "best_bid_cents",
    "best_ask_cents",
    "bid_depth",
    "ask_depth",
    "yes_bid_qty",
    "yes_ask_qty",
    "no_bid_qty",
    "no_ask_qty",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_mid",
    "microprice",
    "micropressure",
    "quote_speed_cents",
    "book_imbalance",
    "side_depth_imbalance",
    "side_micropressure",
    "yes_mid_chg_0_5m",
    "yes_mid_chg_1_0m",
    "yes_mid_chg_2_0m",
    "yes_mid_chg_3_0m",
    "yes_mid_chg_5_0m",
    "side_mid_chg_05m",
    "side_mid_chg_1m",
    "side_mid_chg_2m",
    "side_mid_chg_3m",
    "spread_chg_1_0m",
    "btc_spot_age_sec",
    "btc_ret_1m_bps",
    "btc_ret_3m_bps",
    "btc_ret_5m_bps",
    "side_btc_1m_bps",
    "side_btc_3m_bps",
    "side_btc_5m_bps",
    "btc_lookback_age_1m_sec",
    "btc_lookback_age_3m_sec",
    "btc_lookback_age_5m_sec",
    "rv_60m",
    "rv_15m",
    "rv_ratio_15_60",
    "lognormal_p_yes",
    "side_fair_p",
    "fair_edge_cents",
    "fee_edge",
    "distance_bps",
    "side_distance_bps",
    "lookback_age_0_5m_sec",
    "lookback_age_1_0m_sec",
    "lookback_age_2_0m_sec",
    "lookback_age_3_0m_sec",
    "lookback_age_5_0m_sec",
    "spread_lookback_age_1_0m_sec",
]

LIVE_INCOMPATIBLE_FEATURES = {
    "bid_depth",
    "ask_depth",
    "rv_15m",
    "rv_ratio_15_60",
}


@dataclass
class ModelResult:
    name: str
    model: Any
    scaler: Any | None
    feature_cols: list[str]
    train_seconds: float


def as_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def max_drawdown(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if x.empty:
        return 0.0
    return float((x - x.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def metrics(trades: pd.DataFrame, pnl_col: str) -> dict[str, Any]:
    if trades.empty:
        return {
            "trades": 0,
            "pnl": 0.0,
            "return_on_100_pct": 0.0,
            "premium": 0.0,
            "rop_pct": 0.0,
            "win_rate_pct": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "avg_entry": 0.0,
            "avg_pred_p": 0.0,
            "avg_pred_ev": 0.0,
        }
    pnl = pd.to_numeric(trades[pnl_col], errors="coerce").fillna(0.0)
    premium = pd.to_numeric(trades["premium"], errors="coerce").fillna(0.0)
    wins = pd.to_numeric(trades["win"], errors="coerce").fillna(0.0)
    pred_p = pd.to_numeric(trades["pred_win_prob"], errors="coerce").fillna(0.0)
    pred_ev = pd.to_numeric(trades["pred_ev"], errors="coerce").fillna(0.0)
    return {
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4),
        "return_on_100_pct": round(float(pnl.sum()), 4),
        "premium": round(float(premium.sum()), 4),
        "rop_pct": round(float(pnl.sum() / premium.sum() * 100.0), 2) if float(premium.sum()) > 0 else 0.0,
        "win_rate_pct": round(float(wins.mean() * 100.0), 2),
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(trades["entry_price"], errors="coerce").mean()), 4),
        "avg_pred_p": round(float(pred_p.mean()), 4),
        "avg_pred_ev": round(float(pred_ev.mean()), 4),
    }


def first_per_event(df: pd.DataFrame, score_col: str = "pred_ev") -> pd.DataFrame:
    if df.empty:
        return df.copy()
    sort_cols = ["event_ticker", "available_at", "timestamp_ms", score_col, "pred_win_prob", "side"]
    ascending = [True, True, True, False, False, True]
    return (
        df.sort_values(sort_cols, ascending=ascending, na_position="last")
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def add_two_cent_stress(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    stressed_entry = (pd.to_numeric(out["entry_price"], errors="coerce") + 0.02).clip(upper=0.99)
    stressed_fee = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    won = out["win"].astype(bool).to_numpy()
    out["pnl_2c"] = np.where(won, 1.0 - stressed_entry - stressed_fee, -stressed_entry - stressed_fee)
    return out


def assign_split(close_time: pd.Series) -> pd.Series:
    ts = as_utc(close_time)
    return pd.Series(
        np.select(
            [
                (ts >= APR_START) & (ts < TRAIN_END),
                (ts >= TRAIN_END) & (ts < VALIDATION_END),
                (ts >= VALIDATION_END) & (ts < APR_END),
            ],
            ["train", "validation", "test"],
            default="outside",
        ),
        index=close_time.index,
    )


def load_frame(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    print(f"loading {path}", flush=True)
    df = pd.read_parquet(path)
    df["close_time"] = as_utc(df["close_time"])
    df["available_at"] = as_utc(df["available_at"])
    df = df[(df["close_time"] >= APR_START) & (df["close_time"] < APR_END)].copy()
    df["split"] = assign_split(df["close_time"])
    df = df[df["split"].ne("outside")].copy()
    base_mask = (
        df["data_quality_ok"].astype(bool)
        & df["available_at"].lt(df["close_time"])
        & pd.to_numeric(df["spread_cents"], errors="coerce").le(2.0)
        & pd.to_numeric(df["visible_qty"], errors="coerce").ge(1.0)
        & pd.to_numeric(df["ttl_min"], errors="coerce").between(0.0, 15.0, inclusive="both")
        & pd.to_numeric(df["entry_price"], errors="coerce").between(0.01, 0.99, inclusive="both")
        & pd.to_numeric(df["btc_spot_age_sec"], errors="coerce").le(90.0)
    )
    before = len(df)
    df = df[base_mask].copy()
    print(f"base executable-quality filter kept {len(df):,}/{before:,} rows", flush=True)
    df = add_two_cent_stress(df)

    if max_rows is not None and len(df) > max_rows:
        # Stratified by split for quick smoke tests only.
        parts = []
        for _, group in df.groupby("split", sort=False):
            frac = min(1.0, max_rows / len(df))
            parts.append(group.sample(frac=frac, random_state=14))
        df = pd.concat(parts, ignore_index=True).sort_values(["close_time", "available_at"]).reset_index(drop=True)
        print(f"sampled to {len(df):,} rows for smoke mode", flush=True)
    return df.reset_index(drop=True)


def prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    out["side_yes"] = out["side"].astype(str).str.lower().eq("yes").astype(float)
    close = as_utc(out["close_time"])
    available = as_utc(out["available_at"])
    close_minutes = close.dt.hour.astype(float) * 60.0 + close.dt.minute.astype(float)
    avail_minutes = available.dt.hour.astype(float) * 60.0 + available.dt.minute.astype(float)
    out["close_day_frac_sin"] = np.sin(2.0 * np.pi * close_minutes / 1440.0)
    out["close_day_frac_cos"] = np.cos(2.0 * np.pi * close_minutes / 1440.0)
    out["avail_day_frac_sin"] = np.sin(2.0 * np.pi * avail_minutes / 1440.0)
    out["avail_day_frac_cos"] = np.cos(2.0 * np.pi * avail_minutes / 1440.0)
    out["close_quarter"] = (close.dt.minute // 15).astype(float)

    candidates = BASE_FEATURES + [
        "side_yes",
        "close_day_frac_sin",
        "close_day_frac_cos",
        "avail_day_frac_sin",
        "avail_day_frac_cos",
        "close_quarter",
    ]
    features = [c for c in candidates if c in out.columns and c not in LEAK_COLUMNS]
    for col in features:
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    features = [col for col in features if out[col].notna().any()]
    return out, features


def event_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("event_ticker")["event_ticker"].transform("size").astype(float)
    w = 1.0 / np.sqrt(counts.clip(lower=1.0))
    w = w / float(w.mean())
    return w.to_numpy(dtype=np.float32)


def train_lightgbm(train: pd.DataFrame, features: list[str]) -> ModelResult:
    from lightgbm import LGBMClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline

    start = time.time()
    model = make_pipeline(
        SimpleImputer(strategy="median"),
        LGBMClassifier(
            objective="binary",
            n_estimators=450,
            learning_rate=0.03,
            num_leaves=31,
            min_child_samples=250,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=3.0,
            n_jobs=-1,
            random_state=14,
            verbose=-1,
        ),
    )
    model.fit(train[features], train["win"].astype(int), lgbmclassifier__sample_weight=event_weights(train))
    return ModelResult("lightgbm_tabular", model, None, features, time.time() - start)


def train_xgboost(train: pd.DataFrame, features: list[str]) -> ModelResult:
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from xgboost import XGBClassifier

    start = time.time()
    model = make_pipeline(
        SimpleImputer(strategy="median"),
        XGBClassifier(
            objective="binary:logistic",
            n_estimators=360,
            max_depth=4,
            learning_rate=0.04,
            min_child_weight=75,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=4.0,
            eval_metric="logloss",
            tree_method="hist",
            n_jobs=-1,
            random_state=14,
        ),
    )
    model.fit(train[features], train["win"].astype(int), xgbclassifier__sample_weight=event_weights(train))
    return ModelResult("xgboost_tabular", model, None, features, time.time() - start)


def train_logistic(train: pd.DataFrame, features: list[str]) -> ModelResult:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    start = time.time()
    model = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(
            C=0.25,
            max_iter=1000,
            n_jobs=-1,
            random_state=14,
        ),
    )
    model.fit(train[features], train["win"].astype(int), logisticregression__sample_weight=event_weights(train))
    return ModelResult("logistic_calibrator", model, None, features, time.time() - start)


def train_torch_mlp(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    features: list[str],
    epochs: int = 5,
    batch_size: int = 8192,
) -> ModelResult:
    import torch
    import torch.nn as nn
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    start = time.time()
    torch.manual_seed(14)
    scaler = make_pipeline(SimpleImputer(strategy="median"), StandardScaler())
    x_train = scaler.fit_transform(train[features]).astype(np.float32)
    y_train = train["win"].astype(np.float32).to_numpy()
    w_train = event_weights(train).astype(np.float32)
    x_val = scaler.transform(validation[features]).astype(np.float32)
    y_val = validation["win"].astype(np.float32).to_numpy()

    input_dim = x_train.shape[1]
    model = nn.Sequential(
        nn.Linear(input_dim, 64),
        nn.ReLU(),
        nn.Dropout(0.08),
        nn.Linear(64, 32),
        nn.ReLU(),
        nn.Dropout(0.05),
        nn.Linear(32, 1),
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=2e-3)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")

    x_tensor = torch.from_numpy(x_train)
    y_tensor = torch.from_numpy(y_train).reshape(-1, 1)
    w_tensor = torch.from_numpy(w_train).reshape(-1, 1)
    n = len(x_train)
    best_state = None
    best_val = float("inf")
    patience = 2
    stale = 0
    rng = np.random.default_rng(14)
    for epoch in range(epochs):
        model.train()
        order = rng.permutation(n)
        total_loss = 0.0
        for start_idx in range(0, n, batch_size):
            idx = order[start_idx : start_idx + batch_size]
            xb = x_tensor[idx]
            yb = y_tensor[idx]
            wb = w_tensor[idx]
            optimizer.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = (loss_fn(logits, yb) * wb).mean()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            total_loss += float(loss.detach()) * len(idx)
        model.eval()
        with torch.no_grad():
            val_logits = model(torch.from_numpy(x_val)).reshape(-1)
            val_loss = float(nn.functional.binary_cross_entropy_with_logits(val_logits, torch.from_numpy(y_val)).detach())
        print(f"mlp epoch={epoch + 1} train_loss={total_loss / n:.5f} val_loss={val_loss:.5f}", flush=True)
        if val_loss + 1e-4 < best_val:
            best_val = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return ModelResult("torch_mlp_tabular", model, scaler, features, time.time() - start)


def predict_proba(result: ModelResult, frame: pd.DataFrame) -> np.ndarray:
    if result.name == "torch_mlp_tabular":
        import torch

        x = result.scaler.transform(frame[result.feature_cols]).astype(np.float32)
        result.model.eval()
        probs: list[np.ndarray] = []
        with torch.no_grad():
            for start_idx in range(0, len(x), 65536):
                xb = torch.from_numpy(x[start_idx : start_idx + 65536])
                p = torch.sigmoid(result.model(xb).reshape(-1)).cpu().numpy()
                probs.append(p)
        return np.concatenate(probs) if probs else np.array([], dtype=float)
    return result.model.predict_proba(frame[result.feature_cols])[:, 1]


def score_frame(result: ModelResult, frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["model"] = result.name
    out["pred_win_prob"] = predict_proba(result, out).astype(float)
    entry = pd.to_numeric(out["entry_price"], errors="coerce").astype(float)
    fee = pd.to_numeric(out["entry_fee"], errors="coerce").astype(float)
    out["pred_ev"] = out["pred_win_prob"] * (1.0 - entry - fee) + (1.0 - out["pred_win_prob"]) * (-entry - fee)
    stressed_entry = (entry + 0.02).clip(upper=0.99)
    stressed_fee = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    out["pred_ev_2c"] = out["pred_win_prob"] * (1.0 - stressed_entry - stressed_fee) + (1.0 - out["pred_win_prob"]) * (
        -stressed_entry - stressed_fee
    )
    return out


def choose_gate(validation_scored: pd.DataFrame, min_trades: int) -> dict[str, float | int]:
    best: dict[str, float | int] | None = None
    for min_p in np.round(np.arange(0.52, 0.91, 0.02), 2):
        for min_ev in np.round(np.arange(0.00, 0.201, 0.01), 2):
            selected = first_per_event(validation_scored[(validation_scored["pred_win_prob"] >= min_p) & (validation_scored["pred_ev"] >= min_ev)])
            if len(selected) < min_trades:
                continue
            pnl = pd.to_numeric(selected["pnl_2c"], errors="coerce").fillna(0.0)
            # Prefer validation PnL, but heavily penalize drawdown and ultra-low
            # sample counts.  This keeps threshold search from picking one lucky
            # trade as the "best" model.
            score = float(pnl.sum() + 0.50 * max_drawdown(pnl) - 0.02 * max(0, min_trades * 2 - len(selected)))
            row = {
                "min_p": float(min_p),
                "min_ev": float(min_ev),
                "validation_trades": int(len(selected)),
                "validation_pnl_2c": float(pnl.sum()),
                "validation_max_dd_2c": max_drawdown(pnl),
                "score": score,
            }
            if best is None or score > float(best["score"]):
                best = row
    if best is None:
        return {"min_p": 0.60, "min_ev": 0.00, "validation_trades": 0, "validation_pnl_2c": 0.0, "validation_max_dd_2c": 0.0, "score": -1e9}
    return best


def classify_auc_brier(scored: pd.DataFrame) -> dict[str, float]:
    try:
        from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

        y = scored["win"].astype(int)
        p = scored["pred_win_prob"].astype(float).clip(1e-5, 1 - 1e-5)
        return {
            "auc": round(float(roc_auc_score(y, p)), 4) if y.nunique() > 1 else 0.0,
            "brier": round(float(brier_score_loss(y, p)), 4),
            "logloss": round(float(log_loss(y, p)), 4),
        }
    except Exception:
        return {"auc": 0.0, "brier": 0.0, "logloss": 0.0}


def run_model(result: ModelResult, frame: pd.DataFrame, out_dir: Path, min_trades: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    split_frames = {name: frame[frame["split"].eq(name)].copy() for name in ["train", "validation", "test"]}
    scored = {name: score_frame(result, split_frames[name]) for name in split_frames}
    gate = choose_gate(scored["validation"], min_trades=min_trades)
    rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    for split, part in scored.items():
        selected = first_per_event(part[(part["pred_win_prob"] >= gate["min_p"]) & (part["pred_ev"] >= gate["min_ev"])])
        selected["model"] = result.name
        selected["gate_min_p"] = gate["min_p"]
        selected["gate_min_ev"] = gate["min_ev"]
        trade_frames.append(selected)
        for pnl_col in ["pnl", "pnl_2c"]:
            row = {
                "model": result.name,
                "split": split,
                "pnl_model": pnl_col,
                "gate_min_p": gate["min_p"],
                "gate_min_ev": gate["min_ev"],
                "train_seconds": round(result.train_seconds, 3),
            }
            row.update(metrics(selected, pnl_col))
            row.update({f"class_{k}": v for k, v in classify_auc_brier(part).items()})
            rows.append(row)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    trades.to_parquet(out_dir / f"{result.name}_trades.parquet", index=False, compression="zstd")
    return pd.DataFrame(rows), trades


def write_report(out_dir: Path, summary: pd.DataFrame, data_report: dict[str, Any], feature_cols: list[str]) -> None:
    lines = [
        "# BTC15M April Top Model Training",
        "",
        "## Data",
        f"- Input: `{data_report['input']}`",
        f"- Rows after executable-quality filter: {data_report['rows']:,}",
        f"- Events after executable-quality filter: {data_report['events']:,}",
        f"- Features: {len(feature_cols)}",
        "- Chronological split: 75% train, 12.5% validation, 12.5% final test.",
        "",
        "## Split Counts",
        "```",
        pd.DataFrame(data_report["splits"]).to_string(index=False),
        "```",
        "",
        "## Final Summary",
        "```",
        summary.sort_values(["model", "split", "pnl_model"]).to_string(index=False),
        "```",
        "",
        "## Feature Columns",
        "```",
        "\n".join(feature_cols),
        "```",
        "",
        "Notes:",
        "- The model target is candidate-side settlement win/loss.",
        "- Trading metrics are first qualifying candidate per event under each model's validation-selected gate.",
        "- `pnl_2c` adds a 2c adverse entry stress before fees.",
        "- Validation selects the gate; final test is not used for fitting or threshold selection.",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--models", default="lightgbm,xgboost,mlp", help="comma-separated: lightgbm,xgboost,mlp")
    parser.add_argument("--max-rows", type=int, default=None, help="smoke-test row cap; default uses full April candidate table")
    parser.add_argument("--min-validation-trades", type=int, default=12)
    parser.add_argument(
        "--live-compatible-only",
        action="store_true",
        help="drop features that are not reconstructed in live websocket replay",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    frame = load_frame(args.input, max_rows=args.max_rows)
    frame, features = prepare_features(frame)
    if args.live_compatible_only:
        features = [f for f in features if f not in LIVE_INCOMPATIBLE_FEATURES]
    train = frame[frame["split"].eq("train")].copy()
    validation = frame[frame["split"].eq("validation")].copy()
    if train.empty or validation.empty:
        raise SystemExit("empty train or validation split")

    split_report = []
    for split, part in frame.groupby("split", sort=False):
        split_report.append(
            {
                "split": split,
                "start_close": str(part["close_time"].min()),
                "end_close": str(part["close_time"].max()),
                "rows": int(len(part)),
                "events": int(part["event_ticker"].nunique()),
                "win_rate": round(float(part["win"].astype(float).mean()), 4),
            }
        )

    data_report = {
        "input": str(args.input),
        "rows": int(len(frame)),
        "events": int(frame["event_ticker"].nunique()),
        "markets": int(frame["market_ticker"].nunique()),
        "features": features,
        "splits": split_report,
        "split_boundaries": {
            "apr_start": str(APR_START),
            "train_end": str(TRAIN_END),
            "validation_end": str(VALIDATION_END),
            "apr_end": str(APR_END),
        },
    }
    (args.out_dir / "data_report.json").write_text(json.dumps(data_report, indent=2), encoding="utf-8")
    print(json.dumps({k: data_report[k] for k in ["rows", "events", "markets"]}, indent=2), flush=True)
    print("features", len(features), flush=True)
    print(pd.DataFrame(split_report).to_string(index=False), flush=True)

    requested = {x.strip().lower() for x in args.models.split(",") if x.strip()}
    trainers = []
    if "lightgbm" in requested:
        trainers.append(train_lightgbm)
    if "xgboost" in requested:
        trainers.append(train_xgboost)
    if "logistic" in requested or "logit" in requested:
        trainers.append(train_logistic)
    if "mlp" in requested:
        trainers.append(lambda tr, feat: train_torch_mlp(tr, validation, feat))
    if not trainers:
        raise SystemExit("no models requested")

    all_summaries = []
    all_trades = []
    for trainer in trainers:
        result = trainer(train, features)
        print(f"trained {result.name} in {result.train_seconds:.1f}s", flush=True)
        with (args.out_dir / f"{result.name}.pkl").open("wb") as f:
            pickle.dump({"model": result.model, "scaler": result.scaler, "features": result.feature_cols}, f)
        summary, trades = run_model(result, frame, args.out_dir, min_trades=args.min_validation_trades)
        all_summaries.append(summary)
        all_trades.append(trades)
        print(summary[summary["pnl_model"].eq("pnl_2c")].to_string(index=False), flush=True)

    summary_df = pd.concat(all_summaries, ignore_index=True)
    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    summary_df.to_csv(args.out_dir / "model_summary.csv", index=False)
    trades_df.to_parquet(args.out_dir / "all_model_trades.parquet", index=False, compression="zstd")
    write_report(args.out_dir, summary_df, data_report, features)
    print(f"wrote {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
