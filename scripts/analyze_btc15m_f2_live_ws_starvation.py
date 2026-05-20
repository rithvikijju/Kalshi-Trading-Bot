#!/usr/bin/env python3
"""Decompose frozen BTC15M F2 live-websocket replay starvation.

This is a read-only diagnostic. It applies frozen q250/q1000/q250-YES rules to
the live websocket capture and explains which gates block row collection. It
does not search thresholds, tune rules, submit orders, or touch live processes.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    DEFAULT_CAPTURE_DB,
    add_proxy_results,
    fee_array,
    load_capture,
    metadata_from_lifecycle,
    prepare_quotes,
)


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_f2_live_ws_starvation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_START = "2026-05-18T04:17:44Z"


FROZEN_CANDIDATES: list[dict[str, Any]] = [
    {
        "candidate": "q250_firstskip_qty500",
        "side": "both",
        "fair_p_min": 0.60,
        "edge_cents_min": 12.0,
        "ttl_min": 10.0,
        "ttl_max": 12.0,
        "spread_max_cents": 2.0,
        "entry_min": 0.02,
        "entry_max": 0.50,
        "visible_qty_min": 250.0,
        "first_signal_visible_qty_min": 500.0,
        "max_btc_spot_age_sec": 10.0,
    },
    {
        "candidate": "q250_firstskip_qty500_yes",
        "side": "yes",
        "fair_p_min": 0.60,
        "edge_cents_min": 12.0,
        "ttl_min": 10.0,
        "ttl_max": 12.0,
        "spread_max_cents": 2.0,
        "entry_min": 0.02,
        "entry_max": 0.50,
        "visible_qty_min": 250.0,
        "first_signal_visible_qty_min": 500.0,
        "max_btc_spot_age_sec": 10.0,
    },
    {
        "candidate": "q1000_yes",
        "side": "yes",
        "fair_p_min": 0.60,
        "edge_cents_min": 12.0,
        "ttl_min": 10.0,
        "ttl_max": 12.0,
        "spread_max_cents": 2.0,
        "entry_min": 0.02,
        "entry_max": 0.50,
        "visible_qty_min": 1000.0,
        "first_signal_visible_qty_min": None,
        "max_btc_spot_age_sec": 10.0,
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diagnose frozen BTC15M F2 live-WS starvation.")
    p.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=None)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--btc-model", choices=["spot", "rolling60"], default="spot")
    return p.parse_args()


def pct(num: float, den: float) -> float:
    return round(float(num) / float(den), 6) if den else 0.0


def finite_stat(values: pd.Series, quantile: float | None = None) -> float | str:
    nums = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if nums.empty:
        return ""
    if quantile is None:
        return round(float(nums.max()), 4)
    return round(float(nums.quantile(quantile)), 4)


def side_rows(q: pd.DataFrame, side: str) -> pd.DataFrame:
    out = q.copy()
    p_yes = pd.to_numeric(out["lognormal_p_yes"], errors="coerce")
    if side == "yes":
        out["side"] = "yes"
        out["entry_price"] = pd.to_numeric(out["yes_ask"], errors="coerce")
        out["visible_qty"] = pd.to_numeric(out["yes_ask_qty"], errors="coerce")
        out["side_fair_p"] = p_yes
    elif side == "no":
        out["side"] = "no"
        out["entry_price"] = pd.to_numeric(out["no_ask"], errors="coerce")
        out["visible_qty"] = pd.to_numeric(out["no_ask_qty"], errors="coerce")
        out["side_fair_p"] = 1.0 - p_yes
    else:
        raise ValueError(side)
    out["entry_fee"] = fee_array(out["entry_price"])
    out["fair_edge_cents"] = (out["side_fair_p"] - out["entry_price"]) * 100.0 - out["entry_fee"] * 100.0
    return out


def materialize_candidate_sides(q: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    sides = ["yes", "no"] if cfg["side"] == "both" else [str(cfg["side"])]
    frames = [side_rows(q, side) for side in sides]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def gate_masks(frame: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, pd.Series]:
    idx = frame.index
    entry = pd.to_numeric(frame["entry_price"], errors="coerce")
    qty = pd.to_numeric(frame["visible_qty"], errors="coerce")
    ttl = pd.to_numeric(frame["ttl_min"], errors="coerce")
    spread = pd.to_numeric(frame["spread_cents"], errors="coerce")
    spot_age = pd.to_numeric(frame["btc_spot_age_sec"], errors="coerce")
    prob = pd.to_numeric(frame["side_fair_p"], errors="coerce")
    edge = pd.to_numeric(frame["fair_edge_cents"], errors="coerce")
    return {
        "valid_quote": entry.between(0.01, 0.99) & qty.ge(1.0),
        "btc_spot_age": spot_age.between(0.0, float(cfg["max_btc_spot_age_sec"])),
        "ttl_window": ttl.between(float(cfg["ttl_min"]), float(cfg["ttl_max"])),
        "spread": spread.le(float(cfg["spread_max_cents"])),
        "entry_band": entry.between(float(cfg["entry_min"]), float(cfg["entry_max"])),
        "visible_qty": qty.ge(float(cfg["visible_qty_min"])),
        "side_probability": prob.ge(float(cfg["fair_p_min"])),
        "edge": edge.ge(float(cfg["edge_cents_min"])),
        "index": pd.Series(True, index=idx),
    }


def summarize_candidate(q: pd.DataFrame, cfg: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    frame = materialize_candidate_sides(q, cfg)
    if frame.empty:
        empty = pd.DataFrame()
        return {"candidate": cfg["candidate"], "rows_total": 0}, empty, empty

    masks = gate_masks(frame, cfg)
    order = [
        "valid_quote",
        "btc_spot_age",
        "ttl_window",
        "spread",
        "entry_band",
        "visible_qty",
        "side_probability",
        "edge",
    ]
    total_rows = int(len(frame))
    total_events = int(frame["event_ticker"].dropna().astype(str).nunique())
    gate_rows: list[dict[str, Any]] = []
    cumulative = pd.Series(True, index=frame.index)
    for gate in order:
        mask = masks[gate].fillna(False)
        independent_pass = int(mask.sum())
        fail_after_previous = int((cumulative & ~mask).sum())
        cumulative &= mask
        gate_rows.append(
            {
                "candidate": cfg["candidate"],
                "side_scope": cfg["side"],
                "gate": gate,
                "independent_pass_rows": independent_pass,
                "independent_pass_share": pct(independent_pass, total_rows),
                "fail_after_previous_rows": fail_after_previous,
                "pass_after_gate_rows": int(cumulative.sum()),
                "pass_after_gate_share": pct(int(cumulative.sum()), total_rows),
            }
        )

    raw_hits = frame.loc[cumulative].copy()
    raw_hits = raw_hits.sort_values(
        ["event_ticker", "received_at_ns", "fair_edge_cents", "seq", "side"],
        ascending=[True, True, False, True, True],
    )
    first_signals = raw_hits.drop_duplicates("event_ticker", keep="first").copy()
    first_qty_min = cfg.get("first_signal_visible_qty_min")
    if first_qty_min is not None and float(first_qty_min) > float(cfg["visible_qty_min"]):
        first_qty_pass = pd.to_numeric(first_signals["visible_qty"], errors="coerce").ge(float(first_qty_min))
        first_signal_qty_rejects = int((~first_qty_pass).sum())
        first_signal_qty_pass_events = int(first_qty_pass.sum())
    else:
        first_signal_qty_rejects = 0
        first_signal_qty_pass_events = int(len(first_signals))

    common_no_alpha = (
        masks["valid_quote"]
        & masks["btc_spot_age"]
        & masks["ttl_window"]
        & masks["spread"]
        & masks["entry_band"]
        & masks["visible_qty"]
    ).fillna(False)
    common = frame.loc[common_no_alpha].copy()
    near_miss_rows = [
        {
            "candidate": cfg["candidate"],
            "side_scope": cfg["side"],
            "slice": "passes_execution_gates_before_prob_edge",
            "rows": int(len(common)),
            "events": int(common["event_ticker"].dropna().astype(str).nunique()) if not common.empty else 0,
            "side_fair_p_p50": finite_stat(common["side_fair_p"], 0.50) if not common.empty else "",
            "side_fair_p_p95": finite_stat(common["side_fair_p"], 0.95) if not common.empty else "",
            "side_fair_p_max": finite_stat(common["side_fair_p"]) if not common.empty else "",
            "fair_edge_cents_p50": finite_stat(common["fair_edge_cents"], 0.50) if not common.empty else "",
            "fair_edge_cents_p95": finite_stat(common["fair_edge_cents"], 0.95) if not common.empty else "",
            "fair_edge_cents_max": finite_stat(common["fair_edge_cents"]) if not common.empty else "",
        }
    ]
    for label, mask in [
        ("passes_all_except_edge", common_no_alpha & masks["side_probability"]),
        ("passes_all_except_probability", common_no_alpha & masks["edge"]),
        ("passes_all_except_visible_qty", masks["valid_quote"]
         & masks["btc_spot_age"]
         & masks["ttl_window"]
         & masks["spread"]
         & masks["entry_band"]
         & masks["side_probability"]
         & masks["edge"]),
    ]:
        sl = frame.loc[mask.fillna(False)].copy()
        near_miss_rows.append(
            {
                "candidate": cfg["candidate"],
                "side_scope": cfg["side"],
                "slice": label,
                "rows": int(len(sl)),
                "events": int(sl["event_ticker"].dropna().astype(str).nunique()) if not sl.empty else 0,
                "side_fair_p_p50": finite_stat(sl["side_fair_p"], 0.50) if not sl.empty else "",
                "side_fair_p_p95": finite_stat(sl["side_fair_p"], 0.95) if not sl.empty else "",
                "side_fair_p_max": finite_stat(sl["side_fair_p"]) if not sl.empty else "",
                "fair_edge_cents_p50": finite_stat(sl["fair_edge_cents"], 0.50) if not sl.empty else "",
                "fair_edge_cents_p95": finite_stat(sl["fair_edge_cents"], 0.95) if not sl.empty else "",
                "fair_edge_cents_max": finite_stat(sl["fair_edge_cents"]) if not sl.empty else "",
            }
        )

    summary = {
        "candidate": cfg["candidate"],
        "side_scope": cfg["side"],
        "rows_total": total_rows,
        "events_total": total_events,
        "raw_hit_rows": int(len(raw_hits)),
        "raw_hit_events": int(raw_hits["event_ticker"].dropna().astype(str).nunique()) if not raw_hits.empty else 0,
        "first_signal_events": int(len(first_signals)),
        "first_signal_qty_rejects": first_signal_qty_rejects,
        "first_signal_qty_pass_events": first_signal_qty_pass_events,
        "post_first_signal_trade_events": first_signal_qty_pass_events,
        "max_side_fair_p_after_execution_gates": near_miss_rows[0]["side_fair_p_max"],
        "max_edge_after_execution_gates": near_miss_rows[0]["fair_edge_cents_max"],
        "top_incremental_blocker": "",
        "top_incremental_blocker_rows": 0,
        "top_incremental_blocker_share": 0.0,
        **{k: v for k, v in cfg.items() if k != "candidate"},
    }
    blockers = [row for row in gate_rows if row["fail_after_previous_rows"] > 0]
    if blockers:
        top = max(blockers, key=lambda row: int(row["fail_after_previous_rows"]))
        summary["top_incremental_blocker"] = top["gate"]
        summary["top_incremental_blocker_rows"] = int(top["fail_after_previous_rows"])
        summary["top_incremental_blocker_share"] = pct(int(top["fail_after_previous_rows"]), total_rows)
    return summary, pd.DataFrame(gate_rows), pd.DataFrame(near_miss_rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    top, btc, lifecycle, _decisions, info = load_capture(args.capture_db, args.start, args.end)
    meta, official_results = metadata_from_lifecycle(lifecycle)
    q = prepare_quotes(top, btc, meta, official_results, args.btc_model)
    q = add_proxy_results(q, btc, args.btc_model)
    capture_end = pd.Timestamp(info["capture_end_utc"])
    q = q[q["close_time"].le(capture_end) & q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()

    summaries: list[dict[str, Any]] = []
    gate_frames: list[pd.DataFrame] = []
    near_frames: list[pd.DataFrame] = []
    for cfg in FROZEN_CANDIDATES:
        summary, gates, near = summarize_candidate(q, cfg)
        summaries.append(summary)
        if not gates.empty:
            gate_frames.append(gates)
        if not near.empty:
            near_frames.append(near)

    summary_df = pd.DataFrame(summaries)
    gates_df = pd.concat(gate_frames, ignore_index=True) if gate_frames else pd.DataFrame()
    near_df = pd.concat(near_frames, ignore_index=True) if near_frames else pd.DataFrame()

    summary_df.to_csv(args.out_dir / "f2_live_ws_starvation_summary.csv", index=False)
    gates_df.to_csv(args.out_dir / "f2_live_ws_starvation_gate_counts.csv", index=False)
    near_df.to_csv(args.out_dir / "f2_live_ws_starvation_near_misses.csv", index=False)

    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_db": str(args.capture_db),
        "capture_start_utc": info.get("capture_start_utc"),
        "capture_end_utc": info.get("capture_end_utc"),
        "top_rows": info.get("top_rows"),
        "quote_rows_after_meta_closed_with_proxy": int(len(q)),
        "btc_model": args.btc_model,
        "candidates": [cfg["candidate"] for cfg in FROZEN_CANDIDATES],
        "note": "Read-only starvation decomposition. Do not use this post-freeze window to retune thresholds.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True, default=str), encoding="utf-8")

    compact_cols = [
        "candidate",
        "side_scope",
        "rows_total",
        "events_total",
        "raw_hit_rows",
        "raw_hit_events",
        "first_signal_events",
        "first_signal_qty_rejects",
        "post_first_signal_trade_events",
        "top_incremental_blocker",
        "top_incremental_blocker_rows",
        "max_side_fair_p_after_execution_gates",
        "max_edge_after_execution_gates",
    ]
    report = [
        "# BTC15M F2 Live-WS Starvation Decomposition",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"Capture: `{run_info['capture_start_utc']}` to `{run_info['capture_end_utc']}`",
        "",
        "## Verdict",
        "",
        "This is a read-only collection diagnostic. It decomposes frozen gates and does not make any strategy deployable.",
        "",
        "## Candidate Summary",
        "",
        summary_df[[col for col in compact_cols if col in summary_df.columns]].fillna("").to_string(index=False)
        if not summary_df.empty
        else "_No candidate rows._",
        "",
        "## Gate Counts",
        "",
        gates_df.fillna("").to_string(index=False) if not gates_df.empty else "_No gate rows._",
        "",
        "## Near Misses",
        "",
        near_df.fillna("").to_string(index=False) if not near_df.empty else "_No near-miss rows._",
        "",
        "## Interpretation",
        "",
        "- `top_incremental_blocker` is order-dependent: valid quote, BTC freshness, TTL, spread, entry, quantity, probability, then edge.",
        "- `passes_execution_gates_before_prob_edge` shows whether the policy has executable-looking rows before the alpha gates.",
        "- `passes_all_except_edge` and `passes_all_except_probability` are diagnostics only. They are not candidate proposals.",
        "- Any new rule suggested by this artifact must be preregistered before future holdout evaluation.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(run_info, indent=2, sort_keys=True, default=str),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
