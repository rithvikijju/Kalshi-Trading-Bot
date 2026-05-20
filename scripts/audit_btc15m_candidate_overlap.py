#!/usr/bin/env python3
"""Audit row-level overlap among refreshed BTC15M live-replay candidates.

This is a read-only research diagnostic. It helps prevent double-counting tiny
official samples from highly overlapping q250 YES-only and q1000 YES replays.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_candidate_overlap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

SOURCES = {
    "full_causal": {
        "q250_firstskip_qty500": BACKTEST_ROOT
        / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex"
        / "live_ws_trades_rest_official.csv",
        "q250_firstskip_qty500_yes": BACKTEST_ROOT
        / "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex"
        / "live_ws_trades_rest_official.csv",
        "q1000_yes": BACKTEST_ROOT
        / "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex"
        / "live_ws_trades_rest_official.csv",
    },
    "postfreeze": {
        "q250_firstskip_qty500": BACKTEST_ROOT
        / "btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex"
        / "live_ws_trades_rest_official.csv",
        "q250_firstskip_qty500_yes": BACKTEST_ROOT
        / "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex"
        / "live_ws_trades_rest_official.csv",
        "q1000_yes": BACKTEST_ROOT
        / "btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex"
        / "live_ws_trades_rest_official.csv",
    },
}

DETAIL_COLS = [
    "source",
    "event_ticker",
    "market_ticker",
    "received_at_utc",
    "close_time",
    "side",
    "entry_price",
    "visible_qty",
    "side_fair_p",
    "fair_edge_cents",
    "official_result_filled",
    "proxy_result",
    "pnl_official_rest_2c",
    "pnl_proxy_2c",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BTC15M candidate overlap.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def num_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    out = str(value)
    if out.lower() in {"nan", "none", "<na>"}:
        return ""
    return out


def official_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    result = df.get("official_result_filled", pd.Series("", index=df.index)).fillna("").astype(str)
    pnl = num_series(df, "pnl_official_rest_2c")
    return result.ne("") & pnl.notna()


def event_set(df: pd.DataFrame, *, official_only: bool = False) -> set[str]:
    if df.empty or "event_ticker" not in df.columns:
        return set()
    work = df.loc[official_mask(df)] if official_only else df
    return set(work["event_ticker"].dropna().astype(str))


def load_candidates() -> dict[str, dict[str, pd.DataFrame]]:
    loaded: dict[str, dict[str, pd.DataFrame]] = {}
    for source, paths in SOURCES.items():
        loaded[source] = {}
        for candidate, path in paths.items():
            df = read_csv(path)
            if not df.empty:
                df = df.copy()
                df["source"] = source
                df["candidate"] = candidate
            loaded[source][candidate] = df
    return loaded


def candidate_summaries(loaded: dict[str, dict[str, pd.DataFrame]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source, by_candidate in loaded.items():
        for candidate, df in by_candidate.items():
            official = official_mask(df)
            official_rows = df.loc[official]
            pnl = num_series(official_rows, "pnl_official_rest_2c")
            wins = num_series(official_rows, "win_pnl_official_rest_2c")
            proxy_result = df.get("proxy_result", pd.Series("", index=df.index)).fillna("").astype(str)
            official_result = df.get("official_result_filled", pd.Series("", index=df.index)).fillna("").astype(str)
            both = proxy_result.ne("") & official_result.ne("")
            mismatches = both & proxy_result.ne(official_result)
            side = df.get("side", pd.Series("", index=df.index)).fillna("").astype(str)
            rows.append(
                {
                    "source": source,
                    "candidate": candidate,
                    "rows": len(df),
                    "unique_events": len(event_set(df)),
                    "duplicate_event_rows": max(0, len(df) - len(event_set(df))),
                    "yes_rows": int(side.eq("yes").sum()),
                    "no_rows": int(side.eq("no").sum()),
                    "official_rows": len(official_rows),
                    "official_unique_events": len(event_set(df, official_only=True)),
                    "official_pnl_2c": float(pnl.fillna(0).sum()) if len(pnl) else 0.0,
                    "official_win_rate": float(wins.mean()) if len(wins.dropna()) else 0.0,
                    "proxy_official_both": int(both.sum()),
                    "proxy_official_mismatches": int(mismatches.sum()),
                    "deployability_meaning": "diagnostic_only_overlap_audit_not_promotion_evidence",
                }
            )
    return pd.DataFrame(rows)


def pairwise_summaries(loaded: dict[str, dict[str, pd.DataFrame]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source, by_candidate in loaded.items():
        candidates = list(by_candidate)
        for left in candidates:
            for right in candidates:
                if left == right:
                    continue
                left_df = by_candidate[left]
                right_df = by_candidate[right]
                left_events = event_set(left_df, official_only=True)
                right_events = event_set(right_df, official_only=True)
                shared = left_events & right_events
                left_only = left_events - right_events
                right_only = right_events - left_events
                union = left_events | right_events
                overlap_rate = len(shared) / len(union) if union else 0.0
                status = (
                    "identical_official_event_set"
                    if left_events == right_events and left_events
                    else "right_subset_of_left"
                    if right_events and right_events.issubset(left_events)
                    else "left_subset_of_right"
                    if left_events and left_events.issubset(right_events)
                    else "partial_overlap"
                    if shared
                    else "no_official_overlap"
                )
                rows.append(
                    {
                        "source": source,
                        "left_candidate": left,
                        "right_candidate": right,
                        "left_official_events": len(left_events),
                        "right_official_events": len(right_events),
                        "shared_official_events": len(shared),
                        "left_only_official_events": len(left_only),
                        "right_only_official_events": len(right_only),
                        "official_event_overlap_rate": round(overlap_rate, 6),
                        "overlap_status": status,
                        "shared_events": ";".join(sorted(shared)),
                        "left_only_events": ";".join(sorted(left_only)),
                        "right_only_events": ";".join(sorted(right_only)),
                    }
                )
    return pd.DataFrame(rows)


def membership_details(loaded: dict[str, dict[str, pd.DataFrame]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source, by_candidate in loaded.items():
        all_events = sorted(set().union(*(event_set(df, official_only=True) for df in by_candidate.values())))
        for event in all_events:
            row: dict[str, Any] = {"source": source, "event_ticker": event}
            for candidate, df in by_candidate.items():
                work = df[df.get("event_ticker", pd.Series("", index=df.index)).astype(str).eq(event)].copy()
                work = work.loc[official_mask(work)] if not work.empty else work
                row[f"in_{candidate}"] = not work.empty
                if not work.empty:
                    first = work.iloc[0]
                    for col in DETAIL_COLS:
                        if col in first.index:
                            row[f"{candidate}_{col}"] = first[col]
            rows.append(row)
    return pd.DataFrame(rows)


def report_text(run_info: dict[str, Any], candidates: pd.DataFrame, pairwise: pd.DataFrame) -> str:
    q250_yes_vs_q1000 = pairwise[
        (pairwise["source"].eq("full_causal"))
        & (pairwise["left_candidate"].eq("q250_firstskip_qty500_yes"))
        & (pairwise["right_candidate"].eq("q1000_yes"))
    ]
    headline = "No q250 YES-only vs q1000 YES comparison was available."
    if not q250_yes_vs_q1000.empty:
        row = q250_yes_vs_q1000.iloc[0]
        headline = (
            f"Full-causal q250 YES-only vs q1000 YES official event overlap: "
            f"{row['shared_official_events']}/{row['left_official_events']} left events and "
            f"{row['shared_official_events']}/{row['right_official_events']} right events "
            f"({row['overlap_status']})."
        )
    return "\n".join(
        [
            "# BTC15M Candidate Overlap Audit",
            "",
            f"Created UTC: `{run_info['created_at_utc']}`",
            "",
            "## Headline",
            "",
            headline,
            "",
            "This is diagnostic only. Overlapping replay rows do not create independent promotion evidence.",
            "",
            "## Candidate Summary",
            "",
            candidates.fillna("").to_string(index=False),
            "",
            "## Pairwise Official Event Overlap",
            "",
            pairwise.drop(columns=["shared_events", "left_only_events", "right_only_events"], errors="ignore")
            .fillna("")
            .to_string(index=False),
            "",
            "## Run Info",
            "",
            "```json",
            json.dumps(run_info, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    loaded = load_candidates()
    candidates = candidate_summaries(loaded)
    pairwise = pairwise_summaries(loaded)
    membership = membership_details(loaded)
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": list(SOURCES),
        "candidates": sorted({candidate for paths in SOURCES.values() for candidate in paths}),
        "note": "Read-only overlap audit. It does not start processes, tune thresholds, trade, or deploy.",
    }
    candidates.to_csv(args.out_dir / "candidate_overlap_summary.csv", index=False)
    pairwise.to_csv(args.out_dir / "candidate_pairwise_overlap.csv", index=False)
    membership.to_csv(args.out_dir / "candidate_event_membership.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")
    report = report_text(run_info, candidates, pairwise)
    (args.out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
