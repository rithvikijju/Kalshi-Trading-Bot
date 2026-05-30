#!/usr/bin/env python3
"""Audit BTC paper-shadow fills against captured live decision logs.

This is the faithful replay audit for periods where a shadow actually ran.  It
does not re-score historical books; it checks that the captured `paper_fill`
order decisions agree row-for-row with the SQLite paper ledger, then attaches
official-settlement PnL from `check_btc_shadow_official_settlement.py`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_shadow_decision_log_parity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_OFFICIAL_TRADES = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"


@dataclass(frozen=True)
class Target:
    ledger: str
    capture_db: Path


TARGETS = [
    Target(
        "btc15m_q250_qty500_firstskip_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb",
    ),
    Target(
        "btc15m_q250_qty500_firstskip_yes_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb",
    ),
    Target(
        "btc15m_q1000_yes_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_capture.duckdb",
    ),
    Target(
        "btc1h_high_conf80_entry70_no_chase_shadow",
        Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--since-utc", default="", help="Optional inclusive UTC lower bound for decision and ledger rows.")
    parser.add_argument("--until-utc", default="", help="Optional exclusive UTC upper bound for decision and ledger rows.")
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        metavar="LEDGER=CAPTURE_DB",
        help=(
            "Override the default capture targets. Can be passed multiple times. "
            "Useful for paused DuckDB snapshots copied from the remote collector."
        ),
    )
    parser.add_argument(
        "--ledger-filter",
        action="append",
        default=[],
        help="When --target is not supplied, restrict default targets to this ledger name. Can be passed multiple times.",
    )
    parser.add_argument(
        "--allow-sidecar-fallback",
        action="store_true",
        help=(
            "If DuckDB cannot be read, scan the .replay.jsonl sidecar. "
            "This can be very slow for multi-GB live captures, so it is opt-in."
        ),
    )
    return parser.parse_args()


def parse_targets(values: list[str], ledger_filters: list[str]) -> list[Target]:
    if values:
        targets: list[Target] = []
        for raw in values:
            if "=" not in raw:
                raise ValueError(f"--target must be LEDGER=CAPTURE_DB, got {raw!r}")
            ledger, capture_db = raw.split("=", 1)
            ledger = ledger.strip()
            if not ledger:
                raise ValueError(f"--target has empty ledger name: {raw!r}")
            targets.append(Target(ledger, Path(capture_db.strip()).expanduser()))
        return targets
    if not ledger_filters:
        return list(TARGETS)
    wanted = {str(item).strip() for item in ledger_filters if str(item).strip()}
    return [target for target in TARGETS if target.ledger in wanted]


def parse_ts(value: Any) -> pd.Timestamp | None:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts)


def apply_window(df: pd.DataFrame, time_col: str, since_utc: str, until_utc: str) -> pd.DataFrame:
    if df.empty or time_col not in df.columns:
        return df.copy()
    out = df.copy()
    ts = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    keep = ts.notna()
    since = parse_ts(since_utc) if since_utc else None
    until = parse_ts(until_utc) if until_utc else None
    if since is not None:
        keep &= ts >= since
    if until is not None:
        keep &= ts < until
    return out.loc[keep].copy()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_duckdb_table(path: Path, table: str) -> tuple[pd.DataFrame, str]:
    if not path.exists():
        return pd.DataFrame(), "missing_capture_db"
    try:
        import duckdb

        con = duckdb.connect(str(path), read_only=True)
        try:
            exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [table],
            ).fetchone()[0]
            if not exists:
                return pd.DataFrame(), f"missing_table_{table}"
            return con.execute(f"SELECT * FROM {table}").fetchdf(), "duckdb_readonly"
        finally:
            con.close()
    except Exception as exc:
        return pd.DataFrame(), f"duckdb_read_failed: {exc!r}"


def read_sidecar_rows(path: Path, table: str) -> tuple[pd.DataFrame, str]:
    sidecar = path.with_name(path.name + ".replay.jsonl")
    if not sidecar.exists():
        return pd.DataFrame(), "missing_replay_sidecar"
    rows: list[dict[str, Any]] = []
    marker = f'"table": "{table}"'
    errors = 0
    source = "replay_sidecar_python_scan"
    rg = shutil.which("rg")
    if rg:
        try:
            found = subprocess.run(
                [rg, "-F", marker, str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
            )
            if found.returncode in {0, 1}:
                source = "replay_sidecar_rg"
                for line in found.stdout.splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        errors += 1
                        continue
                    if row.get("table") == table:
                        rows.append(row)
                if found.returncode == 0 or rows:
                    if errors:
                        source += f"_json_errors_{errors}"
                    return pd.DataFrame(rows), source
        except Exception:
            rows = []
            errors = 0
    with sidecar.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if marker not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue
            if row.get("table") == table:
                rows.append(row)
    if errors:
        source += f"_json_errors_{errors}"
    return pd.DataFrame(rows), source


def read_capture_table(path: Path, table: str, *, allow_sidecar_fallback: bool = False) -> tuple[pd.DataFrame, str]:
    df, source = read_duckdb_table(path, table)
    if not df.empty:
        return df, source
    if not allow_sidecar_fallback:
        return df, f"{source};sidecar_fallback_disabled"
    sidecar_df, sidecar_source = read_sidecar_rows(path, table)
    if not sidecar_df.empty:
        return sidecar_df, f"{sidecar_source}_after_{source}"
    return df, f"{source};{sidecar_source}"


def read_signal_scan_summary(
    path: Path,
    target: Target,
    since_utc: str,
    until_utc: str,
    *,
    allow_sidecar_fallback: bool = False,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "ledger": target.ledger,
            "capture_source": "missing_capture_db",
            "selected_signal_rows": 0,
            "latest_selected_signal_utc": "",
            "latest_selected_signal": "",
        }
    try:
        import duckdb

        con = duckdb.connect(str(path), read_only=True)
        try:
            exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'signal_scan'"
            ).fetchone()[0]
            if not exists:
                source = "missing_table_signal_scan"
            else:
                filters = ["COALESCE(TRY_CAST(candidate_count AS DOUBLE), 0) > 0"]
                params: list[Any] = []
                if since_utc:
                    filters.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= TRY_CAST(? AS TIMESTAMPTZ)")
                    params.append(since_utc)
                if until_utc:
                    filters.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) < TRY_CAST(? AS TIMESTAMPTZ)")
                    params.append(until_utc)
                where_sql = " AND ".join(filters)
                count = int(con.execute(f"SELECT COUNT(*) FROM signal_scan WHERE {where_sql}", params).fetchone()[0])
                latest = con.execute(
                    f"""
                    SELECT received_at_utc, detail
                    FROM signal_scan
                    WHERE {where_sql}
                    ORDER BY
                        TRY_CAST(received_at_ns AS BIGINT) DESC NULLS LAST,
                        TRY_CAST(received_at_utc AS TIMESTAMPTZ) DESC NULLS LAST
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
                return {
                    "ledger": target.ledger,
                    "capture_source": "duckdb_readonly_aggregate",
                    "selected_signal_rows": count,
                    "latest_selected_signal_utc": str(latest[0]) if latest else "",
                    "latest_selected_signal": str(latest[1])[:500] if latest else "",
                }
        finally:
            con.close()
    except Exception as exc:
        source = f"duckdb_signal_scan_summary_failed: {exc!r}"

    if not allow_sidecar_fallback:
        return {
            "ledger": target.ledger,
            "capture_source": f"{source};sidecar_fallback_disabled",
            "selected_signal_rows": 0,
            "latest_selected_signal_utc": "",
            "latest_selected_signal": "",
        }

    raw_scans, scan_source = read_capture_table(path, "signal_scan", allow_sidecar_fallback=True)
    scans = normalize_signal_scans(raw_scans, target, since_utc, until_utc)
    if scans.empty:
        return {
            "ledger": target.ledger,
            "capture_source": scan_source,
            "selected_signal_rows": 0,
            "latest_selected_signal_utc": "",
            "latest_selected_signal": "",
        }
    return {
        "ledger": target.ledger,
        "capture_source": scan_source,
        "selected_signal_rows": int(len(scans)),
        "latest_selected_signal_utc": str(scans["received_at_utc"].iloc[-1]),
        "latest_selected_signal": str(scans["detail"].iloc[-1])[:500],
    }


def as_num(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(float("nan"), index=index, dtype=float)
    return pd.to_numeric(series, errors="coerce")


def as_text(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series("", index=index, dtype=str)
    return series.fillna("").astype(str)


def normalize_decisions(raw: pd.DataFrame, target: Target, since_utc: str, until_utc: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    work = raw.copy()
    if "table" in work.columns:
        work = work.drop(columns=["table"])
    action = as_text(work.get("action"), work.index).str.lower()
    work = work.loc[action.eq("paper_fill")].copy()
    if work.empty:
        return work
    work = apply_window(work, "received_at_utc", since_utc, until_utc)
    if work.empty:
        return work
    out = pd.DataFrame(
        {
            "ledger": target.ledger,
            "event_ticker": as_text(work.get("event_ticker"), work.index).str.upper(),
            "market_ticker": as_text(work.get("market_ticker"), work.index).str.upper(),
            "side": as_text(work.get("side"), work.index).str.lower(),
            "decision_received_at_utc": as_text(work.get("received_at_utc"), work.index),
            "decision_received_at_ns": as_num(work.get("received_at_ns"), work.index),
            "decision_entry_price": as_num(work.get("entry_price"), work.index),
            "decision_contracts": as_num(work.get("contracts"), work.index),
            "decision_estimated_cost": as_num(work.get("estimated_cost"), work.index),
            "decision_net_edge_cents": as_num(work.get("net_edge_cents"), work.index),
            "decision_btc_spot": as_num(work.get("btc_spot"), work.index),
            "decision_detail": as_text(work.get("detail"), work.index),
        }
    )
    return add_occurrence_key(out, "decision_received_at_utc")


def normalize_signal_scans(raw: pd.DataFrame, target: Target, since_utc: str, until_utc: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    work = raw.copy()
    if "table" in work.columns:
        work = work.drop(columns=["table"])
    work = apply_window(work, "received_at_utc", since_utc, until_utc)
    if work.empty:
        return work
    candidate_count = as_num(work.get("candidate_count"), work.index).fillna(0)
    selected = work.loc[candidate_count.gt(0)].copy()
    if selected.empty:
        return selected
    return pd.DataFrame(
        {
            "ledger": target.ledger,
            "received_at_utc": as_text(selected.get("received_at_utc"), selected.index),
            "event_ticker": as_text(selected.get("event_ticker"), selected.index).str.upper(),
            "selected_market": as_text(selected.get("selected_market"), selected.index).str.upper(),
            "selected_side": as_text(selected.get("selected_side"), selected.index).str.lower(),
            "entry_price": as_num(selected.get("entry_price"), selected.index),
            "net_edge_cents": as_num(selected.get("net_edge_cents"), selected.index),
            "model_p_yes": as_num(selected.get("model_p_yes"), selected.index),
            "action": as_text(selected.get("action"), selected.index),
            "detail": as_text(selected.get("detail"), selected.index),
        }
    )


def normalize_ledger(raw: pd.DataFrame, target: Target, since_utc: str, until_utc: str) -> pd.DataFrame:
    if raw.empty or "ledger" not in raw.columns:
        return pd.DataFrame()
    work = raw.loc[raw["ledger"].astype(str).eq(target.ledger)].copy()
    if work.empty:
        return work
    status = as_text(work.get("status"), work.index).str.lower()
    work = work.loc[status.eq("paper_filled")].copy()
    if work.empty:
        return work
    work = apply_window(work, "created_at", since_utc, until_utc)
    if work.empty:
        return work
    out = pd.DataFrame(
        {
            "ledger": target.ledger,
            "event_ticker": as_text(work.get("event_ticker"), work.index).str.upper(),
            "market_ticker": as_text(work.get("market_ticker"), work.index).str.upper(),
            "side": as_text(work.get("side"), work.index).str.lower(),
            "ledger_created_at_utc": as_text(work.get("created_at"), work.index),
            "ledger_entry_price": as_num(work.get("entry_price"), work.index),
            "ledger_contracts": as_num(work.get("contracts"), work.index),
            "ledger_fee": as_num(work.get("fee"), work.index),
            "official_result": as_text(work.get("official_result"), work.index).str.lower(),
            "official_win": as_text(work.get("official_win"), work.index).str.lower(),
            "official_pnl": as_num(work.get("official_pnl"), work.index),
            "official_premium": as_num(work.get("official_premium"), work.index),
            "proxy_result": as_text(work.get("proxy_result"), work.index).str.lower(),
            "proxy_pnl": as_num(work.get("proxy_pnl"), work.index),
            "official_proxy_result_mismatch": as_text(
                work.get("official_proxy_result_mismatch"), work.index
            ).str.lower(),
        }
    )
    return add_occurrence_key(out, "ledger_created_at_utc")


def add_occurrence_key(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    ts = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    out["_sort_ts"] = ts
    out = out.sort_values(["ledger", "market_ticker", "side", "_sort_ts"], kind="mergesort")
    out["_occurrence"] = out.groupby(["ledger", "market_ticker", "side"], dropna=False).cumcount()
    out["recon_key"] = (
        out["ledger"].astype(str)
        + "|"
        + out["market_ticker"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|"
        + out["_occurrence"].astype(str)
    )
    return out.drop(columns=["_sort_ts"])


def ensure_merge_keys(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    out = df.copy()
    for key in keys:
        if key not in out.columns:
            out[key] = pd.Series(dtype=str)
    return out


def merged_ts(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(df[col], utc=True, errors="coerce")


def merged_num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def max_drawdown(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in nums:
        equity += float(value)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return float(max_dd)


def trade_sharpe(values: pd.Series) -> float | str:
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(nums) < 2:
        return ""
    std = float(nums.std(ddof=1))
    if std <= 0 or not math.isfinite(std):
        return ""
    return float(nums.mean() / std * math.sqrt(len(nums)))


def scalar_sum(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").dropna()
    return float(nums.sum()) if len(nums) else 0.0


def boolish_true(values: pd.Series) -> int:
    return int(values.fillna("").astype(str).str.lower().isin({"true", "1", "yes"}).sum())


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    targets = parse_targets(args.target, args.ledger_filter)
    if not targets:
        raise ValueError("no parity targets selected")
    official = read_csv(args.official_trades)
    summary_rows: list[dict[str, Any]] = []
    matched_frames: list[pd.DataFrame] = []
    missing_frames: list[pd.DataFrame] = []
    extra_frames: list[pd.DataFrame] = []
    signal_rows: list[dict[str, Any]] = []

    for target in targets:
        raw_decisions, decision_source = read_capture_table(
            target.capture_db,
            "order_decision",
            allow_sidecar_fallback=args.allow_sidecar_fallback,
        )
        decisions = normalize_decisions(raw_decisions, target, args.since_utc, args.until_utc)
        scan_summary = read_signal_scan_summary(
            target.capture_db,
            target,
            args.since_utc,
            args.until_utc,
            allow_sidecar_fallback=args.allow_sidecar_fallback,
        )
        ledger = normalize_ledger(official, target, args.since_utc, args.until_utc)

        signal_rows.append(scan_summary)

        merge_keys = ["recon_key", "ledger", "event_ticker", "market_ticker", "side"]
        ledger_for_merge = ensure_merge_keys(ledger, merge_keys)
        decisions_for_merge = ensure_merge_keys(decisions, merge_keys)
        merged = ledger_for_merge.merge(
            decisions_for_merge,
            on=merge_keys,
            how="outer",
            indicator=True,
            suffixes=("_ledger", "_decision"),
        )
        if not merged.empty:
            ledger_ts = merged_ts(merged, "ledger_created_at_utc")
            decision_ts = merged_ts(merged, "decision_received_at_utc")
            merged["decision_minus_ledger_sec"] = (decision_ts - ledger_ts).dt.total_seconds()
            merged["entry_price_diff"] = merged_num(merged, "decision_entry_price") - merged_num(
                merged, "ledger_entry_price"
            )
            merged["contracts_diff"] = merged_num(merged, "decision_contracts") - merged_num(
                merged, "ledger_contracts"
            )

        matched = merged.loc[merged["_merge"].eq("both")].copy() if not merged.empty else pd.DataFrame()
        missing = merged.loc[merged["_merge"].eq("left_only")].copy() if not merged.empty else pd.DataFrame()
        extra = merged.loc[merged["_merge"].eq("right_only")].copy() if not merged.empty else pd.DataFrame()
        if not matched.empty:
            matched_frames.append(matched)
        if not missing.empty:
            missing_frames.append(missing)
        if not extra.empty:
            extra_frames.append(extra)

        official_settled = ledger.loc[ledger["official_result"].isin(["yes", "no"])].copy()
        wins = official_settled["official_win"].fillna("").astype(str).str.lower().isin({"true", "1", "yes"})
        summary_rows.append(
            {
                "ledger": target.ledger,
                "capture_db": str(target.capture_db),
                "decision_source": decision_source,
                "paper_fill_decision_rows": int(len(decisions)),
                "ledger_paper_filled_rows": int(len(ledger)),
                "matched_rows": int(len(matched)),
                "missing_decision_rows": int(len(missing)),
                "extra_decision_rows": int(len(extra)),
                "selected_signal_rows": int(scan_summary["selected_signal_rows"]),
                "official_settled_rows": int(len(official_settled)),
                "official_pnl": scalar_sum(official_settled["official_pnl"]) if not official_settled.empty else 0.0,
                "official_premium": scalar_sum(official_settled["official_premium"]) if not official_settled.empty else 0.0,
                "official_win_rate": float(wins.mean()) if len(wins) else 0.0,
                "official_rop": (
                    scalar_sum(official_settled["official_pnl"]) / scalar_sum(official_settled["official_premium"])
                    if not official_settled.empty and scalar_sum(official_settled["official_premium"]) > 0
                    else 0.0
                ),
                "trade_sharpe": trade_sharpe(official_settled["official_pnl"]) if not official_settled.empty else "",
                "max_drawdown": max_drawdown(official_settled["official_pnl"]) if not official_settled.empty else 0.0,
                "proxy_result_mismatches": boolish_true(official_settled["official_proxy_result_mismatch"])
                if not official_settled.empty
                else 0,
                "max_abs_entry_price_diff": float(matched["entry_price_diff"].abs().max())
                if not matched.empty and "entry_price_diff" in matched
                else "",
                "max_abs_contracts_diff": float(matched["contracts_diff"].abs().max())
                if not matched.empty and "contracts_diff" in matched
                else "",
                "max_abs_decision_time_delta_sec": float(matched["decision_minus_ledger_sec"].abs().max())
                if not matched.empty and "decision_minus_ledger_sec" in matched
                else "",
            }
        )

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(args.out_dir / "decision_log_parity_summary.csv", index=False)
    pd.DataFrame(signal_rows).to_csv(args.out_dir / "selected_signal_summary.csv", index=False)
    if matched_frames:
        pd.concat(matched_frames, ignore_index=True).to_csv(args.out_dir / "decision_log_matched_rows.csv", index=False)
    else:
        pd.DataFrame().to_csv(args.out_dir / "decision_log_matched_rows.csv", index=False)
    if missing_frames:
        pd.concat(missing_frames, ignore_index=True).to_csv(args.out_dir / "decision_log_missing_decision_rows.csv", index=False)
    else:
        pd.DataFrame().to_csv(args.out_dir / "decision_log_missing_decision_rows.csv", index=False)
    if extra_frames:
        pd.concat(extra_frames, ignore_index=True).to_csv(args.out_dir / "decision_log_extra_decision_rows.csv", index=False)
    else:
        pd.DataFrame().to_csv(args.out_dir / "decision_log_extra_decision_rows.csv", index=False)

    if not summary.empty and (
        summary["missing_decision_rows"].fillna(0).astype(float).gt(0).any()
        or summary["extra_decision_rows"].fillna(0).astype(float).gt(0).any()
    ):
        if summary["decision_source"].fillna("").astype(str).str.contains("sidecar_fallback_disabled").any():
            interpretation = (
                "Safe active-run audit could not read one or more locked capture DuckDB files and did not scan "
                "multi-GB sidecars. Use a paused DuckDB snapshot for row-for-row decision parity. The official "
                "ledger PnL columns in this report are still valid for the loaded official trades CSV."
            )
        else:
            interpretation = (
                "Decision rows did not match ledger rows row-for-row. Inspect missing/extra CSVs before using this "
                "period as faithful replay evidence."
            )
    else:
        interpretation = (
            "This is a faithful audit of actual shadow fills. It proves captured order_decision rows match "
            "the ledger; it is not a counterfactual all-window strategy replay."
        )

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_trades": str(args.official_trades),
        "since_utc": args.since_utc,
        "until_utc": args.until_utc,
        "targets": [{"ledger": target.ledger, "capture_db": str(target.capture_db)} for target in targets],
        "interpretation": interpretation,
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC Shadow Decision-Log Parity",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Official trades: `{args.official_trades}`",
        f"Since UTC: `{args.since_utc or 'all'}`",
        "",
        "## Summary",
        "",
        summary.round(6).to_string(index=False) if not summary.empty else "No rows.",
        "",
        "## Interpretation",
        "",
        info["interpretation"],
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
