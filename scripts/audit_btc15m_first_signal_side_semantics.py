#!/usr/bin/env python3
"""Compare global-first and side-filtered BTC15M first-signal semantics.

The q250 YES-only forward path is intentionally a fresh candidate. This audit
keeps that distinction explicit by checking whether a side-filtered replay is a
strict subset of the global both-side first-signal replay, or whether it creates
extra rows by waiting past an earlier global first signal.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_first_signal_side_semantics_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_FULL_GLOBAL = BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex"
DEFAULT_FULL_SIDE = BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex"
DEFAULT_POSTFREEZE_GLOBAL = BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex"
DEFAULT_POSTFREEZE_SIDE = BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex"
COMPARABLE_CONFIG_KEYS = (
    "fair_p_min",
    "edge_cents_min",
    "ttl_min",
    "ttl_max",
    "spread_max_cents",
    "entry_min",
    "entry_max",
    "visible_qty_min",
    "first_signal_visible_qty_min",
    "max_btc_spot_age_sec",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit BTC15M first-signal side-filter semantics.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--global-dir",
        type=Path,
        default=DEFAULT_FULL_GLOBAL,
        help="Directory or file for the global both-side first-signal replay.",
    )
    p.add_argument(
        "--side-dir",
        type=Path,
        default=DEFAULT_FULL_SIDE,
        help="Directory or file for the side-filtered first-signal replay.",
    )
    p.add_argument("--target-side", choices=["yes", "no"], default="yes")
    p.add_argument("--global-label", default="q250_firstskip_qty500_global_first")
    p.add_argument("--side-label", default="q250_firstskip_qty500_yes_side_first")
    p.add_argument(
        "--single",
        action="store_true",
        help="Only compare --global-dir and --side-dir. Defaults to the full replay plus post-freeze suite.",
    )
    return p.parse_args()


def project_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def resolve_trade_file(path: Path) -> Path:
    if path.is_file():
        return path
    for name in ("live_ws_trades_rest_official.parquet", "f2_live_ws_trades.parquet", "live_ws_trades_rest_official.csv"):
        candidate = path / name
        if candidate.exists():
            return candidate
    return path / "live_ws_trades_rest_official.parquet"


def read_trades(path: Path) -> pd.DataFrame:
    file_path = resolve_trade_file(path)
    if not file_path.exists():
        return pd.DataFrame()
    if file_path.suffix.lower() == ".parquet":
        return pd.read_parquet(file_path)
    return pd.read_csv(file_path)


def read_run_info(path: Path) -> dict[str, Any]:
    if path.is_file():
        info_path = path.parent / "run_info.json"
    else:
        info_path = path / "run_info.json"
    if not info_path.exists():
        return {}
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if any(key in info for key in COMPARABLE_CONFIG_KEYS):
        return info
    input_trades = info.get("input_trades")
    if isinstance(input_trades, str) and input_trades:
        replay_trade_path = Path(input_trades)
        if not replay_trade_path.is_absolute():
            replay_trade_path = PROJECT_ROOT / replay_trade_path
        replay_info_path = replay_trade_path.parent / "run_info.json"
        if replay_info_path.exists():
            try:
                replay_info = json.loads(replay_info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                replay_info = {}
            if replay_info:
                merged = dict(info)
                merged.update(replay_info)
                return merged
    return info


def comparable_config(global_info: dict[str, Any], side_info: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    mismatched: list[str] = []
    for key in COMPARABLE_CONFIG_KEYS:
        if key not in global_info or key not in side_info:
            missing.append(key)
            continue
        g = global_info.get(key)
        s = side_info.get(key)
        try:
            g_float = float(g)
            s_float = float(s)
        except (TypeError, ValueError):
            if str(g).strip().lower() != str(s).strip().lower():
                mismatched.append(key)
        else:
            if abs(g_float - s_float) > 1e-9:
                mismatched.append(key)
    return {
        "config_comparable": not missing and not mismatched,
        "config_missing_keys": ";".join(missing),
        "config_mismatch_keys": ";".join(mismatched),
    }


def numeric_sum(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns or df.empty:
        return 0.0
    return round(float(pd.to_numeric(df[column], errors="coerce").fillna(0.0).sum()), 4)


def first_by_event(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy()
    if "received_at_ns" in work.columns:
        work["_sort_received_at_ns"] = pd.to_numeric(work["received_at_ns"], errors="coerce")
    else:
        work["_sort_received_at_ns"] = range(len(work))
    work = work.sort_values(["event_ticker", "_sort_received_at_ns"]).drop_duplicates("event_ticker", keep="first")
    return work.drop(columns=["_sort_received_at_ns"], errors="ignore").reset_index(drop=True)


def compare(global_df: pd.DataFrame, side_df: pd.DataFrame, target_side: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    global_first = first_by_event(global_df)
    side_first = first_by_event(side_df)
    if global_first.empty:
        global_first = pd.DataFrame(columns=["event_ticker", "side"])
    if side_first.empty:
        side_first = pd.DataFrame(columns=["event_ticker", "side"])

    g_cols = [c for c in ["event_ticker", "market_ticker", "received_at_utc", "side", "entry_price", "visible_qty", "official_result_filled", "proxy_result", "pnl_official_rest_2c", "pnl_proxy_2c"] if c in global_first.columns]
    s_cols = [c for c in ["event_ticker", "market_ticker", "received_at_utc", "side", "entry_price", "visible_qty", "official_result_filled", "proxy_result", "pnl_official_rest_2c", "pnl_proxy_2c"] if c in side_first.columns]
    merged = global_first[g_cols].merge(
        side_first[s_cols],
        on="event_ticker",
        how="outer",
        suffixes=("_global", "_side_filtered"),
        indicator=True,
    )
    if "side_global" not in merged.columns and "side" in merged.columns:
        merged = merged.rename(columns={"side": "side_global"})
    if "side_side_filtered" not in merged.columns:
        merged["side_side_filtered"] = ""
    if "side_global" not in merged.columns:
        merged["side_global"] = ""

    merged["side_global"] = merged["side_global"].fillna("").astype(str).str.lower()
    merged["side_side_filtered"] = merged["side_side_filtered"].fillna("").astype(str).str.lower()

    def classify(row: pd.Series) -> str:
        if row["_merge"] == "left_only":
            if row["side_global"] == target_side:
                return "global_target_side_missing_from_side_replay"
            return "global_first_only"
        if row["_merge"] == "right_only":
            return "side_filtered_extra_event"
        if row["side_global"] == target_side and row["side_side_filtered"] == target_side:
            return "same_event_same_target_side"
        if row["side_global"] != target_side and row["side_side_filtered"] == target_side:
            return "side_filtered_after_opposite_global_first"
        return "same_event_side_disagreement"

    merged["semantics_class"] = merged.apply(classify, axis=1)
    side_extra = merged["semantics_class"].isin(
        ["side_filtered_extra_event", "side_filtered_after_opposite_global_first"]
    )
    side_agree = merged["semantics_class"].eq("same_event_same_target_side")
    global_opp = merged["semantics_class"].eq("global_first_only") & ~merged["side_global"].eq(target_side)
    missing_target_side = merged["semantics_class"].eq("global_target_side_missing_from_side_replay")
    status = "SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST" if int(side_extra.sum()) == 0 else "SIDE_FILTER_DRIFTS_FROM_GLOBAL_FIRST"

    if int(side_extra.sum()) > 0:
        continuity_note = "side-filtered replay added events or waited past opposite-side global first signals"
    elif int(missing_target_side.sum()) > 0:
        continuity_note = "side-filtered replay did not add events, but it is a strict subset missing target-side global-first rows"
    else:
        continuity_note = "side-filtered replay did not add events beyond the current global-first replay"

    summary = {
        "audit_status": status,
        "target_side": target_side,
        "global_first_rows": int(len(global_first)),
        "side_filtered_rows": int(len(side_first)),
        "side_events_agree_with_global_first": int(side_agree.sum()),
        "side_first_extra_events": int(side_extra.sum()),
        "global_first_opposite_side_events": int(global_opp.sum()),
        "global_target_side_missing_from_side_replay": int(missing_target_side.sum()),
        "global_first_target_side_events": int(global_first["side"].astype(str).str.lower().eq(target_side).sum())
        if "side" in global_first.columns
        else 0,
        "global_official_pnl_2c": numeric_sum(global_first, "pnl_official_rest_2c"),
        "side_filtered_official_pnl_2c": numeric_sum(side_first, "pnl_official_rest_2c"),
        "global_proxy_pnl_2c": numeric_sum(global_first, "pnl_proxy_2c"),
        "side_filtered_proxy_pnl_2c": numeric_sum(side_first, "pnl_proxy_2c"),
        "continuity_note": continuity_note,
    }
    return merged.sort_values(["event_ticker"]).reset_index(drop=True), summary


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    text = df.fillna("").astype(str)
    headers = list(text.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in text.iterrows():
        cells = [str(row[col]).replace("|", "\\|") for col in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def report_text(summaries: pd.DataFrame, info: dict[str, Any], details: pd.DataFrame) -> str:
    class_counts = (
        details.groupby(["comparison", "semantics_class"], dropna=False)
        .size()
        .reset_index(name="rows")
        if "semantics_class" in details.columns
        else pd.DataFrame()
    )
    any_drift = summaries["audit_status"].astype(str).eq("SIDE_FILTER_DRIFTS_FROM_GLOBAL_FIRST").any()
    lines = [
        "# BTC15M First-Signal Side Semantics Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
    ]
    if not any_drift:
        lines.append(
            "- The side-filtered replay is a subset of the checked global-first replays; it did not create extra events by waiting past a global first signal in these artifacts."
        )
    else:
        lines.append(
            "- CAUTION: at least one side-filtered replay creates extra events or waits past an earlier opposite-side global first signal. Treat that row set as a newer policy with its own forward evidence clock."
        )
    lines.extend(
        [
            "- This audit is diagnostic only. It does not make old replay rows promotion evidence.",
            "",
            "## Summary",
            "",
            markdown_table(summaries),
            "",
            "## Event Classes",
            "",
            markdown_table(class_counts),
            "",
            "## Inputs",
            "",
            markdown_table(pd.DataFrame(info["comparisons"])),
            "",
        ]
    )
    return "\n".join(lines)


def comparison_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.single:
        return [
            {
                "comparison": "custom",
                "global_dir": args.global_dir,
                "side_dir": args.side_dir,
                "global_label": args.global_label,
                "side_label": args.side_label,
            }
        ]
    return [
        {
            "comparison": "full_old_replay",
            "global_dir": DEFAULT_FULL_GLOBAL,
            "side_dir": DEFAULT_FULL_SIDE,
            "global_label": "q250_firstskip_qty500_global_first_full",
            "side_label": "q250_firstskip_qty500_yes_side_first_full",
        },
        {
            "comparison": "postfreeze_replay",
            "global_dir": DEFAULT_POSTFREEZE_GLOBAL,
            "side_dir": DEFAULT_POSTFREEZE_SIDE,
            "global_label": "q250_firstskip_qty500_global_first_postfreeze",
            "side_label": "q250_firstskip_qty500_yes_side_first_postfreeze",
        },
    ]


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    detail_rows: list[pd.DataFrame] = []
    input_rows: list[dict[str, Any]] = []
    for spec in comparison_specs(args):
        global_df = read_trades(spec["global_dir"])
        side_df = read_trades(spec["side_dir"])
        global_info = read_run_info(spec["global_dir"])
        side_info = read_run_info(spec["side_dir"])
        details, summary = compare(global_df, side_df, args.target_side)
        summary.update(comparable_config(global_info, side_info))
        summary.update(
            {
                "comparison": spec["comparison"],
                "global_label": spec["global_label"],
                "side_label": spec["side_label"],
                "global_input": project_path(resolve_trade_file(spec["global_dir"])),
                "side_input": project_path(resolve_trade_file(spec["side_dir"])),
            }
        )
        details.insert(0, "comparison", spec["comparison"])
        summary_rows.append(summary)
        detail_rows.append(details)
        input_rows.append(
            {
                "comparison": spec["comparison"],
                "global_input": summary["global_input"],
                "side_input": summary["side_input"],
            }
        )
    summaries = pd.DataFrame(summary_rows)
    details = pd.concat(detail_rows, ignore_index=True) if detail_rows else pd.DataFrame()
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_side": args.target_side,
        "comparisons": input_rows,
        "note": "Diagnostic only; compares policy semantics and does not authorize deployment or count old rows for promotion.",
    }
    summaries.to_csv(args.out_dir / "first_signal_side_semantics_summary.csv", index=False)
    details.to_csv(args.out_dir / "first_signal_side_semantics_details.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (args.out_dir / "report.md").write_text(report_text(summaries, info, details), encoding="utf-8")
    print(summaries.to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
