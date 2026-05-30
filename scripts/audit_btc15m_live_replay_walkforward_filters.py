from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRADES = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_full_raw_live_holdout_window_grid_20260522_0530_1300_6h"
    / "all_trades.csv"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_live_replay_walkforward_filters_latest_codex"


@dataclass(frozen=True)
class FilterSpec:
    field: str
    value: str

    @classmethod
    def parse(cls, text: str) -> "FilterSpec":
        if "=" not in text:
            raise argparse.ArgumentTypeError("filter must use field=value syntax")
        field, value = text.split("=", 1)
        field = field.strip()
        value = value.strip()
        if not field or not value:
            raise argparse.ArgumentTypeError("filter field and value must be non-empty")
        return cls(field=field, value=value)

    @property
    def label(self) -> str:
        return f"{self.field}={self.value}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Chronological walk-forward audit for simple BTC15M live-replay filters. "
            "Filters are selected on train folds only and then scored on the next fold."
        )
    )
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--strategy", default="current_lowdd_no_rv")
    parser.add_argument("--train-days", type=float, default=4.0)
    parser.add_argument("--test-hours", type=float, default=24.0)
    parser.add_argument("--step-hours", type=float, default=24.0)
    parser.add_argument("--min-train-rows", type=int, default=40)
    parser.add_argument("--min-oos-rows", type=int, default=5)
    parser.add_argument("--min-train-bootstrap-prob", type=float, default=0.90)
    parser.add_argument("--bootstrap-iters", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument("--fixed-filter", type=FilterSpec.parse, default=FilterSpec("btc3_abs_bin", "btc3_3_6bps"))
    return parser.parse_args()


def read_trades(path: Path, strategy: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "strategy" not in df.columns:
        raise ValueError(f"{path} has no strategy column")
    df = df[df["strategy"].astype(str).eq(strategy)].copy()
    if df.empty:
        raise ValueError(f"no rows found for strategy={strategy!r}")
    df["received_at_utc"] = pd.to_datetime(df["received_at_utc"], utc=True)
    for col in [
        "pnl",
        "premium",
        "entry_price",
        "visible_qty",
        "ttl_min",
        "score",
        "spread_cents",
        "btc_ret_1m_bps",
        "btc_ret_3m_bps",
        "btc_ret_15m_bps",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "win" in df.columns:
        df["win"] = df["win"].astype(str).str.lower().isin(["true", "1", "yes"])
    return df.sort_values("received_at_utc").reset_index(drop=True)


def bin_abs(series: pd.Series, edges: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series.abs(), bins=edges, labels=labels, include_lowest=True)


def bin_signed(series: pd.Series, prefix: str) -> pd.Series:
    out = pd.Series(index=series.index, dtype="object")
    out[series < 0] = f"{prefix}_down"
    out[series >= 0] = f"{prefix}_up"
    return out


def add_filter_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["hour_bin"] = pd.cut(
        out["received_at_utc"].dt.hour,
        bins=[-1, 5, 11, 17, 23],
        labels=["utc_00_05", "utc_06_11", "utc_12_17", "utc_18_23"],
    )
    out["entry_bin"] = pd.cut(
        out["entry_price"],
        bins=[0.0, 0.35, 0.55, 0.75, 1.01],
        labels=["entry_lt35", "entry_35_55", "entry_55_75", "entry_75_100"],
        include_lowest=True,
    )
    out["visible_bin"] = pd.cut(
        out["visible_qty"],
        bins=[-1.0, 100.0, 500.0, 1.0e12],
        labels=["qty_lt100", "qty_100_500", "qty_500p"],
    )
    out["ttl_bin"] = pd.cut(
        out["ttl_min"],
        bins=[0.0, 4.5, 4.8, 5.1, 8.1],
        labels=["ttl_lt4_5", "ttl_4_5_4_8", "ttl_4_8_5_1", "ttl_5_1p"],
        include_lowest=True,
    )
    out["score_bin"] = pd.cut(
        out["score"],
        bins=[-1.0e9, 0.14, 0.24, 1.0e9],
        labels=["score_lt14c", "score_14_24c", "score_24cp"],
    )
    out["btc1_abs_bin"] = bin_abs(
        out["btc_ret_1m_bps"],
        edges=[-0.1, 0.5, 1.5, 3.0, 1.0e6],
        labels=["btc1_lt0_5bps", "btc1_0_5_1_5bps", "btc1_1_5_3bps", "btc1_3bp"],
    )
    out["btc3_abs_bin"] = bin_abs(
        out["btc_ret_3m_bps"],
        edges=[-0.1, 1.0, 3.0, 6.0, 1.0e6],
        labels=["btc3_lt1bps", "btc3_1_3bps", "btc3_3_6bps", "btc3_6bp"],
    )
    out["btc15_abs_bin"] = bin_abs(
        out["btc_ret_15m_bps"],
        edges=[-0.1, 2.0, 5.0, 10.0, 1.0e6],
        labels=["btc15_lt2bps", "btc15_2_5bps", "btc15_5_10bps", "btc15_10bp"],
    )
    out["btc1_sign_bin"] = bin_signed(out["btc_ret_1m_bps"], "btc1")
    out["btc3_sign_bin"] = bin_signed(out["btc_ret_3m_bps"], "btc3")
    out["btc15_sign_bin"] = bin_signed(out["btc_ret_15m_bps"], "btc15")

    for suffix in [
        "entry_bin",
        "visible_bin",
        "ttl_bin",
        "score_bin",
        "hour_bin",
        "btc1_abs_bin",
        "btc3_abs_bin",
        "btc15_abs_bin",
        "btc1_sign_bin",
        "btc3_sign_bin",
        "btc15_sign_bin",
    ]:
        out[f"side_{suffix}"] = out["side"].astype(str) + "_" + out[suffix].astype(str)
    return out


def candidate_fields(df: pd.DataFrame) -> list[str]:
    base = [
        "side",
        "entry_bin",
        "visible_bin",
        "ttl_bin",
        "score_bin",
        "hour_bin",
        "btc1_abs_bin",
        "btc3_abs_bin",
        "btc15_abs_bin",
        "btc1_sign_bin",
        "btc3_sign_bin",
        "btc15_sign_bin",
    ]
    combos = [col for col in df.columns if col.startswith("side_") and col not in {"side"}]
    return [col for col in base + combos if col in df.columns]


def build_folds(df: pd.DataFrame, train_days: float, test_hours: float, step_hours: float) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start = df["received_at_utc"].min().floor("h")
    end = df["received_at_utc"].max().ceil("h")
    train_end = start + pd.Timedelta(days=train_days)
    folds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    while train_end < end:
        test_end = min(train_end + pd.Timedelta(hours=test_hours), end)
        if test_end > train_end:
            folds.append((train_end, test_end))
        train_end += pd.Timedelta(hours=step_hours)
    return folds


def max_drawdown(pnls: pd.Series) -> float:
    if pnls.empty:
        return 0.0
    cumulative = pnls.astype(float).cumsum()
    drawdown = cumulative - cumulative.cummax()
    return float(drawdown.min())


def bootstrap_event_pnl(df: pd.DataFrame, iters: int, rng: np.random.Generator) -> dict[str, float]:
    if df.empty:
        return {
            "event_bootstrap_p05": 0.0,
            "event_bootstrap_p50": 0.0,
            "event_bootstrap_p95": 0.0,
            "event_bootstrap_prob_profit": 0.0,
        }
    if "event_ticker" in df.columns:
        pnl = df.groupby("event_ticker", dropna=False)["pnl"].sum().to_numpy(dtype=float)
    else:
        pnl = df["pnl"].to_numpy(dtype=float)
    samples = rng.choice(pnl, size=(iters, len(pnl)), replace=True).sum(axis=1)
    return {
        "event_bootstrap_p05": float(np.quantile(samples, 0.05)),
        "event_bootstrap_p50": float(np.quantile(samples, 0.50)),
        "event_bootstrap_p95": float(np.quantile(samples, 0.95)),
        "event_bootstrap_prob_profit": float((samples > 0).mean()),
    }


def metrics(df: pd.DataFrame, iters: int, rng: np.random.Generator) -> dict[str, object]:
    ordered = df.sort_values("received_at_utc") if "received_at_utc" in df.columns else df.copy()
    rows = int(len(ordered))
    events = int(ordered["event_ticker"].nunique()) if "event_ticker" in ordered.columns else rows
    pnl = float(ordered["pnl"].sum()) if rows else 0.0
    premium = float(ordered["premium"].sum()) if rows and "premium" in ordered.columns else 0.0
    wins = float(ordered["win"].mean()) if rows and "win" in ordered.columns else 0.0
    duplicate_event_rows = int(rows - events)
    nonfinal_rows = int((ordered["status"].astype(str) != "finalized").sum()) if rows and "status" in ordered.columns else 0
    non_executable_rows = 0
    if rows and {"visible_qty", "spread_cents"}.issubset(ordered.columns):
        non_executable_rows = int(((ordered["visible_qty"] <= 0) | (ordered["spread_cents"] > 2)).sum())
    pair_lock_rows = 0
    if rows:
        side_pair = ordered["side"].astype(str).eq("pair") if "side" in ordered.columns else pd.Series(False, index=ordered.index)
        legs_pair = ordered["legs"].astype(str).str.contains("->", regex=False, na=False) if "legs" in ordered.columns else pd.Series(False, index=ordered.index)
        lock_profit = ordered["lock_profit"].notna() if "lock_profit" in ordered.columns else pd.Series(False, index=ordered.index)
        pair_lock_rows = int((side_pair | legs_pair | lock_profit).sum())
    blockers = []
    if duplicate_event_rows:
        blockers.append("duplicate_event_rows")
    if nonfinal_rows:
        blockers.append("nonfinal_rows")
    if non_executable_rows:
        blockers.append("non_executable_or_wide_quote_rows")
    if pair_lock_rows:
        blockers.append("pair_lock_or_position_aware_rows")
    out: dict[str, object] = {
        "rows": rows,
        "events": events,
        "duplicate_event_rows": duplicate_event_rows,
        "nonfinal_rows": nonfinal_rows,
        "non_executable_or_wide_quote_rows": non_executable_rows,
        "pair_lock_rows": pair_lock_rows,
        "row_quality_blockers": ";".join(blockers),
        "pnl": pnl,
        "premium": premium,
        "return_on_premium": float(pnl / premium) if premium else 0.0,
        "win_rate": wins,
        "max_dd": max_drawdown(ordered["pnl"]) if rows else 0.0,
        "first_entry": ordered["received_at_utc"].min().isoformat() if rows and "received_at_utc" in ordered.columns else "",
        "last_entry": ordered["received_at_utc"].max().isoformat() if rows and "received_at_utc" in ordered.columns else "",
    }
    out.update(bootstrap_event_pnl(ordered, iters=iters, rng=rng))
    return out


def prefixed(prefix: str, values: dict[str, object]) -> dict[str, object]:
    return {f"{prefix}_{key}": value for key, value in values.items()}


def apply_filter(df: pd.DataFrame, spec: FilterSpec) -> pd.DataFrame:
    if spec.field not in df.columns:
        raise ValueError(f"filter field {spec.field!r} is not present in trades")
    return df[df[spec.field].astype(str).eq(spec.value)].copy()


def iter_candidate_filters(df: pd.DataFrame, fields: Iterable[str]) -> Iterable[tuple[str, str, pd.DataFrame]]:
    for field in fields:
        series = df[field].dropna()
        for value in sorted(series.astype(str).unique()):
            if value == "nan":
                continue
            yield field, value, df[df[field].astype(str).eq(value)].copy()


def select_best_candidate(
    candidates: pd.DataFrame,
    min_train_rows: int,
    min_train_prob: float,
) -> pd.Series | None:
    if candidates.empty:
        return None
    eligible = candidates[
        (candidates["train_rows"] >= min_train_rows)
        & (candidates["train_event_bootstrap_p05"] > 0)
        & (candidates["train_event_bootstrap_prob_profit"] >= min_train_prob)
    ].copy()
    if eligible.empty:
        return None
    eligible = eligible.sort_values(
        ["train_event_bootstrap_p05", "train_pnl", "train_rows"],
        ascending=[False, False, False],
    )
    return eligible.iloc[0]


def quality_summary(df: pd.DataFrame) -> dict[str, object]:
    duplicate_event_rows = int(len(df) - df["event_ticker"].nunique()) if "event_ticker" in df.columns else 0
    nonfinal_rows = int((df["status"].astype(str) != "finalized").sum()) if "status" in df.columns else 0
    non_executable_rows = 0
    if {"visible_qty", "spread_cents"}.issubset(df.columns):
        non_executable_rows = int(((df["visible_qty"] <= 0) | (df["spread_cents"] > 2)).sum())
    pair_lock_rows = 0
    if not df.empty:
        side_pair = df["side"].astype(str).eq("pair") if "side" in df.columns else pd.Series(False, index=df.index)
        legs_pair = df["legs"].astype(str).str.contains("->", regex=False, na=False) if "legs" in df.columns else pd.Series(False, index=df.index)
        lock_profit = df["lock_profit"].notna() if "lock_profit" in df.columns else pd.Series(False, index=df.index)
        pair_lock_rows = int((side_pair | legs_pair | lock_profit).sum())
    blockers = []
    if duplicate_event_rows:
        blockers.append("duplicate_event_rows")
    if nonfinal_rows:
        blockers.append("nonfinal_rows")
    if non_executable_rows:
        blockers.append("non_executable_or_wide_quote_rows")
    if pair_lock_rows:
        blockers.append("pair_lock_or_position_aware_rows")
    return {
        "rows": int(len(df)),
        "events": int(df["event_ticker"].nunique()) if "event_ticker" in df.columns else int(len(df)),
        "duplicate_event_rows": duplicate_event_rows,
        "nonfinal_rows": nonfinal_rows,
        "non_executable_or_wide_quote_rows": non_executable_rows,
        "pair_lock_rows": pair_lock_rows,
        "row_quality_blockers": ";".join(blockers),
        "min_visible_qty": float(df["visible_qty"].min()) if "visible_qty" in df.columns else None,
        "max_spread_cents": float(df["spread_cents"].max()) if "spread_cents" in df.columns else None,
    }


def markdown_table(rows: list[dict[str, object]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        values = []
        for col in columns:
            value = row.get(col, "")
            if isinstance(value, float):
                value = f"{value:.6g}"
            values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_report(
    out_dir: Path,
    args: argparse.Namespace,
    quality: dict[str, object],
    aggregate: pd.DataFrame,
    fold_summary: pd.DataFrame,
    candidate_screen: pd.DataFrame,
) -> None:
    selected = fold_summary[fold_summary["adaptive_filter"].astype(str).ne("")]
    fixed = aggregate[aggregate["mode"].eq("fixed_filter_oos")].to_dict("records")
    adaptive = aggregate[aggregate["mode"].eq("adaptive_selected_oos")].to_dict("records")
    baseline = aggregate[aggregate["mode"].eq("baseline_oos")].to_dict("records")
    top_candidates = candidate_screen.sort_values(
        ["fold_id", "train_event_bootstrap_p05", "train_pnl"],
        ascending=[True, False, False],
    ).groupby("fold_id", as_index=False).head(3)
    top_rows = top_candidates[
        [
            "fold_id",
            "filter",
            "train_rows",
            "train_pnl",
            "train_event_bootstrap_p05",
            "train_event_bootstrap_prob_profit",
            "test_rows",
            "test_pnl",
        ]
    ].to_dict("records")
    lines = [
        "# BTC15M Live Replay Walk-Forward Filter Audit",
        "",
        f"Trades: `{args.trades}`",
        "",
        f"Strategy: `{args.strategy}`",
        "",
        f"Fixed filter: `{args.fixed_filter.label}`",
        "",
        f"Train window: `{args.train_days}` days; test window: `{args.test_hours}` hours; step: `{args.step_hours}` hours.",
        "",
        f"Selection gate: train rows >= `{args.min_train_rows}`, train event-bootstrap p05 > `0`, train profit probability >= `{args.min_train_bootstrap_prob}`.",
        "",
        "## Input Quality",
        "",
        markdown_table([quality], list(quality.keys())),
        "",
        "## Out-of-Sample Summary",
        "",
        markdown_table(
            aggregate.to_dict("records"),
            [
                "mode",
                "rows",
                "events",
                "pnl",
                "premium",
                "return_on_premium",
                "win_rate",
                "max_dd",
                "event_bootstrap_p05",
                "event_bootstrap_prob_profit",
                "row_quality_blockers",
                "folds_with_rows",
            ],
        ),
        "",
        "## Fold Selection",
        "",
        markdown_table(
            fold_summary[
                [
                    "fold_id",
                    "test_start_utc",
                    "test_end_utc",
                    "baseline_test_rows",
                    "baseline_test_pnl",
                    "adaptive_filter",
                    "adaptive_test_rows",
                    "adaptive_test_pnl",
                    "fixed_test_rows",
                    "fixed_test_pnl",
                ]
            ].to_dict("records"),
            [
                "fold_id",
                "test_start_utc",
                "test_end_utc",
                "baseline_test_rows",
                "baseline_test_pnl",
                "adaptive_filter",
                "adaptive_test_rows",
                "adaptive_test_pnl",
                "fixed_test_rows",
                "fixed_test_pnl",
            ],
        ),
        "",
        "## Top Train Candidates Per Fold",
        "",
        markdown_table(
            top_rows,
            [
                "fold_id",
                "filter",
                "train_rows",
                "train_pnl",
                "train_event_bootstrap_p05",
                "train_event_bootstrap_prob_profit",
                "test_rows",
                "test_pnl",
            ],
        ),
        "",
        "## Verdict",
        "",
    ]
    adaptive_row = adaptive[0] if adaptive else {}
    fixed_row = fixed[0] if fixed else {}
    baseline_row = baseline[0] if baseline else {}
    verdict = "No filter is promotion-ready."
    reasons = []
    if adaptive_row:
        if float(adaptive_row.get("event_bootstrap_p05", 0.0)) <= 0:
            reasons.append("adaptive train-selected filters did not produce positive OOS bootstrap lower tail")
        if float(adaptive_row.get("pnl", 0.0)) <= 0:
            reasons.append("adaptive train-selected filters did not produce positive OOS PnL")
        if str(adaptive_row.get("row_quality_blockers", "")):
            reasons.append(f"adaptive train-selected rows have quality blockers: {adaptive_row.get('row_quality_blockers', '')}")
    if fixed_row and float(fixed_row.get("event_bootstrap_p05", 0.0)) <= 0:
        reasons.append("fixed filter OOS bootstrap lower tail remains non-positive")
    if fixed_row and str(fixed_row.get("row_quality_blockers", "")):
        reasons.append(f"fixed filter rows have quality blockers: {fixed_row.get('row_quality_blockers', '')}")
    if baseline_row and float(baseline_row.get("event_bootstrap_p05", 0.0)) <= 0:
        reasons.append("baseline OOS bootstrap lower tail remains non-positive")
    if baseline_row and str(baseline_row.get("row_quality_blockers", "")):
        reasons.append(f"baseline rows have quality blockers: {baseline_row.get('row_quality_blockers', '')}")
    if selected.empty:
        reasons.append("no folds selected an eligible adaptive filter")
    lines.append(verdict)
    if reasons:
        lines.append("")
        for reason in reasons:
            lines.append(f"- {reason}.")
    lines.append("")
    lines.append(
        "Use any positive rows here only as preregistration guidance for future forward metrics; they are not deployment evidence."
    )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.trades = args.trades.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    df = add_filter_columns(read_trades(args.trades, args.strategy))
    fields = candidate_fields(df)
    folds = build_folds(df, args.train_days, args.test_hours, args.step_hours)
    if not folds:
        raise ValueError("not enough history to build walk-forward folds")

    fold_rows: list[dict[str, object]] = []
    candidate_rows: list[dict[str, object]] = []
    adaptive_test_frames: list[pd.DataFrame] = []
    fixed_test_frames: list[pd.DataFrame] = []
    baseline_test_frames: list[pd.DataFrame] = []

    for fold_id, (test_start, test_end) in enumerate(folds, start=1):
        train = df[df["received_at_utc"] < test_start].copy()
        test = df[(df["received_at_utc"] >= test_start) & (df["received_at_utc"] < test_end)].copy()
        baseline_test_frames.append(test.assign(walkforward_mode="baseline_oos", fold_id=fold_id))
        baseline_train_metrics = metrics(train, args.bootstrap_iters, rng)
        baseline_test_metrics = metrics(test, args.bootstrap_iters, rng)

        for field, value, train_filtered in iter_candidate_filters(train, fields):
            test_filtered = test[test[field].astype(str).eq(value)].copy()
            row = {
                "fold_id": fold_id,
                "test_start_utc": test_start.isoformat(),
                "test_end_utc": test_end.isoformat(),
                "field": field,
                "value": value,
                "filter": f"{field}={value}",
            }
            row.update(prefixed("train", metrics(train_filtered, args.bootstrap_iters, rng)))
            row.update(prefixed("test", metrics(test_filtered, args.bootstrap_iters, rng)))
            row["passes_train_gate"] = bool(
                row["train_rows"] >= args.min_train_rows
                and row["train_event_bootstrap_p05"] > 0
                and row["train_event_bootstrap_prob_profit"] >= args.min_train_bootstrap_prob
            )
            row["test_min_rows_ok"] = bool(row["test_rows"] >= args.min_oos_rows)
            candidate_rows.append(row)

        candidates = pd.DataFrame(candidate_rows)
        fold_candidates = candidates[candidates["fold_id"].eq(fold_id)].copy()
        selected = select_best_candidate(
            fold_candidates,
            min_train_rows=args.min_train_rows,
            min_train_prob=args.min_train_bootstrap_prob,
        )
        if selected is None:
            adaptive_filter = ""
            adaptive_test = test.iloc[0:0].copy()
        else:
            adaptive_filter = str(selected["filter"])
            adaptive_spec = FilterSpec(field=str(selected["field"]), value=str(selected["value"]))
            adaptive_test = apply_filter(test, adaptive_spec)
            adaptive_test_frames.append(
                adaptive_test.assign(walkforward_mode="adaptive_selected_oos", fold_id=fold_id, selected_filter=adaptive_filter)
            )

        fixed_train = apply_filter(train, args.fixed_filter)
        fixed_test = apply_filter(test, args.fixed_filter)
        fixed_test_frames.append(
            fixed_test.assign(walkforward_mode="fixed_filter_oos", fold_id=fold_id, selected_filter=args.fixed_filter.label)
        )

        fold_row = {
            "fold_id": fold_id,
            "test_start_utc": test_start.isoformat(),
            "test_end_utc": test_end.isoformat(),
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "adaptive_filter": adaptive_filter,
        }
        fold_row.update(prefixed("baseline_train", baseline_train_metrics))
        fold_row.update(prefixed("baseline_test", baseline_test_metrics))
        fold_row.update(prefixed("adaptive_test", metrics(adaptive_test, args.bootstrap_iters, rng)))
        fold_row.update(prefixed("fixed_train", metrics(fixed_train, args.bootstrap_iters, rng)))
        fold_row.update(prefixed("fixed_test", metrics(fixed_test, args.bootstrap_iters, rng)))
        fold_rows.append(fold_row)

    fold_summary = pd.DataFrame(fold_rows)
    candidate_screen = pd.DataFrame(candidate_rows)

    aggregate_rows: list[dict[str, object]] = []
    mode_frames = {
        "baseline_oos": baseline_test_frames,
        "adaptive_selected_oos": adaptive_test_frames,
        "fixed_filter_oos": fixed_test_frames,
    }
    for mode, frames in mode_frames.items():
        mode_df = pd.concat(frames, ignore_index=True) if frames else df.iloc[0:0].copy()
        row = {"mode": mode, "folds_with_rows": int(mode_df["fold_id"].nunique()) if "fold_id" in mode_df.columns and not mode_df.empty else 0}
        row.update(metrics(mode_df, args.bootstrap_iters, rng))
        aggregate_rows.append(row)
        if not mode_df.empty:
            mode_df.to_csv(args.out_dir / f"{mode}_trades.csv", index=False)

    aggregate = pd.DataFrame(aggregate_rows)
    quality = quality_summary(df)

    fold_summary.to_csv(args.out_dir / "fold_summary.csv", index=False)
    candidate_screen.to_csv(args.out_dir / "candidate_screen.csv", index=False)
    aggregate.to_csv(args.out_dir / "aggregate_summary.csv", index=False)
    df.to_csv(args.out_dir / "trades_with_filter_bins.csv", index=False)
    manifest = {
        "trades": str(args.trades),
        "out_dir": str(args.out_dir.resolve()),
        "strategy": args.strategy,
        "fixed_filter": args.fixed_filter.label,
        "train_days": args.train_days,
        "test_hours": args.test_hours,
        "step_hours": args.step_hours,
        "min_train_rows": args.min_train_rows,
        "min_oos_rows": args.min_oos_rows,
        "min_train_bootstrap_prob": args.min_train_bootstrap_prob,
        "bootstrap_iters": args.bootstrap_iters,
        "seed": args.seed,
        "candidate_fields": fields,
        "fold_count": len(folds),
        "input_quality": quality,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(args.out_dir, args, quality, aggregate, fold_summary, candidate_screen)
    print(json.dumps({"out_dir": str(args.out_dir), "folds": len(folds), "rows": int(len(df))}, indent=2))


if __name__ == "__main__":
    main()
