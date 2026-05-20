#!/usr/bin/env python3
"""Build a side-specific BTC settlement-basis risk audit.

This is a deployment-control diagnostic, not a strategy search. It combines
the current BTC15M REST-official basis watch with BTC1H official shadow rows
and asks a narrow question: does proxy-vs-official settlement risk already
block promotion for each frozen path?
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_settlement_basis_risk_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC settlement-basis risk diagnostics.")
    p.add_argument(
        "--btc15m-basis-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_settlement_basis_watch_latest_codex",
    )
    p.add_argument(
        "--shadow-official-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex",
    )
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--freeze-utc", default="2026-05-18T04:17:44Z")
    p.add_argument("--max-mismatch-rate", type=float, default=0.02)
    p.add_argument("--min-btc15m-official-rows", type=int, default=100)
    p.add_argument("--min-btc1h-official-rows", type=int, default=50)
    p.add_argument("--max-official-minus-proxy-pnl-per-trade", type=float, default=-0.02)
    return p.parse_args()


def norm_text(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().replace({"nan": "", "none": "", "<na>": ""})


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def quantile(series: pd.Series, q: float) -> float:
    x = pd.to_numeric(series, errors="coerce").dropna()
    if x.empty:
        return math.nan
    return float(x.quantile(q))


def load_btc15m(path: Path) -> pd.DataFrame:
    rows = read_csv(path / "settlement_basis_rows.csv")
    if rows.empty:
        return pd.DataFrame()
    out = rows.copy()
    out["family"] = "BTC15M"
    out["source_table"] = "btc15m_settlement_basis_rows"
    out["created_at"] = out.get("received_at_utc", "")
    out["official_result"] = norm_text(out.get("official_result_filled", out.get("rest_result", "")))
    out["proxy_result"] = norm_text(out.get("proxy_result", ""))
    out["official_pnl_2c"] = pd.to_numeric(out.get("pnl_official_rest_2c"), errors="coerce")
    out["proxy_pnl_2c"] = pd.to_numeric(out.get("pnl_proxy_2c"), errors="coerce")
    out["basis_usd"] = pd.to_numeric(out.get("basis_official_minus_proxy_usd"), errors="coerce")
    out["proxy_distance_usd"] = pd.to_numeric(out.get("proxy_distance_usd"), errors="coerce")
    out["official_distance_usd"] = pd.to_numeric(out.get("official_distance_usd"), errors="coerce")
    out["entry_spot_distance_bps"] = pd.to_numeric(out.get("aligned_decision_distance_bps"), errors="coerce")
    return out


def load_btc1h(path: Path) -> pd.DataFrame:
    rows = read_csv(path / "shadow_official_trades.csv")
    if rows.empty:
        return pd.DataFrame()
    finalized = rows[norm_text(rows.get("official_status", "")).eq("finalized")].copy()
    if finalized.empty:
        return pd.DataFrame()
    out = finalized.copy()
    ledger = out.get("ledger", pd.Series("", index=out.index)).astype(str).str.lower()
    event_ticker = out.get("event_ticker", pd.Series("", index=out.index)).astype(str).str.upper()
    market_ticker = out.get("market_ticker", pd.Series("", index=out.index)).astype(str).str.upper()
    is_btc15m = ledger.str.contains("btc15m") | event_ticker.str.contains("KXBTC15M") | market_ticker.str.contains(
        "KXBTC15M"
    )
    out["family"] = np.where(is_btc15m, "BTC15M", "BTC1H")
    out["source"] = "shadow_official_ledger"
    out["source_table"] = "shadow_official_trades"
    out["candidate"] = out.get("ledger", "btc1h_shadow")
    out["created_at"] = out.get("created_at", "")
    out["official_result"] = norm_text(out.get("official_result", ""))
    out["proxy_result"] = norm_text(out.get("proxy_result", ""))
    out["official_pnl_2c"] = pd.to_numeric(out.get("official_pnl"), errors="coerce")
    out["proxy_pnl_2c"] = pd.to_numeric(out.get("proxy_pnl"), errors="coerce")
    out["basis_usd"] = pd.to_numeric(out.get("official_minus_proxy_spot"), errors="coerce")
    out["proxy_distance_usd"] = pd.to_numeric(out.get("proxy_close_minus_strike"), errors="coerce")
    out["official_distance_usd"] = pd.to_numeric(out.get("official_expiration_minus_strike"), errors="coerce")
    out["entry_spot_distance_bps"] = pd.to_numeric(out.get("entry_spot_distance_bps"), errors="coerce")
    return out


def normalize_rows(frames: list[pd.DataFrame]) -> pd.DataFrame:
    nonempty = [df for df in frames if not df.empty]
    if not nonempty:
        return pd.DataFrame()
    rows = pd.concat(nonempty, ignore_index=True, sort=False)
    rows["candidate"] = rows.get("candidate", "").astype(str)
    rows["side"] = norm_text(rows.get("side", ""))
    rows["source"] = rows.get("source", "").astype(str)
    both = rows["official_result"].isin(["yes", "no"]) & rows["proxy_result"].isin(["yes", "no"])
    rows["both_result_rows"] = both
    rows["proxy_official_mismatch"] = both & rows["official_result"].ne(rows["proxy_result"])
    rows["pnl_delta_official_minus_proxy_2c"] = rows["official_pnl_2c"] - rows["proxy_pnl_2c"]
    rows["adverse_proxy_official_mismatch"] = rows["proxy_official_mismatch"] & rows[
        "pnl_delta_official_minus_proxy_2c"
    ].lt(0)
    side_yes = rows["side"].eq("yes")
    rows["aligned_proxy_distance_usd"] = np.where(side_yes, rows["proxy_distance_usd"], -rows["proxy_distance_usd"])
    rows["aligned_official_distance_usd"] = np.where(
        side_yes,
        rows["official_distance_usd"],
        -rows["official_distance_usd"],
    )
    rows["proxy_win_for_side"] = rows["aligned_proxy_distance_usd"] > 0
    rows["official_win_for_side"] = rows["aligned_official_distance_usd"] > 0
    rows["proxy_win_official_loss"] = rows["proxy_win_for_side"] & ~rows["official_win_for_side"]
    rows["abs_basis_usd"] = rows["basis_usd"].abs()
    rows["abs_proxy_distance_usd"] = rows["proxy_distance_usd"].abs()
    return rows


def summarize(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    out_rows: list[dict[str, Any]] = []
    for key, g in rows.groupby(group_cols, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        official = g[g["official_result"].isin(["yes", "no"])]
        proxy = g[g["proxy_result"].isin(["yes", "no"])]
        both = g[g["both_result_rows"]]
        delta = pd.to_numeric(g["pnl_delta_official_minus_proxy_2c"], errors="coerce")
        basis = pd.to_numeric(g["basis_usd"], errors="coerce")
        abs_basis = basis.abs()
        proxy_dist = pd.to_numeric(g["abs_proxy_distance_usd"], errors="coerce")
        row = dict(zip(group_cols, key_tuple, strict=False))
        row.update(
            {
                "rows": int(len(g)),
                "official_rows": int(len(official)),
                "proxy_rows": int(len(proxy)),
                "both_result_rows": int(len(both)),
                "mismatches": int(g["proxy_official_mismatch"].sum()),
                "adverse_mismatches": int(g["adverse_proxy_official_mismatch"].sum()),
                "proxy_win_official_loss_rows": int(g["proxy_win_official_loss"].sum()),
                "mismatch_rate": round(float(g["proxy_official_mismatch"].sum() / len(both)), 4)
                if len(both)
                else 0.0,
                "official_pnl_2c": round(float(pd.to_numeric(official["official_pnl_2c"], errors="coerce").sum()), 4)
                if not official.empty
                else 0.0,
                "proxy_pnl_2c": round(float(pd.to_numeric(proxy["proxy_pnl_2c"], errors="coerce").sum()), 4)
                if not proxy.empty
                else 0.0,
                "pnl_delta_official_minus_proxy_2c": round(float(delta.sum()), 4)
                if not delta.dropna().empty
                else 0.0,
                "pnl_delta_per_both_trade_2c": round(float(delta.sum() / len(both)), 4) if len(both) else 0.0,
                "mean_basis_usd": round(float(basis.mean()), 4) if not basis.dropna().empty else math.nan,
                "basis_p05_usd": round(quantile(basis, 0.05), 4),
                "basis_p50_usd": round(quantile(basis, 0.50), 4),
                "basis_p95_usd": round(quantile(basis, 0.95), 4),
                "abs_basis_p90_usd": round(quantile(abs_basis, 0.90), 4),
                "abs_basis_p95_usd": round(quantile(abs_basis, 0.95), 4),
                "max_abs_basis_usd": round(float(abs_basis.max()), 4) if not abs_basis.dropna().empty else math.nan,
                "median_abs_proxy_distance_usd": round(float(proxy_dist.median()), 4)
                if not proxy_dist.dropna().empty
                else math.nan,
                "near_proxy_distance_le_abs_basis_p95_rows": int(
                    proxy_dist.le(quantile(abs_basis, 0.95)).fillna(False).sum()
                )
                if not abs_basis.dropna().empty
                else 0,
            }
        )
        out_rows.append(row)
    return pd.DataFrame(out_rows).sort_values(group_cols).reset_index(drop=True)


def gate_rows(summary: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in summary.iterrows():
        family = str(row.get("family", ""))
        min_rows = args.min_btc15m_official_rows if family == "BTC15M" else args.min_btc1h_official_rows
        reasons: list[str] = []
        if int(row.get("official_rows", 0) or 0) < min_rows:
            reasons.append("too_few_official_rows")
        if float(row.get("mismatch_rate", 0.0) or 0.0) > args.max_mismatch_rate:
            reasons.append("proxy_official_mismatch_rate_high")
        if float(row.get("pnl_delta_per_both_trade_2c", 0.0) or 0.0) < args.max_official_minus_proxy_pnl_per_trade:
            reasons.append("official_minus_proxy_pnl_delta_bad")
        if int(row.get("adverse_mismatches", 0) or 0) > 0:
            reasons.append("adverse_proxy_official_mismatches")
        if int(row.get("proxy_win_official_loss_rows", 0) or 0) > 0:
            reasons.append("proxy_win_official_loss_flip")
        rows.append(
            {
                "family": family,
                "candidate": row.get("candidate", ""),
                "side": row.get("side", ""),
                "source": row.get("source", ""),
                "basis_gate_pass": not reasons,
                "basis_gate_reasons": ";".join(reasons),
                "promotion_min_official_rows": min_rows,
                "official_rows": row.get("official_rows", 0),
                "both_result_rows": row.get("both_result_rows", 0),
                "mismatch_rate": row.get("mismatch_rate", 0.0),
                "adverse_mismatches": row.get("adverse_mismatches", 0),
                "pnl_delta_per_both_trade_2c": row.get("pnl_delta_per_both_trade_2c", 0.0),
                "abs_basis_p95_usd": row.get("abs_basis_p95_usd", math.nan),
                "max_abs_basis_usd": row.get("max_abs_basis_usd", math.nan),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = normalize_rows([load_btc15m(args.btc15m_basis_dir), load_btc1h(args.shadow_official_dir)])
    source_summary = summarize(rows, ["family", "source", "candidate", "side"])
    candidate_summary = summarize(rows, ["family", "candidate", "side"])
    gates = gate_rows(candidate_summary, args)
    mismatches = rows[rows.get("proxy_official_mismatch", pd.Series(dtype=bool)).fillna(False)].copy()

    rows.to_csv(args.out_dir / "settlement_basis_risk_rows.csv", index=False)
    source_summary.to_csv(args.out_dir / "settlement_basis_risk_by_source.csv", index=False)
    candidate_summary.to_csv(args.out_dir / "settlement_basis_risk_by_candidate.csv", index=False)
    gates.to_csv(args.out_dir / "settlement_basis_risk_gates.csv", index=False)
    mismatches.to_csv(args.out_dir / "settlement_basis_risk_mismatches.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_utc": args.freeze_utc,
        "btc15m_basis_dir": str(args.btc15m_basis_dir),
        "shadow_official_dir": str(args.shadow_official_dir),
        "rows": int(len(rows)),
        "mismatch_rows": int(len(mismatches)),
        "max_mismatch_rate": args.max_mismatch_rate,
        "max_official_minus_proxy_pnl_per_trade": args.max_official_minus_proxy_pnl_per_trade,
        "min_btc15m_official_rows": args.min_btc15m_official_rows,
        "min_btc1h_official_rows": args.min_btc1h_official_rows,
        "note": "Diagnostic only. This audits settlement-basis risk for frozen paths; it does not create a trading rule.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "family",
        "candidate",
        "side",
        "basis_gate_pass",
        "basis_gate_reasons",
        "official_rows",
        "mismatch_rate",
        "adverse_mismatches",
        "pnl_delta_per_both_trade_2c",
        "abs_basis_p95_usd",
        "max_abs_basis_usd",
    ]
    gate_view = gates[[col for col in compact_cols if col in gates.columns]].copy()
    report = [
        "# BTC Settlement Basis Risk Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Freeze UTC: `{args.freeze_utc}`",
        "",
        "## Basis Gates",
        "",
        gate_view.fillna("").to_string(index=False) if not gate_view.empty else "_No gate rows._",
        "",
        "## Candidate Summary",
        "",
        candidate_summary.fillna("").round(4).to_string(index=False) if not candidate_summary.empty else "_No rows._",
        "",
        "## Mismatches",
        "",
        mismatches[
            [
                "family",
                "source",
                "candidate",
                "side",
                "market_ticker",
                "created_at",
                "official_result",
                "proxy_result",
                "official_pnl_2c",
                "proxy_pnl_2c",
                "pnl_delta_official_minus_proxy_2c",
                "basis_usd",
                "proxy_distance_usd",
                "official_distance_usd",
            ]
        ].fillna("").round(4).to_string(index=False)
        if not mismatches.empty
        else "_No mismatches._",
        "",
        "## Interpretation",
        "",
        "- Any failed basis gate blocks deployment; the row can only continue as research/forward collection.",
        "- The thresholds mirror the current Pro/local promotion posture: enough official rows, mismatch rate <= 2%, and no materially bad official-minus-proxy PnL drift.",
        "- This artifact does not propose filters; it shows where the settlement index makes proxy evidence unsafe.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
