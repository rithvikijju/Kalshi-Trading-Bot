#!/usr/bin/env python3
"""Explain BTC1H no-chase websocket cadence instability without tuning.

This is a diagnostic, not a strategy search. It compares the already-frozen
`high_conf_80_no_chase` and `high_conf_80_entry70_no_chase` rows from the
high-confidence robustness artifact and asks whether the known 70c entry cap
explains the harsh 1-second websocket loss.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc1h_no_chase_ws_instability_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
NO_CHASE = "high_conf_80_no_chase"
ENTRY70 = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "backtest_outputs" / "btc1h_highconf_robustness_20260516_012255" / "all_input_trades.csv",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    return parser.parse_args()


def parse_mixed_utc(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return series.map(lambda x: pd.to_datetime(x, utc=True, errors="coerce"))


def win_series(df: pd.DataFrame) -> pd.Series:
    if "win_bool" in df.columns:
        raw = df["win_bool"]
    elif "win" in df.columns:
        raw = df["win"]
    else:
        return pd.to_numeric(df.get("pnl", 0.0), errors="coerce").fillna(0.0) > 0.0
    numeric = pd.to_numeric(raw, errors="coerce")
    if numeric.notna().any():
        return numeric.fillna(0.0) > 0.5
    return raw.astype(str).str.lower().isin({"true", "1", "yes"})


def add_stress_pnl(df: pd.DataFrame, stress_cents: float) -> pd.DataFrame:
    out = df.copy()
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    stressed_entry = (out["entry_price"] + stress_cents / 100.0).clip(upper=0.99)
    fee = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    premium = stressed_entry + fee
    win = win_series(out)
    out["win_bool_calc"] = win
    out["premium_stress"] = premium
    out["pnl_stress"] = np.where(win.to_numpy(), 1.0 - premium.to_numpy(), -premium.to_numpy())
    out["entry_bucket"] = np.where(out["entry_price"].gt(0.70), ">70c", "<=70c")
    return out


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for key, g in df.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        pnl = g["pnl_stress"].astype(float)
        eq = pnl.cumsum()
        dd = float((eq - eq.cummax()).min()) if len(eq) else 0.0
        row = {col: val for col, val in zip(group_cols, key)}
        row.update(
            {
                "trades": int(len(g)),
                "pnl": round(float(pnl.sum()), 4),
                "win_rate": round(float(g["win_bool_calc"].mean()), 4),
                "premium": round(float(g["premium_stress"].sum()), 4),
                "max_dd": round(dd, 4),
                "avg_entry": round(float(g["entry_price"].mean()), 4),
                "first_entry": str(g["entry_time"].min()),
                "last_entry": str(g["entry_time"].max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(args.input)
    df = df[df["source"].astype(str).eq("websocket")].copy()
    df = df[df["variant"].astype(str).isin({NO_CHASE, ENTRY70})].copy()
    df["entry_time"] = parse_mixed_utc(df["entry_time"])
    df["cadence_sec"] = pd.to_numeric(df["cadence_sec"], errors="coerce")
    df = add_stress_pnl(df.dropna(subset=["entry_time", "cadence_sec", "entry_price"]), args.stress_cents)

    no = df[df["variant"].eq(NO_CHASE)].copy()
    e70 = df[df["variant"].eq(ENTRY70)].copy()
    e70_events = set(zip(e70["cadence_sec"], e70["event_ticker"]))
    no["entry70_same_event"] = [pair in e70_events for pair in zip(no["cadence_sec"], no["event_ticker"])]
    no["cap_relation"] = np.where(no["entry_price"].gt(0.70), "filtered_by_70c_cap", "kept_or_replaced")

    summary_variant = summarize(df, ["cadence_sec", "variant"])
    summary_no_bucket = summarize(no, ["cadence_sec", "cap_relation", "entry_bucket", "entry70_same_event"])
    one_sec_rows = no[no["cadence_sec"].eq(1.0)].sort_values(["entry_time", "event_ticker"])
    one_sec_rows.to_csv(args.out_dir / "no_chase_1s_rows.csv", index=False)
    summary_variant.to_csv(args.out_dir / "variant_cadence_summary.csv", index=False)
    summary_no_bucket.to_csv(args.out_dir / "no_chase_bucket_summary.csv", index=False)

    keep_cols = [
        "event_ticker",
        "market_ticker",
        "side",
        "entry_time",
        "entry_price",
        "pnl_stress",
        "win_bool_calc",
        "side_probability",
        "net_edge_cents",
        "side_ret_10m",
        "btc_ret_10m_usd",
        "visible_qty",
        "entry70_same_event",
        "cap_relation",
    ]
    one_sec_view = one_sec_rows[[c for c in keep_cols if c in one_sec_rows.columns]].copy()

    lines = [
        "# BTC1H No-Chase Websocket Instability Diagnostic",
        "",
        f"Input: `{args.input}`.",
        f"Stress: `+{args.stress_cents:.1f}c` adverse entry.",
        "",
        "This is a fixed diagnostic. It does not introduce or tune a new threshold.",
        "",
        "## Variant by Cadence",
        "",
        markdown_table(summary_variant.sort_values(["cadence_sec", "variant"])),
        "",
        "## No-Chase Rows by 70c Cap Relation",
        "",
        markdown_table(summary_no_bucket.sort_values(["cadence_sec", "cap_relation", "entry_bucket"])),
        "",
        "## 1s No-Chase Rows",
        "",
        markdown_table(one_sec_view),
        "",
        "## Interpretation",
        "",
        "- If `filtered_by_70c_cap` rows are negative at 1s, the entry70 cap explains the cadence instability.",
        "- If `<=70c` rows are also negative, the issue is not just high entry price.",
        "- This diagnostic should guide pre-registered forward checks, not create a newly tuned rule.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(summary_no_bucket.sort_values(["cadence_sec", "cap_relation", "entry_bucket"]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
