#!/usr/bin/env python3
"""Stress BTC1H active-candidate proxy labels by official-settlement basis.

Historical BTC1H rows settle on proxy/captured BTC labels, while Kalshi BTC
markets settle on the official expiration value.  This research-only audit
keeps the active policy fixed, shifts each proxy settlement against the chosen
side by fixed dollar shocks, and recomputes hold-to-settlement PnL with fees.
It does not fit a guard, retune thresholds, promote, deploy, or touch processes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.build_btc1h_decision_distance_guard_audit import (  # noqa: E402
    ACTIVE_LEDGER,
    ACTIVE_VARIANT,
    DEFAULT_OUT as DISTANCE_DEFAULT_OUT,
    default_paths,
    markdown_table,
    max_drawdown,
    prepare_forward,
    prepare_historical,
    read_csv,
    sharpe,
)


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = DISTANCE_DEFAULT_OUT.parent / "btc1h_basis_stress_audit_latest_codex"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--shadow-official-trades", type=Path, default=None)
    parser.add_argument(
        "--basis-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_official_basis_mismatch_audit_latest_codex" / "btc1h_basis_mismatch_summary.csv",
    )
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--basis-shocks-usd", default="0,25,50,75")
    return parser.parse_args()


def parse_shocks(text: str) -> list[float]:
    out: list[float] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    return sorted(dict.fromkeys(out))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not Path(path).exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def add_observed_basis_shocks(shocks: list[float], basis_summary_path: Path) -> list[float]:
    rows = safe_read_csv(basis_summary_path)
    if rows.empty:
        return shocks
    row = rows.iloc[0].to_dict()
    for key in ["p95_abs_official_minus_proxy_spot", "max_abs_official_minus_proxy_spot", "max_adverse_basis_usd"]:
        value = to_float(row.get(key), 0.0)
        if value > 0:
            shocks.append(round(value, 3))
    return sorted(dict.fromkeys(float(x) for x in shocks))


def strike_series(df: pd.DataFrame) -> pd.Series:
    result = pd.Series(np.nan, index=df.index, dtype=float)
    for col in ["floor_strike", "ticker_strike", "strike"]:
        if col in df.columns:
            result = result.combine_first(pd.to_numeric(df[col], errors="coerce"))
    if result.notna().any():
        return result
    if "market_ticker" in df.columns:
        parsed = df["market_ticker"].astype(str).str.rsplit("-T", n=1).str[-1]
        return pd.to_numeric(parsed, errors="coerce")
    return result


def settlement_spot_series(df: pd.DataFrame) -> pd.Series:
    result = pd.Series(np.nan, index=df.index, dtype=float)
    for col in ["settlement_spot", "proxy_close_spot", "expiration_value"]:
        if col in df.columns:
            result = result.combine_first(pd.to_numeric(df[col], errors="coerce"))
    return result


def event_from_market(market_ticker: Any) -> str:
    text = str(market_ticker or "").upper()
    if "-T" in text:
        return text.split("-T", 1)[0]
    return text


def prepare_hist(args: argparse.Namespace) -> pd.DataFrame:
    paths = default_paths(args)
    robustness = prepare_historical(safe_read_csv(paths["robustness_trades"]), "robustness_trade_logs", args.stress_cents)
    direct = prepare_historical(safe_read_csv(paths["direct_trades"]), "direct_predexon_trade_logs", args.stress_cents)
    frames = [df for df in [robustness, direct] if not df.empty]
    if not frames:
        return pd.DataFrame()
    work = pd.concat(frames, ignore_index=True, sort=False)
    work["strike_for_stress"] = strike_series(work)
    work["proxy_settlement_spot"] = settlement_spot_series(work)
    work["side"] = work["side"].astype(str).str.lower()
    work["market_ticker"] = work["market_ticker"].astype(str).str.upper()
    if "event_ticker" not in work.columns:
        work["event_ticker"] = ""
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    missing_event = work["event_ticker"].isin(["", "NAN", "NONE"])
    work.loc[missing_event, "event_ticker"] = work.loc[missing_event, "market_ticker"].map(event_from_market)
    work["market_side_key"] = work["market_ticker"] + "|" + work["side"]
    work["entry_stressed"] = (pd.to_numeric(work["entry_price"], errors="coerce") + args.stress_cents / 100.0).clip(
        upper=0.99
    )
    work["premium_for_stress"] = work["entry_stressed"].map(
        lambda x: float(x) + kalshi_fee_dollars(float(x), contracts=1, liquidity="taker")
    )
    proxy_yes = work["proxy_settlement_spot"] >= work["strike_for_stress"]
    work["proxy_result_for_stress"] = np.where(proxy_yes, "yes", "no")
    return work.dropna(subset=["strike_for_stress", "proxy_settlement_spot", "entry_stressed"]).reset_index(drop=True)


def prepare_forward_rows(args: argparse.Namespace) -> pd.DataFrame:
    paths = default_paths(args)
    forward = prepare_forward(safe_read_csv(paths["shadow_official_trades"]))
    if forward.empty:
        return pd.DataFrame()
    work = forward.copy()
    work["proxy_side_margin_abs_usd"] = pd.to_numeric(
        work.get("proxy_close_minus_strike", pd.Series(np.nan, index=work.index)), errors="coerce"
    ).abs()
    return work


def pnl_from_result(side: pd.Series, result: pd.Series, premium: pd.Series) -> pd.Series:
    win = side.astype(str).str.lower().eq(result.astype(str).str.lower())
    return pd.Series(np.where(win, 1.0 - premium.astype(float), -premium.astype(float)), index=side.index)


def max_drawdown_values(values: Iterable[float]) -> float:
    return max_drawdown(pd.Series(list(values), dtype=float))


def stress_rows(hist: pd.DataFrame, shocks: list[float]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for shock in shocks:
        work = hist.copy()
        adverse_direction = np.where(work["side"].eq("yes"), -1.0, 1.0)
        work["basis_shock_usd"] = shock
        work["stressed_settlement_spot"] = work["proxy_settlement_spot"] + adverse_direction * shock
        stressed_yes = work["stressed_settlement_spot"] >= work["strike_for_stress"]
        work["stressed_result"] = np.where(stressed_yes, "yes", "no")
        work["proxy_result_for_stress"] = work["proxy_result_for_stress"].astype(str).str.lower()
        work["basis_result_flip"] = work["stressed_result"] != work["proxy_result_for_stress"]
        work["basis_stressed_pnl"] = pnl_from_result(
            work["side"], work["stressed_result"], work["premium_for_stress"]
        )
        work["basis_stressed_win"] = work["side"].eq(work["stressed_result"])
        rows.append(work)
    return pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()


def summarize_group(group: pd.DataFrame) -> dict[str, Any]:
    pnl = pd.to_numeric(group["basis_stressed_pnl"], errors="coerce").fillna(0.0)
    wins = group["basis_stressed_win"].astype(bool)
    flips = group["basis_result_flip"].astype(bool)
    unique = group.sort_values([c for c in ["entry_time", "event_ticker", "market_ticker", "side"] if c in group.columns])
    unique = unique.drop_duplicates("market_side_key", keep="first")
    unique_pnl = pd.to_numeric(unique["basis_stressed_pnl"], errors="coerce").fillna(0.0)
    return {
        "trades": int(len(group)),
        "unique_market_side_rows": int(len(unique)),
        "pnl": round(float(pnl.sum()), 6),
        "unique_market_side_pnl": round(float(unique_pnl.sum()), 6),
        "win_rate": round(float(wins.mean()), 6) if len(wins) else 0.0,
        "basis_flip_rows": int(flips.sum()),
        "basis_flip_rate": round(float(flips.mean()), 6) if len(flips) else 0.0,
        "max_drawdown": round(max_drawdown_values(pnl), 6),
        "sharpe": round(sharpe(pnl), 6),
    }


def build_summary(stressed: pd.DataFrame, forward: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    holdouts: list[dict[str, Any]] = []
    for shock, group in stressed.groupby("basis_shock_usd", dropna=False, sort=True):
        row = {"basis_shock_usd": shock, "panel": "historical_all_rows"}
        row.update(summarize_group(group))
        row["positive_pnl"] = bool(row["pnl"] > 0)
        row["positive_unique_market_side_pnl"] = bool(row["unique_market_side_pnl"] > 0)
        rows.append(row)
        for (source, holdout), hg in group.groupby(["evidence_source", "holdout"], dropna=False, sort=True):
            hrow = {
                "basis_shock_usd": shock,
                "evidence_source": source,
                "holdout": holdout,
            }
            hrow.update(summarize_group(hg))
            hrow["positive_pnl"] = bool(hrow["pnl"] > 0)
            holdouts.append(hrow)
    summary = pd.DataFrame(rows)
    by_holdout = pd.DataFrame(holdouts)
    if not summary.empty:
        neg_holdouts = {}
        for shock, rows in by_holdout[~by_holdout["positive_pnl"]].groupby("basis_shock_usd", dropna=False):
            neg_holdouts[shock] = ";".join((rows["evidence_source"].astype(str) + "|" + rows["holdout"].astype(str)).tolist())
        summary["negative_holdouts"] = summary["basis_shock_usd"].map(neg_holdouts).fillna("")
        summary["positive_holdouts"] = summary["basis_shock_usd"].map(
            by_holdout.groupby("basis_shock_usd")["positive_pnl"].sum().to_dict()
        )
        summary["holdouts"] = summary["basis_shock_usd"].map(by_holdout.groupby("basis_shock_usd").size().to_dict())
    if not forward.empty:
        observed_min_margin = float(pd.to_numeric(forward.get("proxy_side_margin_abs_usd", pd.Series(dtype=float)), errors="coerce").min())
        summary["current_forward_min_proxy_abs_margin_usd"] = round(observed_min_margin, 6)
    summary["deployable_now"] = False
    return summary, by_holdout


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    base_shocks = parse_shocks(args.basis_shocks_usd)
    shocks = add_observed_basis_shocks(base_shocks, args.basis_summary)
    hist = prepare_hist(args)
    forward = prepare_forward_rows(args)
    if hist.empty:
        raise SystemExit("No active BTC1H historical rows available for basis stress audit.")
    stressed = stress_rows(hist, shocks)
    summary, by_holdout = build_summary(stressed, forward)

    keep_cols = [
        "basis_shock_usd",
        "evidence_source",
        "holdout",
        "event_ticker",
        "market_ticker",
        "side",
        "entry_time",
        "entry_price",
        "entry_stressed",
        "proxy_settlement_spot",
        "strike_for_stress",
        "stressed_settlement_spot",
        "proxy_result_for_stress",
        "stressed_result",
        "basis_result_flip",
        "basis_stressed_pnl",
        "basis_stressed_win",
        "premium_for_stress",
    ]
    summary.to_csv(args.out_dir / "btc1h_basis_stress_summary.csv", index=False)
    by_holdout.to_csv(args.out_dir / "btc1h_basis_stress_by_holdout.csv", index=False)
    stressed[[c for c in keep_cols if c in stressed.columns]].to_csv(
        args.out_dir / "btc1h_basis_stress_trade_rows.csv", index=False
    )
    paths = default_paths(args)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": ACTIVE_VARIANT,
        "active_ledger": ACTIVE_LEDGER,
        "stress_cents": args.stress_cents,
        "basis_shocks_usd": shocks,
        "basis_summary": str(args.basis_summary),
        "deployable_now": False,
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
        "note": "Research-only official-basis stress. Stresses proxy labels against each trade side; does not fit or promote a guard.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    report_cols = [
        "basis_shock_usd",
        "trades",
        "pnl",
        "unique_market_side_pnl",
        "win_rate",
        "basis_flip_rows",
        "basis_flip_rate",
        "max_drawdown",
        "sharpe",
        "positive_holdouts",
        "holdouts",
        "negative_holdouts",
    ]
    report = [
        "# BTC1H Official-Basis Stress Audit",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Variant: `{ACTIVE_VARIANT}`",
        f"Historical/proxy adverse entry stress: `+{args.stress_cents:.1f}c`.",
        "",
        "## Verdict",
        "",
        "- Research-only diagnostic. This cannot promote or deploy the strategy.",
        "- Shocks move proxy settlement against the trade side: YES rows lower, NO rows higher.",
        "- Use this to judge official-settlement fragility before any future preregistered guard.",
        "",
        "## Summary",
        "",
        markdown_table(summary[[c for c in report_cols if c in summary.columns]]),
        "",
        "## Holdout Detail",
        "",
        markdown_table(
            by_holdout[
                [
                    "basis_shock_usd",
                    "evidence_source",
                    "holdout",
                    "trades",
                    "pnl",
                    "unique_market_side_pnl",
                    "basis_flip_rows",
                    "basis_flip_rate",
                    "positive_pnl",
                ]
            ]
            if not by_holdout.empty
            else by_holdout
        ),
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
