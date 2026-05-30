#!/usr/bin/env python3
"""Build a BTC1H forward snapshot signal/decision audit.

This is a faithful forward-evidence diagnostic for a paused BTC1H capture
snapshot.  It does not re-score the market surface or search thresholds.  It
uses the live runner's captured `signal_scan` rows, captured `order_decision`
rows, and REST-official shadow ledger output to verify the actual forward
decision chain:

captured selected signal -> captured decision -> paper fill ledger -> official
settlement PnL.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_forward_snapshot_signal_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
DEFAULT_CAPTURE_DB = (
    PROJECT_ROOT
    / "runtime"
    / "remote_snapshots"
    / "snapshot_20260521_145951"
    / "btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb"
)
DEFAULT_OFFICIAL_TRADES = BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
DEFAULT_SINCE_UTC = "2026-05-18T04:17:44Z"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--until-utc", default="")
    parser.add_argument("--min-forward-official-rows", type=int, default=50)
    parser.add_argument("--max-official-proxy-mismatch-rate", type=float, default=0.02)
    return parser.parse_args()


def parse_ts(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts)


def apply_window(df: pd.DataFrame, col: str, since_utc: str, until_utc: str) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return df.copy()
    out = df.copy()
    ts = pd.to_datetime(out[col], utc=True, errors="coerce")
    keep = ts.notna()
    since = parse_ts(since_utc)
    until = parse_ts(until_utc)
    if since is not None:
        keep &= ts >= since
    if until is not None:
        keep &= ts < until
    return out.loc[keep].copy()


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            "select count(*) from information_schema.tables where table_schema='main' and table_name=?",
            [table],
        ).fetchone()[0]
    )


def table_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if not table_exists(con, table):
        return 0
    return int(con.execute(f"select count(*) from {table}").fetchone()[0])


def table_latest(con: duckdb.DuckDBPyConnection, table: str) -> str:
    if not table_exists(con, table):
        return ""
    row = con.execute(f"select max(received_at_utc) from {table}").fetchone()
    return "" if not row or row[0] is None else str(row[0])


def load_selected_signals(con: duckdb.DuckDBPyConnection, since_utc: str, until_utc: str) -> pd.DataFrame:
    if not table_exists(con, "signal_scan"):
        return pd.DataFrame()
    where = ["coalesce(candidate_count, 0) > 0", "lower(coalesce(action, '')) = 'selected'"]
    params: list[Any] = []
    if since_utc:
        where.append("try_cast(received_at_utc as timestamptz) >= try_cast(? as timestamptz)")
        params.append(since_utc)
    if until_utc:
        where.append("try_cast(received_at_utc as timestamptz) < try_cast(? as timestamptz)")
        params.append(until_utc)
    df = con.execute(
        f"""
        select
            received_at_ns as selected_received_at_ns,
            try_cast(received_at_utc as timestamptz) as selected_received_at_utc,
            event_ticker,
            selected_market as market_ticker,
            selected_side as side,
            entry_price as selected_entry_price,
            net_edge_cents as selected_net_edge_cents,
            model_p_yes as selected_model_p_yes,
            btc_spot as selected_btc_spot,
            action as selected_action,
            detail as selected_detail
        from signal_scan
        where {' and '.join(where)}
        order by received_at_ns
        """,
        params,
    ).fetchdf()
    return normalize_keys(df)


def load_decisions(con: duckdb.DuckDBPyConnection, since_utc: str, until_utc: str) -> pd.DataFrame:
    if not table_exists(con, "order_decision"):
        return pd.DataFrame()
    where: list[str] = []
    params: list[Any] = []
    if since_utc:
        where.append("try_cast(received_at_utc as timestamptz) >= try_cast(? as timestamptz)")
        params.append(since_utc)
    if until_utc:
        where.append("try_cast(received_at_utc as timestamptz) < try_cast(? as timestamptz)")
        params.append(until_utc)
    where_sql = "where " + " and ".join(where) if where else ""
    df = con.execute(
        f"""
        select
            received_at_ns as decision_received_at_ns,
            try_cast(received_at_utc as timestamptz) as decision_received_at_utc,
            action as decision_action,
            event_ticker,
            market_ticker,
            side,
            contracts as decision_contracts,
            entry_price as decision_entry_price,
            yes_limit_price as decision_yes_limit_price,
            net_edge_cents as decision_net_edge_cents,
            btc_spot as decision_btc_spot,
            estimated_cost as decision_estimated_cost,
            detail as decision_detail
        from order_decision
        {where_sql}
        order by received_at_ns
        """,
        params,
    ).fetchdf()
    return normalize_keys(df)


def load_official(path: Path, ledger: str, since_utc: str, until_utc: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty or "ledger" not in df.columns:
        return pd.DataFrame()
    df = df.loc[df["ledger"].astype(str).eq(ledger)].copy()
    df = apply_window(df, "created_at", since_utc, until_utc)
    status = df.get("status", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
    df = df.loc[status.eq("paper_filled")].copy()
    return normalize_keys(df)


def normalize_keys(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in ("event_ticker", "market_ticker"):
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.upper()
    if "side" in out.columns:
        out["side"] = out["side"].fillna("").astype(str).str.lower()
    return out


def add_occurrence(df: pd.DataFrame, time_col: str, prefix: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["_entry_key"] = (
        pd.to_numeric(out.get(f"{prefix}_entry_price", out.get("entry_price")), errors="coerce").round(4).astype(str)
    )
    out["_sort_ts"] = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    out = out.sort_values(["event_ticker", "market_ticker", "side", "_entry_key", "_sort_ts"], kind="mergesort")
    out["_occurrence"] = out.groupby(["event_ticker", "market_ticker", "side", "_entry_key"], dropna=False).cumcount()
    out["chain_key"] = (
        out["event_ticker"].astype(str)
        + "|"
        + out["market_ticker"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|"
        + out["_entry_key"].astype(str)
        + "|"
        + out["_occurrence"].astype(str)
    )
    return out.drop(columns=["_sort_ts"])


def add_signal_decision_occurrence(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["_sort_ts"] = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    out = out.sort_values(["event_ticker", "market_ticker", "side", "_sort_ts"], kind="mergesort")
    out["_chain_occurrence"] = out.groupby(["event_ticker", "market_ticker", "side"], dropna=False).cumcount()
    out["chain_key"] = (
        out["event_ticker"].astype(str)
        + "|"
        + out["market_ticker"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|"
        + out["_chain_occurrence"].astype(str)
    )
    return out.drop(columns=["_sort_ts"])


def add_fill_occurrence(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["_sort_ts"] = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    out = out.sort_values(["market_ticker", "side", "_sort_ts"], kind="mergesort")
    out["_fill_occurrence"] = out.groupby(["market_ticker", "side"], dropna=False).cumcount()
    out["fill_key"] = (
        out["market_ticker"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|"
        + out["_fill_occurrence"].astype(str)
    )
    return out.drop(columns=["_sort_ts"])


def max_drawdown(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if nums.size == 0:
        return 0.0
    equity = np.cumsum(nums)
    return float(np.min(equity - np.maximum.accumulate(equity)))


def sharpe(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if nums.size < 2:
        return 0.0
    std = float(nums.std(ddof=1))
    if std <= 1e-12 or not math.isfinite(std):
        return 0.0
    return float(nums.mean() / std * math.sqrt(nums.size))


def boolish(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.lower().isin({"true", "1", "yes"})


def summarize_official(df: pd.DataFrame, holdout: str) -> dict[str, Any]:
    if df.empty:
        return {
            "holdout": holdout,
            "official_rows": 0,
            "official_pnl": 0.0,
            "official_premium": 0.0,
            "official_rop": 0.0,
            "official_win_rate": 0.0,
            "max_drawdown": 0.0,
            "trade_sharpe": 0.0,
            "proxy_rows": 0,
            "proxy_pnl": 0.0,
            "official_proxy_mismatches": 0,
            "official_proxy_mismatch_rate": 0.0,
            "first_created_at": "",
            "last_created_at": "",
        }
    pnl = pd.to_numeric(df["official_pnl"], errors="coerce").fillna(0.0)
    premium = pd.to_numeric(df["official_premium"], errors="coerce").fillna(0.0)
    wins = boolish(df["official_win"])
    proxy_pnl = pd.to_numeric(df.get("proxy_pnl", pd.Series(dtype=float)), errors="coerce")
    proxy_rows = int(proxy_pnl.notna().sum())
    mismatches = boolish(df.get("official_proxy_result_mismatch", pd.Series(dtype=str))).sum()
    premium_sum = float(premium.sum())
    return {
        "holdout": holdout,
        "official_rows": int(len(df)),
        "official_pnl": round(float(pnl.sum()), 6),
        "official_premium": round(premium_sum, 6),
        "official_rop": round(float(pnl.sum()) / premium_sum, 6) if premium_sum > 0 else 0.0,
        "official_win_rate": round(float(wins.mean()), 6) if len(wins) else 0.0,
        "max_drawdown": round(max_drawdown(pnl), 6),
        "trade_sharpe": round(sharpe(pnl), 6),
        "proxy_rows": proxy_rows,
        "proxy_pnl": round(float(proxy_pnl.dropna().sum()), 6) if proxy_rows else 0.0,
        "official_proxy_mismatches": int(mismatches),
        "official_proxy_mismatch_rate": round(float(mismatches) / len(df), 6) if len(df) else 0.0,
        "first_created_at": str(pd.to_datetime(df["created_at"], utc=True, errors="coerce").min()),
        "last_created_at": str(pd.to_datetime(df["created_at"], utc=True, errors="coerce").max()),
    }


def build_holdout_summary(official: pd.DataFrame) -> pd.DataFrame:
    rows = [summarize_official(official, "H5_forward_snapshot_all")]
    if not official.empty:
        work = official.copy()
        ts = pd.to_datetime(work["created_at"], utc=True, errors="coerce")
        work["_date"] = ts.dt.strftime("%Y-%m-%d")
        for date, group in work.groupby("_date", sort=True):
            rows.append(summarize_official(group.drop(columns=["_date"]), f"H5_forward_snapshot_{date}"))
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.capture_db), read_only=True)
    try:
        selected = load_selected_signals(con, args.since_utc, args.until_utc)
        decisions = load_decisions(con, args.since_utc, args.until_utc)
        counts = {
            table: {
                "rows": table_count(con, table),
                "latest_utc": table_latest(con, table),
            }
            for table in ["ws_orderbook_top", "coinbase_ticker", "signal_scan", "order_decision", "capture_health"]
        }
    finally:
        con.close()

    official = load_official(args.official_trades, args.ledger, args.since_utc, args.until_utc)

    selected_keyed = add_signal_decision_occurrence(selected, "selected_received_at_utc")
    decisions_keyed = add_signal_decision_occurrence(decisions, "decision_received_at_utc")
    chain = selected_keyed.merge(
        decisions_keyed,
        on=["chain_key", "event_ticker", "market_ticker", "side"],
        how="outer",
        indicator=True,
        suffixes=("_selected", "_decision"),
    )
    if not chain.empty:
        selected_ts = pd.to_datetime(chain.get("selected_received_at_utc"), utc=True, errors="coerce")
        decision_ts = pd.to_datetime(chain.get("decision_received_at_utc"), utc=True, errors="coerce")
        chain["decision_minus_selected_sec"] = (decision_ts - selected_ts).dt.total_seconds()
        chain["entry_price_diff"] = (
            pd.to_numeric(chain.get("decision_entry_price"), errors="coerce")
            - pd.to_numeric(chain.get("selected_entry_price"), errors="coerce")
        )
        chain["net_edge_diff"] = (
            pd.to_numeric(chain.get("decision_net_edge_cents"), errors="coerce")
            - pd.to_numeric(chain.get("selected_net_edge_cents"), errors="coerce")
        )

    fills = decisions_keyed.loc[decisions_keyed["decision_action"].fillna("").astype(str).str.lower().eq("paper_fill")].copy()
    fills = add_fill_occurrence(fills, "decision_received_at_utc")
    official_keyed = add_fill_occurrence(official, "created_at")
    fill_official = fills.merge(
        official_keyed,
        on=["fill_key", "market_ticker", "side"],
        how="outer",
        indicator=True,
        suffixes=("_decision", "_official"),
    )

    holdouts = build_holdout_summary(official)
    all_summary = holdouts[holdouts["holdout"].eq("H5_forward_snapshot_all")].iloc[0].to_dict()
    official_rows = int(all_summary["official_rows"])
    mismatch_rate = float(all_summary["official_proxy_mismatch_rate"])
    blockers = []
    if official_rows < args.min_forward_official_rows:
        blockers.append("too_few_forward_official_rows")
    if mismatch_rate > args.max_official_proxy_mismatch_rate:
        blockers.append("official_proxy_mismatch_gate_failed")
    if not chain.empty and not chain["_merge"].eq("both").all():
        blockers.append("selected_signal_to_decision_chain_not_1to1")
    if not fill_official.empty and not fill_official["_merge"].eq("both").all():
        blockers.append("fill_decision_to_official_rows_not_1to1")

    fidelity = {
        "ledger": args.ledger,
        "capture_db": str(args.capture_db),
        "official_trades": str(args.official_trades),
        "since_utc": args.since_utc,
        "until_utc": args.until_utc or "",
        "selected_signal_rows": int(len(selected)),
        "decision_rows": int(len(decisions)),
        "paper_fill_decision_rows": int(len(fills)),
        "skip_decision_rows": int(decisions["decision_action"].fillna("").astype(str).str.lower().eq("skip").sum())
        if not decisions.empty
        else 0,
        "selected_to_decision_matched": int(chain["_merge"].eq("both").sum()) if not chain.empty else 0,
        "selected_without_decision": int(chain["_merge"].eq("left_only").sum()) if not chain.empty else 0,
        "decision_without_selected": int(chain["_merge"].eq("right_only").sum()) if not chain.empty else 0,
        "fill_decision_to_official_matched": int(fill_official["_merge"].eq("both").sum()) if not fill_official.empty else 0,
        "fill_without_official": int(fill_official["_merge"].eq("left_only").sum()) if not fill_official.empty else 0,
        "official_without_fill": int(fill_official["_merge"].eq("right_only").sum()) if not fill_official.empty else 0,
        "official_rows": official_rows,
        "official_pnl": all_summary["official_pnl"],
        "official_win_rate": all_summary["official_win_rate"],
        "official_proxy_mismatch_rate": mismatch_rate,
        "deployable_now": False,
        "deploy_blockers": ";".join(blockers) if blockers else "needs_full_readiness_gate",
    }

    chain.to_csv(args.out_dir / "btc1h_selected_signal_decision_chain.csv", index=False)
    fill_official.to_csv(args.out_dir / "btc1h_fill_decision_official_chain.csv", index=False)
    holdouts.to_csv(args.out_dir / "btc1h_forward_snapshot_holdout_summary.csv", index=False)
    pd.DataFrame([fidelity]).to_csv(args.out_dir / "btc1h_forward_snapshot_fidelity_summary.csv", index=False)
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "fidelity": fidelity,
        "capture_counts": counts,
        "note": (
            "Faithful actual-shadow audit only. It verifies the forward signal/decision/fill chain "
            "for a paused snapshot; it is not an all-window counterfactual replay."
        ),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC1H Forward Snapshot Signal Audit",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"Ledger: `{args.ledger}`",
        f"Capture DB: `{args.capture_db}`",
        "",
        "## Fidelity",
        "",
        pd.DataFrame([fidelity]).to_string(index=False),
        "",
        "## Holdouts",
        "",
        holdouts.round(6).to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- This verifies actual forward shadow behavior from captured signal and decision logs.",
        "- It is stronger than a proxy replay for the 11 filled rows, but it is still too small for deployment.",
        "- The full all-window model replay path needs optimization before it can replace this actual-shadow audit.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
