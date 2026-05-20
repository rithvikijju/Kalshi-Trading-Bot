#!/usr/bin/env python3
"""Assess whether settlement-basis flip modeling is feasible yet.

This is a diagnostic artifact, not a strategy. It builds a row-level table from
the current official-vs-proxy settlement audit, separates decision-time features
from post-event diagnostics, and runs a small grouped cross-validation check
only to answer whether a causal basis-danger model is currently supportable.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_settlement_basis_model_feasibility_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

DECISION_FEATURES = [
    "family_btc15m",
    "side_yes",
    "entry_price",
    "visible_qty_log1p",
    "spread_cents",
    "ttl_min",
    "abs_decision_distance_bps",
    "signed_decision_distance_bps",
    "btc_spot_age_sec",
    "rv_60m",
    "quote_age_ms",
]

POST_EVENT_DIAGNOSTIC_FIELDS = [
    "proxy_distance_usd",
    "abs_proxy_distance_usd",
    "official_distance_usd",
    "basis_usd",
    "abs_basis_usd",
    "proxy_result",
    "official_result",
    "official_pnl_2c",
    "proxy_pnl_2c",
    "pnl_delta_official_minus_proxy_2c",
    "proxy_official_mismatch",
    "adverse_proxy_official_mismatch",
    "proxy_win_official_loss",
]

BANNED_LIVE_FEATURE_TOKENS = [
    "basis",
    "official",
    "proxy_distance",
    "proxy_close",
    "result",
    "pnl",
    "win",
    "settlement",
    "expiration",
    "close_spot",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC settlement-basis model feasibility diagnostics.")
    p.add_argument(
        "--basis-risk-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc_settlement_basis_risk_audit_latest_codex",
    )
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-samples", type=int, default=40)
    p.add_argument("--min-positives", type=int, default=8)
    p.add_argument("--max-folds", type=int, default=5)
    p.add_argument("--min-guard-samples", type=int, default=250)
    p.add_argument("--min-guard-positives", type=int, default=30)
    p.add_argument("--min-guard-roc-auc", type=float, default=0.60)
    p.add_argument("--min-guard-ap-lift", type=float, default=2.0)
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([math.nan] * len(df), index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def text(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col not in df.columns:
        return pd.Series([default] * len(df), index=df.index)
    return df[col].fillna(default).astype(str).replace({"nan": default, "None": default, "<NA>": default})


def boolish(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df[col].astype(str).str.lower().isin(["true", "1", "yes", "y"])


def unsafe_feature_reason(feature: str) -> str:
    normalized = feature.lower()
    hits = [token for token in BANNED_LIVE_FEATURE_TOKENS if token in normalized]
    if hits:
        return "contains_banned_live_token:" + ",".join(hits)
    return ""


def validate_decision_features(features: list[str] | None = None) -> None:
    checked = list(DECISION_FEATURES if features is None else features)
    unsafe = [(feature, unsafe_feature_reason(feature)) for feature in checked if unsafe_feature_reason(feature)]
    if unsafe:
        formatted = "; ".join(f"{feature}({reason})" for feature, reason in unsafe)
        raise ValueError(f"Decision feature list contains post-event/leaky fields: {formatted}")


def feature_safety_audit(decision_features: list[str] | None = None) -> pd.DataFrame:
    checked = list(DECISION_FEATURES if decision_features is None else decision_features)
    rows: list[dict[str, Any]] = []
    for feature in checked:
        reason = unsafe_feature_reason(feature)
        rows.append(
            {
                "feature": feature,
                "feature_role": "decision_time_model_feature",
                "live_feature_allowed": not bool(reason),
                "reason": reason or "known_before_order_decision",
            }
        )
    for field in POST_EVENT_DIAGNOSTIC_FIELDS:
        rows.append(
            {
                "feature": field,
                "feature_role": "post_event_diagnostic_only",
                "live_feature_allowed": False,
                "reason": "unknown_at_order_decision",
            }
        )
    return pd.DataFrame(rows)


def prepare_rows(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    rows = raw.copy()
    rows["family"] = text(rows, "family")
    rows["candidate"] = text(rows, "candidate")
    rows["source"] = text(rows, "source")
    rows["market_ticker"] = text(rows, "market_ticker")
    rows["side"] = text(rows, "side").str.lower()
    rows["created_at"] = text(rows, "created_at")
    rows["proxy_official_mismatch"] = boolish(rows, "proxy_official_mismatch")
    rows["adverse_proxy_official_mismatch"] = boolish(rows, "adverse_proxy_official_mismatch")
    rows["proxy_win_official_loss"] = boolish(rows, "proxy_win_official_loss")
    rows["both_result_rows"] = boolish(rows, "both_result_rows")
    rows["official_pnl_2c"] = num(rows, "official_pnl_2c")
    rows["proxy_pnl_2c"] = num(rows, "proxy_pnl_2c")
    rows["pnl_delta_official_minus_proxy_2c"] = num(rows, "pnl_delta_official_minus_proxy_2c")

    rows["entry_price"] = num(rows, "entry_price")
    rows["visible_qty"] = num(rows, "visible_qty").fillna(num(rows, "top_visible_qty"))
    rows["visible_qty_log1p"] = np.log1p(rows["visible_qty"].clip(lower=0))
    rows["spread_cents"] = num(rows, "spread_cents")
    rows["ttl_min"] = num(rows, "ttl_min")
    signed = num(rows, "aligned_decision_distance_bps").fillna(num(rows, "entry_spot_distance_bps"))
    rows["signed_decision_distance_bps"] = signed
    rows["abs_decision_distance_bps"] = signed.abs()
    rows["btc_spot_age_sec"] = num(rows, "btc_spot_age_sec")
    rows["rv_60m"] = num(rows, "rv_60m")
    rows["quote_age_ms"] = num(rows, "quote_age_ms")
    rows["side_yes"] = rows["side"].eq("yes").astype(float)
    rows["family_btc15m"] = rows["family"].eq("BTC15M").astype(float)

    rows["proxy_distance_usd"] = num(rows, "proxy_distance_usd")
    rows["abs_proxy_distance_usd"] = num(rows, "abs_proxy_distance_usd").fillna(rows["proxy_distance_usd"].abs())
    rows["basis_usd"] = num(rows, "basis_usd")
    rows["abs_basis_usd"] = num(rows, "abs_basis_usd").fillna(rows["basis_usd"].abs())
    rows["official_distance_usd"] = num(rows, "official_distance_usd")
    rows["post_event_leakage_warning"] = (
        "proxy_distance_usd/official_distance_usd/basis_usd are post-event diagnostics only; do not use as live model features"
    )
    return rows


def model_dataset(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    eligible = rows[rows["both_result_rows"]].copy()
    # Remove exact duplicate candidate rows while preserving distinct decision
    # timestamps and prices. This avoids repeated q-family overlap from
    # dominating the feasibility metrics.
    subset = [
        "family",
        "source",
        "market_ticker",
        "side",
        "created_at",
        "entry_price",
        "visible_qty",
        "spread_cents",
        "signed_decision_distance_bps",
    ]
    subset = [col for col in subset if col in eligible.columns]
    return eligible.drop_duplicates(subset=subset).reset_index(drop=True)


def safe_metric(value: float) -> float | str:
    if not math.isfinite(float(value)):
        return ""
    return round(float(value), 4)


def metric_float(row: pd.Series, name: str) -> float:
    value = row.get(name, math.nan)
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def evaluate_target(df: pd.DataFrame, target: str, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    coefs: list[dict[str, Any]] = []
    base = {
        "target": target,
        "samples": int(len(df)),
        "positives": int(df[target].sum()) if target in df.columns else 0,
        "positive_rate": round(float(df[target].mean()), 4) if target in df.columns and len(df) else 0.0,
    }
    if target not in df.columns or df.empty:
        rows.append({**base, "model_status": "missing_target"})
        return pd.DataFrame(rows), pd.DataFrame(coefs)
    positives = int(df[target].sum())
    negatives = int(len(df) - positives)
    if len(df) < args.min_samples or positives < args.min_positives or negatives < args.min_positives:
        rows.append(
            {
                **base,
                "model_status": "insufficient_sample",
                "reason": f"need >= {args.min_samples} samples and >= {args.min_positives} positives/negatives",
            }
        )
        return pd.DataFrame(rows), pd.DataFrame(coefs)
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
        from sklearn.model_selection import GroupKFold, StratifiedKFold
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:  # pragma: no cover - depends on local optional package
        rows.append({**base, "model_status": "sklearn_unavailable", "reason": repr(exc)})
        return pd.DataFrame(rows), pd.DataFrame(coefs)

    work = df.copy()
    y = work[target].astype(int).to_numpy()
    available_features = [feature for feature in DECISION_FEATURES if feature in work.columns and work[feature].notna().any()]
    missing_all_features = [feature for feature in DECISION_FEATURES if feature not in available_features]
    if not available_features:
        rows.append({**base, "model_status": "no_available_decision_features"})
        return pd.DataFrame(rows), pd.DataFrame(coefs)
    x = work[available_features].copy()
    groups = work["market_ticker"].astype(str).to_numpy()
    unique_groups = pd.Series(groups).nunique()
    folds = min(args.max_folds, positives, negatives, unique_groups)
    if folds < 2:
        rows.append({**base, "model_status": "insufficient_groups", "reason": "not enough class-balanced groups"})
        return pd.DataFrame(rows), pd.DataFrame(coefs)

    pipe = Pipeline(
        steps=[
            (
                "prep",
                ColumnTransformer(
                    [("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), available_features)],
                    remainder="drop",
                ),
            ),
            ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.25)),
        ]
    )
    splits = []
    try:
        splitter = GroupKFold(n_splits=folds)
        splits = list(splitter.split(x, y, groups=groups))
        split_kind = "group_kfold_market_ticker"
    except Exception:
        splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=17)
        splits = list(splitter.split(x, y))
        split_kind = "stratified_kfold_fallback"

    fold_rows: list[dict[str, Any]] = []
    all_y: list[int] = []
    all_p: list[float] = []
    for fold, (train_idx, test_idx) in enumerate(splits, start=1):
        if len(set(y[test_idx])) < 2:
            continue
        pipe.fit(x.iloc[train_idx], y[train_idx])
        prob = pipe.predict_proba(x.iloc[test_idx])[:, 1]
        all_y.extend(y[test_idx].tolist())
        all_p.extend(prob.tolist())
        fold_rows.append(
            {
                "target": target,
                "fold": fold,
                "test_rows": int(len(test_idx)),
                "test_positives": int(y[test_idx].sum()),
                "roc_auc": safe_metric(roc_auc_score(y[test_idx], prob)),
                "average_precision": safe_metric(average_precision_score(y[test_idx], prob)),
                "brier": safe_metric(brier_score_loss(y[test_idx], prob)),
            }
        )

    if len(set(all_y)) < 2:
        rows.append({**base, "model_status": "folds_single_class", "folds": len(fold_rows), "split_kind": split_kind})
        return pd.DataFrame(rows), pd.DataFrame(coefs)

    oof_ap = float(average_precision_score(all_y, all_p))
    positive_rate = float(df[target].mean()) if len(df) else math.nan
    rows.append(
        {
            **base,
            "model_status": "diagnostic_only",
            "split_kind": split_kind,
            "folds": len(fold_rows),
            "oof_rows": int(len(all_y)),
            "features_used": ";".join(available_features),
            "features_missing_all": ";".join(missing_all_features),
            "oof_roc_auc": safe_metric(roc_auc_score(all_y, all_p)),
            "oof_average_precision": safe_metric(oof_ap),
            "oof_average_precision_lift": safe_metric(oof_ap / positive_rate) if positive_rate > 0 else "",
            "oof_brier": safe_metric(brier_score_loss(all_y, all_p)),
            "caveat": "small, overlapping research sample; not deployment evidence",
        }
    )

    pipe.fit(x, y)
    model = pipe.named_steps["model"]
    for feature, coef in zip(available_features, model.coef_[0], strict=False):
        coefs.append({"target": target, "feature": feature, "coefficient": round(float(coef), 6)})
    fold_df = pd.DataFrame(fold_rows)
    summary = pd.DataFrame(rows)
    if not fold_df.empty:
        summary = summary.merge(
            fold_df.groupby("target").agg(
                fold_roc_auc_mean=("roc_auc", lambda s: pd.to_numeric(s, errors="coerce").mean()),
                fold_average_precision_mean=("average_precision", lambda s: pd.to_numeric(s, errors="coerce").mean()),
                fold_brier_mean=("brier", lambda s: pd.to_numeric(s, errors="coerce").mean()),
            ),
            on="target",
            how="left",
        )
    return summary, pd.DataFrame(coefs)


def summarize_groups(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    for key, g in rows.groupby(group_cols, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        row = dict(zip(group_cols, key_tuple, strict=False))
        both = g[g["both_result_rows"]]
        row.update(
            {
                "rows": int(len(g)),
                "both_result_rows": int(len(both)),
                "mismatches": int(g["proxy_official_mismatch"].sum()),
                "adverse_mismatches": int(g["adverse_proxy_official_mismatch"].sum()),
                "mismatch_rate": round(float(g["proxy_official_mismatch"].sum() / len(both)), 4) if len(both) else 0.0,
                "adverse_mismatch_rate": round(float(g["adverse_proxy_official_mismatch"].sum() / len(both)), 4)
                if len(both)
                else 0.0,
                "official_pnl_2c": round(float(g["official_pnl_2c"].sum()), 4),
                "proxy_pnl_2c": round(float(g["proxy_pnl_2c"].sum()), 4),
                "pnl_delta_official_minus_proxy_2c": round(float(g["pnl_delta_official_minus_proxy_2c"].sum()), 4),
                "median_abs_decision_distance_bps": round(float(g["abs_decision_distance_bps"].median()), 4)
                if g["abs_decision_distance_bps"].notna().any()
                else math.nan,
                "median_entry_price": round(float(g["entry_price"].median()), 4) if g["entry_price"].notna().any() else math.nan,
            }
        )
        out.append(row)
    return pd.DataFrame(out).sort_values(group_cols).reset_index(drop=True)


def guard_verdicts(
    model_summary: pd.DataFrame,
    args: argparse.Namespace,
    safety: pd.DataFrame,
    *,
    fresh_forward_evaluation_available: bool = False,
) -> pd.DataFrame:
    unsafe_features = safety[(safety["feature_role"].eq("decision_time_model_feature")) & (~safety["live_feature_allowed"])]
    unsafe_feature_names = sorted(unsafe_features["feature"].astype(str).unique().tolist())
    rows: list[dict[str, Any]] = []
    if model_summary.empty:
        return pd.DataFrame(
            [
                {
                    "target": "",
                    "statistical_precheck_pass": False,
                    "deployable_guard_now": False,
                    "guard_blockers": "no_model_summary;no_preregistered_fresh_forward_guard_evaluation",
                }
            ]
        )
    for _, row in model_summary.iterrows():
        blockers: list[str] = []
        target = str(row.get("target", ""))
        status = str(row.get("model_status", ""))
        samples = int(row.get("samples", 0) or 0)
        positives = int(row.get("positives", 0) or 0)
        negatives = max(samples - positives, 0)
        oof_roc_auc = metric_float(row, "oof_roc_auc")
        oof_ap_lift = metric_float(row, "oof_average_precision_lift")

        if unsafe_feature_names:
            blockers.append("unsafe_decision_features:" + ",".join(unsafe_feature_names))
        if status != "diagnostic_only":
            blockers.append(f"model_status_{status or 'missing'}")
        if samples < args.min_guard_samples:
            blockers.append(f"too_few_guard_samples<{args.min_guard_samples}")
        if positives < args.min_guard_positives:
            blockers.append(f"too_few_guard_positives<{args.min_guard_positives}")
        if negatives < args.min_guard_positives:
            blockers.append(f"too_few_guard_negatives<{args.min_guard_positives}")
        if not math.isfinite(oof_roc_auc) or oof_roc_auc < args.min_guard_roc_auc:
            blockers.append(f"weak_oof_roc_auc<{args.min_guard_roc_auc}")
        if not math.isfinite(oof_ap_lift) or oof_ap_lift < args.min_guard_ap_lift:
            blockers.append(f"weak_oof_ap_lift<{args.min_guard_ap_lift}")

        statistical_precheck_pass = len(blockers) == 0
        deployment_blockers = list(blockers)
        if not fresh_forward_evaluation_available:
            deployment_blockers.append("no_preregistered_fresh_forward_guard_evaluation")
        rows.append(
            {
                "target": target,
                "model_status": status,
                "samples": samples,
                "positives": positives,
                "positive_rate": row.get("positive_rate", 0.0),
                "oof_roc_auc": row.get("oof_roc_auc", ""),
                "oof_average_precision": row.get("oof_average_precision", ""),
                "oof_average_precision_lift": row.get("oof_average_precision_lift", ""),
                "statistical_precheck_pass": statistical_precheck_pass,
                "deployable_guard_now": False,
                "guard_blockers": ";".join(dict.fromkeys(deployment_blockers)),
            }
        )
    return pd.DataFrame(rows)


def write_report(
    out_dir: Path,
    info: dict[str, Any],
    group_summary: pd.DataFrame,
    model_summary: pd.DataFrame,
    coefficients: pd.DataFrame,
    guard_verdict: pd.DataFrame,
    safety: pd.DataFrame,
) -> None:
    lines = [
        "# BTC Settlement-Basis Model Feasibility",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Rows: `{info['rows']}`; model rows after de-duplication: `{info['model_rows']}`",
        "",
        "## Group Summary",
        "",
        group_summary.fillna("").round(4).to_string(index=False) if not group_summary.empty else "_No rows._",
        "",
        "## Decision-Feature Model Feasibility",
        "",
        model_summary.fillna("").round(4).to_string(index=False) if not model_summary.empty else "_No model rows._",
        "",
        "## Basis Guard Verdict",
        "",
        guard_verdict.fillna("").round(4).to_string(index=False) if not guard_verdict.empty else "_No verdict rows._",
        "",
        "## Feature Safety Audit",
        "",
        safety.fillna("").to_string(index=False) if not safety.empty else "_No feature safety rows._",
        "",
        "## Coefficients",
        "",
        coefficients.sort_values(["target", "coefficient"], ascending=[True, False]).to_string(index=False)
        if not coefficients.empty
        else "_No coefficients; sample too small or model unavailable._",
        "",
        "## Interpretation",
        "",
        "- This is not a deployable settlement model. It tests whether a decision-time basis-danger model is supportable from the current rows.",
        "- Post-event fields such as proxy close, official distance, and realized basis are kept only for diagnostics and must not become live features.",
        "- `deployable_guard_now` is intentionally false unless a pre-registered guard survives fresh forward official-settled evaluation.",
        "- Any usable basis guard still needs pre-registration and fresh post-freeze official rows before it can affect a candidate.",
        "- Current deployment remains blocked by sample size, q250 NO-side basis fragility, q1000 sparsity, and BTC1H official/proxy disagreement.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    args = parse_args()
    validate_decision_features()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = read_csv(args.basis_risk_dir / "settlement_basis_risk_rows.csv")
    rows = prepare_rows(raw)
    model_rows = model_dataset(rows)
    safety = feature_safety_audit()
    feature_cols = [
        "family",
        "candidate",
        "source",
        "market_ticker",
        "side",
        "created_at",
        "proxy_official_mismatch",
        "adverse_proxy_official_mismatch",
        "proxy_win_official_loss",
        "official_pnl_2c",
        "proxy_pnl_2c",
        "pnl_delta_official_minus_proxy_2c",
        *DECISION_FEATURES,
        "proxy_distance_usd",
        "official_distance_usd",
        "basis_usd",
        "post_event_leakage_warning",
    ]
    rows[[col for col in feature_cols if col in rows.columns]].to_csv(args.out_dir / "basis_model_features_all_rows.csv", index=False)
    model_rows[[col for col in feature_cols if col in model_rows.columns]].to_csv(
        args.out_dir / "basis_model_features_deduped.csv",
        index=False,
    )
    group_summary = summarize_groups(rows, ["family", "candidate", "side"])
    dedup_group_summary = summarize_groups(model_rows, ["family", "side"])
    group_summary.to_csv(args.out_dir / "basis_model_group_summary.csv", index=False)
    dedup_group_summary.to_csv(args.out_dir / "basis_model_deduped_side_summary.csv", index=False)

    summaries: list[pd.DataFrame] = []
    coef_frames: list[pd.DataFrame] = []
    for target in ["proxy_official_mismatch", "adverse_proxy_official_mismatch", "proxy_win_official_loss"]:
        summary, coefs = evaluate_target(model_rows, target, args)
        summaries.append(summary)
        if not coefs.empty:
            coef_frames.append(coefs)
    model_summary = pd.concat(summaries, ignore_index=True, sort=False) if summaries else pd.DataFrame()
    coefficients = pd.concat(coef_frames, ignore_index=True, sort=False) if coef_frames else pd.DataFrame()
    guard_verdict = guard_verdicts(model_summary, args, safety)
    model_summary.to_csv(args.out_dir / "basis_model_feasibility_summary.csv", index=False)
    coefficients.to_csv(args.out_dir / "basis_model_coefficients.csv", index=False)
    guard_verdict.to_csv(args.out_dir / "basis_guard_deployment_verdict.csv", index=False)
    safety.to_csv(args.out_dir / "basis_model_feature_safety_audit.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis_risk_dir": str(args.basis_risk_dir),
        "rows": int(len(rows)),
        "model_rows": int(len(model_rows)),
        "min_samples": args.min_samples,
        "min_positives": args.min_positives,
        "min_guard_samples": args.min_guard_samples,
        "min_guard_positives": args.min_guard_positives,
        "min_guard_roc_auc": args.min_guard_roc_auc,
        "min_guard_ap_lift": args.min_guard_ap_lift,
        "decision_features": DECISION_FEATURES,
        "excluded_post_event_fields": POST_EVENT_DIAGNOSTIC_FIELDS,
        "note": "Diagnostic only. No model output should be used for trading without pre-registration and fresh forward validation.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    write_report(args.out_dir, info, group_summary, model_summary, coefficients, guard_verdict, safety)
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
