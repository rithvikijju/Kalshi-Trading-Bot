#!/usr/bin/env python3
"""Materialize a BTC1H captured order-decision replay baseline.

This diagnostic converts captured `order_decision` paper-fill rows from the
paused BTC1H snapshot into the same trade-row shape used by counterfactual
replay reconciliation. It is not a strategy search and it is not independent
counterfactual replay evidence: it is an exact-decision-log baseline for
isolating whether row-fidelity failures come from data/settlement plumbing or
from the offline replay model/scan semantics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_FILL_OFFICIAL_CHAIN = (
    BACKTEST_ROOT / "btc1h_forward_snapshot_signal_audit_latest_codex" / "btc1h_fill_decision_official_chain.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_order_decision_replay_baseline_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fill-official-chain", type=Path, default=DEFAULT_FILL_OFFICIAL_CHAIN)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--variant", default=VARIANT)
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_time(value: Any) -> pd.Timestamp | None:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts)


def choose_event(row: pd.Series) -> str:
    for col in ("event_ticker_official", "event_ticker_decision", "event_ticker"):
        value = str(row.get(col, "") or "").strip().upper()
        if value and value != "NAN":
            return value
    market = str(row.get("market_ticker", "") or "").strip().upper()
    return market.rsplit("-T", 1)[0] if "-T" in market else ""


def build_trades(fill_chain: pd.DataFrame, variant: str) -> pd.DataFrame:
    if fill_chain.empty:
        return pd.DataFrame()
    work = fill_chain.copy()
    action = work.get("decision_action", pd.Series("", index=work.index)).fillna("").astype(str).str.lower()
    if "_merge" in work.columns:
        matched = work["_merge"].fillna("").astype(str).eq("both")
    else:
        matched = pd.Series(True, index=work.index)
    official_result = work.get("official_result", pd.Series("", index=work.index)).fillna("").astype(str).str.lower()
    work = work.loc[action.eq("paper_fill") & matched & official_result.isin({"yes", "no"})].copy()
    if work.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for row in work.sort_values(["decision_received_at_ns", "market_ticker", "side"]).itertuples(index=False):
        s = pd.Series(row._asdict())
        event_ticker = choose_event(s)
        market_ticker = str(s.get("market_ticker", "") or "").strip().upper()
        side = str(s.get("side", "") or "").strip().lower()
        decision_ts = parse_time(s.get("decision_received_at_utc"))
        official_result_value = str(s.get("official_result", "") or "").strip().lower()
        win = parse_bool(s.get("official_win"))
        entry_price = finite_float(s.get("decision_entry_price", s.get("entry_price")))
        entry_fee = finite_float(s.get("fee"), default=0.0)
        official_premium = finite_float(s.get("official_premium"), default=entry_price + entry_fee)
        official_pnl = finite_float(s.get("official_pnl"), default=0.0)
        rows.append(
            {
                "variant": variant,
                "round_no": 1,
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "side": side,
                "entry_time": decision_ts.isoformat() if decision_ts is not None else str(s.get("decision_received_at_utc", "")),
                "entry_received_at_ns": int(finite_float(s.get("decision_received_at_ns"), default=0.0)),
                "close_time": str(s.get("settlement_ts", "") or ""),
                "entry_price": entry_price,
                "entry_fee": entry_fee,
                "premium": official_premium,
                "visible_qty": finite_float(s.get("top_visible_qty"), default=0.0),
                "side_spread_cents": finite_float(s.get("spread_cents"), default=0.0),
                "model_p_yes": finite_float(s.get("model_p_yes"), default=float("nan")),
                "model_p_side": finite_float(s.get("model_p_yes"), default=float("nan"))
                if side == "yes"
                else 1.0 - finite_float(s.get("model_p_yes"), default=float("nan")),
                "net_edge_cents": finite_float(s.get("decision_net_edge_cents", s.get("net_edge_cents")), default=0.0),
                "edge_threshold_cents": "",
                "btc_spot_model": finite_float(s.get("decision_btc_spot", s.get("entry_btc_spot")), default=0.0),
                "btc_ret_10m_usd": "",
                "btc_spot_source": "captured_order_decision",
                "btc_spot_age_sec": "",
                "btc_research_cache_idx": "",
                "last_top_received_at_ns": int(finite_float(s.get("quote_received_at_ns"), default=0.0)),
                "last_top_received_at_utc": "",
                "seq": "",
                "top_source": "captured_order_decision",
                "official_result": official_result_value,
                "result_source": "captured_order_decision_official_chain",
                "settled": True,
                "win": win,
                "payout": 1.0 if win else 0.0,
                "pnl": official_pnl,
                "diagnostic_replay_source": "captured_order_decision_log",
                "independent_counterfactual_replay": False,
            }
        )
    return pd.DataFrame(rows)


def max_drawdown(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").fillna(0.0)
    if nums.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), nums.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def summarize(trades: pd.DataFrame, variant: str) -> dict[str, Any]:
    pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    premium = pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    wins = trades.get("win", pd.Series(dtype=bool)).fillna(False).astype(bool) if not trades.empty else pd.Series(dtype=bool)
    premium_sum = float(premium.sum()) if not premium.empty else 0.0
    return {
        "variant": variant,
        "baseline_rows": int(len(trades)),
        "settled_rows": int(trades.get("settled", pd.Series(dtype=bool)).fillna(False).astype(bool).sum())
        if not trades.empty
        else 0,
        "official_pnl": round(float(pnl.sum()), 6),
        "official_premium": round(premium_sum, 6),
        "official_rop": round(float(pnl.sum()) / premium_sum, 6) if premium_sum > 0 else 0.0,
        "official_win_rate": round(float(wins.mean()), 6) if len(wins) else 0.0,
        "max_drawdown": round(max_drawdown(pnl), 6),
        "diagnostic_order_decision_baseline": True,
        "independent_counterfactual_replay": False,
        "promotion_usable_as_counterfactual": False,
        "interpretation": "exact captured decision-log baseline; use to isolate replay-engine row-fidelity gaps",
    }


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
    fill_chain = read_csv(args.fill_official_chain)
    trades = build_trades(fill_chain, args.variant)
    summary = summarize(trades, args.variant)

    trades_path = args.out_dir / "btc1h_order_decision_replay_trades.csv"
    summary_path = args.out_dir / "btc1h_order_decision_replay_summary.csv"
    trades.to_csv(trades_path, index=False)
    write_csv(summary_path, [summary])
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "fill_official_chain": str(args.fill_official_chain),
        "out_dir": str(args.out_dir),
        "variant": args.variant,
        "scope": "btc1h_captured_order_decision_replay_baseline_diagnostic_only",
        "independent_counterfactual_replay": False,
        "promotion_usable_as_counterfactual": False,
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC1H Captured Order-Decision Replay Baseline",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary])),
        "",
        "## Interpretation",
        "",
        "- This is a diagnostic exact-decision-log baseline, not an independent counterfactual replay.",
        "- It should reconcile row-for-row with the paper ledger if captured decisions, official settlement, and the reconciliation path are internally consistent.",
        "- It does not relax the requirement that independent replay reproduce decision-time market, entry, and PnL before replay evidence can count.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"wrote {summary_path}")
    print(f"wrote {trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
