#!/usr/bin/env python3
"""Audit side and entry-band profiles for the active BTC1H candidate.

This is a diagnostic slicing report, not a threshold search and not a promotion
artifact.  It reuses the active frozen BTC1H candidate's trade rows and asks
whether any fixed side/entry profile looks more robust across historical/proxy
holdouts, live-websocket holdouts, and the current official-settled shadow
rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_btc1h_decision_distance_guard_audit import (  # noqa: E402
    ACTIVE_LEDGER,
    ACTIVE_VARIANT,
    DEFAULT_OUT as DISTANCE_DEFAULT_OUT,
    as_bool,
    default_paths,
    markdown_table,
    prepare_forward,
    prepare_historical,
    read_csv,
    summarize_pnl,
)


DEFAULT_OUT = DISTANCE_DEFAULT_OUT.parent / "btc1h_side_entry_profile_audit_latest_codex"


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def between(series: pd.Series, low: float | None, high: float | None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    mask = pd.Series(True, index=series.index)
    if low is not None:
        mask &= values > low
    if high is not None:
        mask &= values <= high
    return mask.fillna(False)


def side_is(df: pd.DataFrame, side: str) -> pd.Series:
    return df.get("side", pd.Series("", index=df.index)).astype(str).str.lower().eq(side)


PROFILES = [
    Profile("all_active", "Full frozen active candidate.", lambda df: pd.Series(True, index=df.index)),
    Profile("yes_only", "YES-side trades only.", lambda df: side_is(df, "yes")),
    Profile("no_only", "NO-side trades only.", lambda df: side_is(df, "no")),
    Profile("entry_le_50", "Entry price <= 50c.", lambda df: between(df["entry_price"], None, 0.50)),
    Profile("entry_50_60", "Entry price > 50c and <= 60c.", lambda df: between(df["entry_price"], 0.50, 0.60)),
    Profile("entry_60_70", "Entry price > 60c and <= 70c.", lambda df: between(df["entry_price"], 0.60, 0.70)),
    Profile(
        "yes_entry_le_60",
        "YES-side trades with entry <= 60c.",
        lambda df: side_is(df, "yes") & between(df["entry_price"], None, 0.60),
    ),
    Profile(
        "yes_entry_60_70",
        "YES-side trades with entry > 60c and <= 70c.",
        lambda df: side_is(df, "yes") & between(df["entry_price"], 0.60, 0.70),
    ),
    Profile(
        "no_entry_le_60",
        "NO-side trades with entry <= 60c.",
        lambda df: side_is(df, "no") & between(df["entry_price"], None, 0.60),
    ),
    Profile(
        "no_entry_60_70",
        "NO-side trades with entry > 60c and <= 70c.",
        lambda df: side_is(df, "no") & between(df["entry_price"], 0.60, 0.70),
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--shadow-official-trades", type=Path, default=None)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    return parser.parse_args()


def profile_slice(df: pd.DataFrame, profile: Profile) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    mask = profile.predicate(df).fillna(False).astype(bool)
    return df.loc[mask].copy()


def profile_holdout_rows(historical: pd.DataFrame, forward: pd.DataFrame, profile: Profile) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    hist = profile_slice(historical, profile)
    fwd = profile_slice(forward, profile)
    for (source, holdout), group in hist.groupby(["evidence_source", "holdout"], dropna=False, sort=True):
        row = {
            "profile": profile.name,
            "description": profile.description,
            "evidence_source": source,
            "holdout": holdout,
            "settlement_label": "proxy_or_captured_result_plus_adverse_entry_stress",
        }
        row.update(summarize_pnl(group, "research_pnl_stressed", "research_win", "research_premium_stressed"))
        rows.append(row)
    if not fwd.empty:
        row = {
            "profile": profile.name,
            "description": profile.description,
            "evidence_source": "forward_shadow_official_current_diagnostic",
            "holdout": "H5_forward_remote_official_current_diagnostic",
            "settlement_label": "kalshi_rest_official_actual_fee_current_diagnostic",
        }
        row.update(summarize_pnl(fwd, "official_pnl", "official_win_bool", "official_premium"))
        row["official_proxy_mismatches"] = int(fwd["official_proxy_result_mismatch_bool"].sum())
        row["official_proxy_mismatch_rate"] = round(float(fwd["official_proxy_result_mismatch_bool"].mean()), 6)
        row["proxy_win_official_loss_flips"] = int(fwd["proxy_win_official_loss"].sum())
        rows.append(row)
    return rows


def summarize_profile(historical: pd.DataFrame, forward: pd.DataFrame, profile: Profile, holdouts: pd.DataFrame) -> dict[str, object]:
    hist = profile_slice(historical, profile)
    fwd = profile_slice(forward, profile)
    profile_holdouts = holdouts[
        holdouts["profile"].astype(str).eq(profile.name)
        & ~holdouts["evidence_source"].astype(str).eq("forward_shadow_official_current_diagnostic")
    ].copy()
    ws = profile_holdouts[profile_holdouts["holdout"].astype(str).str.startswith("H4_live_ws")].copy()
    hist_pnl = float(pd.to_numeric(hist.get("research_pnl_stressed", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    positive_holdouts = int((pd.to_numeric(profile_holdouts.get("pnl", pd.Series(dtype=float)), errors="coerce") > 0).sum())
    holdout_count = int(len(profile_holdouts))
    ws_positive = int((pd.to_numeric(ws.get("pnl", pd.Series(dtype=float)), errors="coerce") > 0).sum())
    ws_count = int(len(ws))
    fwd_pnl = float(pd.to_numeric(fwd.get("official_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    fwd_mismatches = int(fwd["official_proxy_result_mismatch_bool"].sum()) if not fwd.empty else 0
    fwd_flips = int(fwd["proxy_win_official_loss"].sum()) if not fwd.empty else 0
    side_counts = (
        hist.get("side", pd.Series(dtype=str)).astype(str).str.lower().value_counts().to_dict() if not hist.empty else {}
    )
    forward_side_counts = (
        fwd.get("side", pd.Series(dtype=str)).astype(str).str.lower().value_counts().to_dict() if not fwd.empty else {}
    )
    profile_signal = "diagnostic_only"
    if profile.name == "all_active":
        profile_signal = "active_frozen_candidate"
    elif len(fwd) > 0 and (fwd_pnl <= 0 or fwd_mismatches > 0 or fwd_flips > 0):
        profile_signal = "diagnostic_current_forward_blocked"
    elif holdout_count > 0 and positive_holdouts == holdout_count and (ws_count == 0 or ws_positive == ws_count):
        profile_signal = "offline_stable_profile_needs_causal_replay_and_forward_shadow_decision"
    return {
        "profile": profile.name,
        "description": profile.description,
        "profile_signal": profile_signal,
        "historical_rows": int(len(hist)),
        "historical_pnl_stressed": round(hist_pnl, 4),
        "positive_historical_holdouts": positive_holdouts,
        "historical_holdouts": holdout_count,
        "negative_historical_holdouts": ";".join(
            profile_holdouts.loc[pd.to_numeric(profile_holdouts.get("pnl", pd.Series(dtype=float)), errors="coerce") <= 0, "holdout"]
            .astype(str)
            .tolist()
        )
        if not profile_holdouts.empty
        else "",
        "ws_positive_cadences": ws_positive,
        "ws_cadences": ws_count,
        "historical_yes_rows": int(side_counts.get("yes", 0)),
        "historical_no_rows": int(side_counts.get("no", 0)),
        "forward_official_rows_current_diagnostic": int(len(fwd)),
        "forward_official_pnl_current_diagnostic": round(fwd_pnl, 4),
        "forward_official_win_rate_current_diagnostic": round(float(fwd["official_win_bool"].mean()), 4)
        if not fwd.empty
        else 0.0,
        "forward_official_proxy_mismatches_current_diagnostic": fwd_mismatches,
        "forward_official_proxy_mismatch_rate_current_diagnostic": round(float(fwd_mismatches / len(fwd)), 6)
        if len(fwd)
        else 0.0,
        "forward_proxy_win_official_loss_flips_current_diagnostic": fwd_flips,
        "forward_yes_rows_current_diagnostic": int(forward_side_counts.get("yes", 0)),
        "forward_no_rows_current_diagnostic": int(forward_side_counts.get("no", 0)),
        "deployable_now": False,
        "profile_status": "research_diagnostic_only_not_a_frozen_policy",
    }


def write_forward_rows(out_dir: Path, forward: pd.DataFrame) -> None:
    rows = []
    for profile in PROFILES:
        fwd = profile_slice(forward, profile)
        if fwd.empty:
            continue
        tagged = fwd.copy()
        tagged["profile"] = profile.name
        rows.append(tagged)
    combined = pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()
    cols = [
        "profile",
        "created_at",
        "market_ticker",
        "side",
        "entry_price",
        "entry_btc_spot",
        "floor_strike",
        "entry_side_margin_usd",
        "official_result",
        "proxy_result",
        "official_pnl",
        "proxy_pnl",
        "official_proxy_result_mismatch_bool",
        "proxy_win_official_loss",
        "official_minus_proxy_spot",
        "quote_age_ms",
        "top_visible_qty",
    ]
    combined[[c for c in cols if c in combined.columns]].to_csv(
        out_dir / "btc1h_side_entry_profile_forward_rows.csv", index=False
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    paths = default_paths(args)
    robustness = prepare_historical(read_csv(paths["robustness_trades"]), "robustness_trade_logs", args.stress_cents)
    direct = prepare_historical(read_csv(paths["direct_trades"]), "direct_predexon_trade_logs", args.stress_cents)
    historical = pd.concat([df for df in [robustness, direct] if not df.empty], ignore_index=True, sort=False)
    forward = prepare_forward(read_csv(paths["shadow_official_trades"]))
    if historical.empty and forward.empty:
        raise SystemExit("No BTC1H active-candidate rows found for side/entry profile audit.")

    holdout_rows: list[dict[str, object]] = []
    for profile in PROFILES:
        holdout_rows.extend(profile_holdout_rows(historical, forward, profile))
    holdouts = pd.DataFrame(holdout_rows)
    summary = pd.DataFrame([summarize_profile(historical, forward, profile, holdouts) for profile in PROFILES])
    summary = summary.sort_values(
        [
            "profile_signal",
            "positive_historical_holdouts",
            "ws_positive_cadences",
            "historical_pnl_stressed",
            "forward_official_pnl_current_diagnostic",
        ],
        ascending=[True, False, False, False, False],
    )

    summary.to_csv(args.out_dir / "btc1h_side_entry_profile_summary.csv", index=False)
    holdouts.to_csv(args.out_dir / "btc1h_side_entry_profile_holdouts.csv", index=False)
    write_forward_rows(args.out_dir, forward)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": ACTIVE_VARIANT,
        "active_ledger": ACTIVE_LEDGER,
        "stress_cents": args.stress_cents,
        "profiles": [profile.name for profile in PROFILES],
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
        "deployable_now": False,
        "note": "Diagnostic side/entry slicing only. Profiles are not frozen deployable policies and current official rows are stale-clock diagnostics.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "profile",
        "profile_signal",
        "historical_rows",
        "historical_pnl_stressed",
        "positive_historical_holdouts",
        "historical_holdouts",
        "ws_positive_cadences",
        "ws_cadences",
        "forward_official_rows_current_diagnostic",
        "forward_official_pnl_current_diagnostic",
        "forward_official_proxy_mismatches_current_diagnostic",
        "forward_proxy_win_official_loss_flips_current_diagnostic",
    ]
    report = [
        "# BTC1H Side/Entry Profile Audit",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Variant: `{ACTIVE_VARIANT}`",
        f"Historical/proxy adverse entry stress: `+{args.stress_cents:.1f}c`.",
        "",
        "## Verdict",
        "",
        "- Research-only diagnostic. No profile here is deployable from current rows.",
        "- Use this to decide whether a future clean-clock shadow is worth preregistering, not to retune the running process.",
        "",
        "## Profile Summary",
        "",
        markdown_table(summary[compact_cols]),
        "",
        "## Holdout Detail",
        "",
        markdown_table(
            holdouts[
                [
                    "profile",
                    "evidence_source",
                    "holdout",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "official_proxy_mismatch_rate",
                ]
                if "official_proxy_mismatch_rate" in holdouts.columns
                else ["profile", "evidence_source", "holdout", "trades", "pnl", "win_rate", "max_dd", "sharpe"]
            ].fillna("")
        ),
        "",
        "## Interpretation",
        "",
        "- YES-only and NO-only profiles are side diagnostics, not side-filter promotions.",
        "- Entry-band profiles are fixed slices of the current frozen policy; selecting one after seeing this table would require a new preregistered forward evidence clock.",
        "- Official rows shown here predate the clean scan-time model-input evidence clock and remain diagnostic only.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
