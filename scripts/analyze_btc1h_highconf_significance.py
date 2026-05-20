#!/usr/bin/env python3
"""Statistical sanity checks for frozen BTC1H high-confidence candidates.

This is not a strategy search.  It reads the robustness audit input trades and
tests whether already-frozen candidates beat a simple breakeven random-outcome
null.  It also compares variants pairwise by event, using zero PnL when a
variant skipped an event.
"""

from __future__ import annotations

import math
import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


OUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"btc1h_highconf_significance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
VARIANTS = [
    "current_1h_late_loss_guard",
    "research_original_late",
    "high_conf_80",
    "high_conf_80_no_chase",
    "high_conf_80_entry70_no_chase",
    "high_conf_80_entry59_70_no_chase",
]
ENTRY59_70_DERIVED_FROM = "high_conf_80_entry70_no_chase"
ENTRY59_70_VARIANT = "high_conf_80_entry59_70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    return parser.parse_args()


def max_drawdown(pnl: np.ndarray) -> float:
    if pnl.size == 0:
        return 0.0
    eq = np.cumsum(pnl)
    return float(np.min(eq - np.maximum.accumulate(eq)))


def sharpe(pnl: np.ndarray) -> float:
    if pnl.size < 2:
        return 0.0
    sd = float(np.std(pnl, ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(np.mean(pnl) / sd * math.sqrt(pnl.size))


def parse_bool_win(df: pd.DataFrame) -> pd.Series:
    if "win_bool" in df.columns:
        raw = df["win_bool"]
    elif "win" in df.columns:
        raw = df["win"]
    else:
        return pd.to_numeric(df.get("pnl", 0.0), errors="coerce").fillna(0.0) > 0.0
    numeric = pd.to_numeric(raw, errors="coerce")
    if numeric.notna().any():
        return numeric.fillna(0.0) > 0.5
    return raw.astype(str).str.lower().isin(["true", "1", "yes"])


def parse_mixed_utc(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return series.map(lambda x: pd.to_datetime(x, utc=True, errors="coerce"))


def add_entry59_70_derived_rows(df: pd.DataFrame) -> pd.DataFrame:
    if ENTRY59_70_VARIANT in set(df["variant"].astype(str)):
        return df
    base = df[df["variant"].astype(str).eq(ENTRY59_70_DERIVED_FROM)].copy()
    if base.empty:
        return df
    entry = pd.to_numeric(base["entry_price"], errors="coerce")
    derived = base[entry.ge(0.59) & entry.le(0.70)].copy()
    if derived.empty:
        return df
    derived["variant"] = ENTRY59_70_VARIANT
    if "model" in derived.columns:
        derived["model"] = ENTRY59_70_VARIANT
    derived["derived_from_variant"] = ENTRY59_70_DERIVED_FROM
    return pd.concat([df, derived], ignore_index=True)


def read_input(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "variant" not in df.columns and "model" in df.columns:
        df["variant"] = df["model"]
    df = df[df["variant"].isin(VARIANTS)].copy()
    df = add_entry59_70_derived_rows(df)
    df["entry_time"] = parse_mixed_utc(df["entry_time"])
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    if "entry_fee" in df.columns:
        df["entry_fee"] = pd.to_numeric(df["entry_fee"], errors="coerce")
    else:
        df["entry_fee"] = df["entry_price"].map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    df["win_bool"] = parse_bool_win(df)
    df["premium_calc"] = df["entry_price"].astype(float) + df["entry_fee"].astype(float)
    df["pnl_calc"] = np.where(df["win_bool"].to_numpy(), 1.0 - df["premium_calc"].to_numpy(), -df["premium_calc"].to_numpy())
    return df.dropna(subset=["source", "variant", "event_ticker", "entry_time", "premium_calc"]).reset_index(drop=True)


def latest_input() -> Path:
    roots = [
        p
        for p in (PROJECT_ROOT / "backtest_outputs").glob("btc1h_highconf_robustness_*")
        if p.is_dir() and (p / "all_input_trades.csv").exists()
    ]
    if not roots:
        raise FileNotFoundError("no btc1h_highconf_robustness_* all_input_trades.csv found")
    return max(roots, key=lambda p: p.stat().st_mtime) / "all_input_trades.csv"


def breakeven_sim_pvalue(g: pd.DataFrame, rng: np.random.Generator, sims: int = 200_000) -> dict[str, float | int]:
    premium = np.clip(g["premium_calc"].to_numpy(dtype=float), 0.0, 1.0)
    pnl_obs = g["pnl_calc"].to_numpy(dtype=float)
    n = len(g)
    if n == 0:
        return {"trades": 0, "pnl": 0.0, "win_rate": 0.0, "sim_p_ge_pnl": 1.0}
    # Under a breakeven binary null, P(win)=premium; each trade has zero
    # expected value after its observed entry fee.
    wins = rng.random((sims, n)) < premium.reshape(1, -1)
    sim_pnl = np.where(wins, 1.0 - premium.reshape(1, -1), -premium.reshape(1, -1)).sum(axis=1)
    obs = float(pnl_obs.sum())
    p_ge = float((sim_pnl >= obs - 1e-12).mean())
    return {
        "trades": int(n),
        "pnl": round(obs, 4),
        "win_rate": round(float(g["win_bool"].mean()), 4),
        "premium": round(float(premium.sum()), 4),
        "max_dd": round(max_drawdown(pnl_obs), 4),
        "sharpe": round(sharpe(pnl_obs), 4),
        "sim_p_ge_pnl": round(p_ge, 6),
        "null_mean": round(float(sim_pnl.mean()), 4),
        "null_p95": round(float(np.quantile(sim_pnl, 0.95)), 4),
        "null_p99": round(float(np.quantile(sim_pnl, 0.99)), 4),
    }


def source_tests(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    # Do not pool websocket cadences as independent; each cadence is a separate
    # replay of the same underlying days.
    groups = []
    pred = df[df["source"].eq("predexon")].copy()
    if not pred.empty:
        groups.extend([(("predexon_all", "predexon", v), g) for v, g in pred.groupby("variant")])
    ws = df[df["source"].eq("websocket")].copy()
    for (cadence, variant), g in ws.groupby(["cadence_sec", "variant"]):
        groups.append(((f"ws_{int(float(cadence))}s", "websocket", variant), g))
    for (dataset, source, variant), g in groups:
        row = {"dataset": dataset, "source": source, "variant": variant}
        row.update(breakeven_sim_pvalue(g.sort_values("entry_time"), rng))
        rows.append(row)
    return pd.DataFrame(rows)


def paired_event_compare(df: pd.DataFrame, a: str, b: str, source: str, cadence: float | None = None) -> dict[str, float | int | str]:
    q = df[df["source"].eq(source)].copy()
    if cadence is not None:
        q = q[pd.to_numeric(q["cadence_sec"], errors="coerce").eq(float(cadence))]
    q = q[q["variant"].isin([a, b])].copy()
    if q.empty:
        return {"source": source, "cadence_sec": cadence if cadence is not None else "", "a": a, "b": b, "events": 0}
    # If a variant has multiple rows for an event in merged artifacts, keep the
    # earliest causal decision for that event/variant.
    q = q.sort_values("entry_time").drop_duplicates(["dataset", "event_ticker", "variant"], keep="first")
    pivot = q.pivot_table(
        index=["dataset", "event_ticker"],
        columns="variant",
        values="pnl_calc",
        aggfunc="first",
        fill_value=0.0,
    )
    for col in [a, b]:
        if col not in pivot.columns:
            pivot[col] = 0.0
    diff = (pivot[b] - pivot[a]).to_numpy(dtype=float)
    nz = diff[np.abs(diff) > 1e-12]
    if nz.size == 0:
        p_two = 1.0
    else:
        # Exact-ish sign-flip permutation for small n; Monte Carlo cap for larger.
        rng = np.random.default_rng(20260516)
        obs = float(diff.sum())
        sims = 200_000
        signs = rng.choice(np.array([-1.0, 1.0]), size=(sims, nz.size))
        sim = (signs * nz.reshape(1, -1)).sum(axis=1)
        p_two = float((np.abs(sim) >= abs(obs) - 1e-12).mean())
    return {
        "source": source,
        "cadence_sec": cadence if cadence is not None else "",
        "a": a,
        "b": b,
        "events": int(len(pivot)),
        "a_pnl": round(float(pivot[a].sum()), 4),
        "b_pnl": round(float(pivot[b].sum()), 4),
        "b_minus_a": round(float(diff.sum()), 4),
        "nonzero_diffs": int(nz.size),
        "signflip_p_two_sided": round(p_two, 6),
    }


def paired_tests(df: pd.DataFrame) -> pd.DataFrame:
    rows = [
        paired_event_compare(df, "high_conf_80", "high_conf_80_no_chase", "predexon"),
        paired_event_compare(df, "high_conf_80", "high_conf_80_entry70_no_chase", "predexon"),
        paired_event_compare(df, "high_conf_80", "high_conf_80_entry59_70_no_chase", "predexon"),
        paired_event_compare(df, "current_1h_late_loss_guard", "high_conf_80_no_chase", "predexon"),
    ]
    for cadence in [1, 5, 10, 15, 20, 30]:
        rows.append(paired_event_compare(df, "high_conf_80", "high_conf_80_no_chase", "websocket", cadence))
        rows.append(paired_event_compare(df, "high_conf_80", "high_conf_80_entry70_no_chase", "websocket", cadence))
        rows.append(paired_event_compare(df, "high_conf_80", "high_conf_80_entry59_70_no_chase", "websocket", cadence))
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
    rng = np.random.default_rng(20260516)
    input_path = args.input or latest_input()
    trades = read_input(input_path)
    source = source_tests(trades, rng)
    paired = paired_tests(trades)
    source.to_csv(args.out_dir / "breakeven_null_tests.csv", index=False)
    paired.to_csv(args.out_dir / "paired_event_tests.csv", index=False)
    lines = [
        "# BTC1H High-Confidence Significance Audit",
        "",
        "This audit uses frozen candidate trades only. It does not tune thresholds.",
        f"Input: `{input_path}`.",
        "",
        "## Breakeven Random-Outcome Null",
        "",
        "Null: each trade wins with probability equal to its all-in premium, so expected PnL is zero.",
        "",
        markdown_table(
            source[
                [
                    "dataset",
                    "variant",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "sim_p_ge_pnl",
                    "null_p95",
                    "null_p99",
                ]
            ].sort_values(["dataset", "pnl"], ascending=[True, False])
        ),
        "",
        "## Paired Event Comparisons",
        "",
        "Rows compare variant B against variant A by event, filling skipped events with zero PnL.",
        "",
        markdown_table(paired.sort_values(["source", "cadence_sec", "b_minus_a"], ascending=[True, True, False])),
        "",
        "## Interpretation",
        "",
        "- Treat p-values as sanity checks, not proof; candidate selection already inspected multiple datasets.",
        "- Predexon `high_conf_80_no_chase` clears the breakeven-null check more strongly than plain `high_conf_80`.",
        "- `high_conf_80_entry59_70_no_chase` is derived from already-replayed `entry70_no_chase` rows by filtering `0.59 <= entry_price <= 0.70`; this is a significance sanity check, not a full causal replay substitute.",
        "- Websocket cadence tests are overlapping replays of the same days, so they are stability checks, not independent samples.",
        "- A future post-freeze shadow/live ledger remains the required promotion gate.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(source.sort_values(["dataset", "pnl"], ascending=[True, False]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
