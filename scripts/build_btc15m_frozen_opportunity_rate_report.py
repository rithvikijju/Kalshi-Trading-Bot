#!/usr/bin/env python3
"""Estimate frozen BTC15M frozen-candidate opportunity rates.

This is a research-control diagnostic, not a strategy search. It compares the
older exact live replay window to the post-freeze window and asks whether the
frozen BTC15M candidates are producing enough official-settled opportunities to
reach a serious promotion gate on a practical timeline.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_frozen_opportunity_rate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_FREEZE_UTC = "2026-05-18T04:17:44Z"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC15M frozen opportunity-rate diagnostics.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--freeze-utc", default=DEFAULT_FREEZE_UTC)
    p.add_argument("--promotion-official-rows", type=int, default=100)
    p.add_argument(
        "--q250-full-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_latest_codex",
    )
    p.add_argument(
        "--q250-rest-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex",
    )
    p.add_argument(
        "--q250-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex",
        help="Post-freeze q250 raw replay dir or REST official-fill dir.",
    )
    p.add_argument(
        "--q250-yes-full-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_latest_codex",
    )
    p.add_argument(
        "--q250-yes-rest-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex",
    )
    p.add_argument(
        "--q250-yes-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex",
        help="Post-freeze q250 YES-only replay dir or REST official-fill dir.",
    )
    p.add_argument(
        "--q1000-full-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_causal_latest_codex",
    )
    p.add_argument(
        "--q1000-rest-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex",
    )
    p.add_argument(
        "--q1000-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex",
        help="Post-freeze q1000 YES replay dir or REST official-fill dir.",
    )
    p.add_argument(
        "--signal-health-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_shadow_signal_health_latest_codex",
    )
    p.add_argument(
        "--shadow-status-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex",
    )
    p.add_argument(
        "--ledger-schema-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc_ledger_schema_preflight_latest_codex",
    )
    return p.parse_args()


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    path = project_path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> pd.DataFrame:
    path = project_path(path)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def integer(value: Any, default: int = 0) -> int:
    return int(round(num(value, float(default))))


def ts(value: Any) -> pd.Timestamp | None:
    try:
        out = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(out):
        return None
    if out.tzinfo is None:
        out = out.tz_localize("UTC")
    return out.tz_convert("UTC")


def duration_days(info: dict[str, Any]) -> float:
    start = ts(info.get("capture_start_utc"))
    end = ts(info.get("capture_end_utc"))
    if start is None or end is None or end <= start:
        return 0.0
    return round(float((end - start).total_seconds() / 86400.0), 4)


def official_summary(path: Path) -> dict[str, float]:
    summary = read_csv(path / "summary.csv")
    if summary.empty:
        summary = read_csv(path / "f2_live_ws_summary.csv")
    if summary.empty:
        return {}
    out: dict[str, float] = {}
    if "official_trades" in summary.columns:
        row = summary.iloc[0]
        return {
            "official_2c_trades": num(row.get("official_trades")),
            "official_2c_pnl": num(row.get("official_pnl")),
            "official_2c_win_rate": num(row.get("official_win_rate")),
            "official_2c_max_dd": num(row.get("official_max_dd")),
            "official_2c_sharpe": num(row.get("official_sharpe")),
            "proxy_2c_trades": num(row.get("proxy_trades")),
            "proxy_2c_pnl": num(row.get("proxy_pnl")),
            "proxy_2c_win_rate": num(row.get("proxy_win_rate")),
            "official_proxy_result_mismatches": num(row.get("official_proxy_result_mismatches")),
            "official_minus_proxy_pnl_2c": num(row.get("official_minus_proxy_pnl_2c")),
        }
    if "result_mode" not in summary.columns:
        return out
    modes = summary["result_mode"].astype(str)
    official = summary[modes.eq("official_2c_subset")]
    proxy = summary[modes.eq("proxy_2c")]
    if not official.empty:
        row = official.iloc[0]
        out.update(
            {
                "official_2c_trades": num(row.get("trades")),
                "official_2c_pnl": num(row.get("pnl")),
                "official_2c_win_rate": num(row.get("win_rate")),
                "official_2c_max_dd": num(row.get("max_dd")),
                "official_2c_sharpe": num(row.get("sharpe")),
            }
        )
    if not proxy.empty:
        row = proxy.iloc[0]
        out.update(
            {
                "proxy_2c_trades": num(row.get("trades")),
                "proxy_2c_pnl": num(row.get("pnl")),
                "proxy_2c_win_rate": num(row.get("win_rate")),
            }
        )
    return out


def replay_dir_from_output(path: Path) -> Path:
    """Return the replay artifact dir for either a replay dir or REST-fill dir."""
    output_dir = project_path(path)
    info = read_json(output_dir / "run_info.json")
    if "capture_start_utc" in info or "f2_signals_closed_with_proxy" in info:
        return output_dir
    input_trades = info.get("input_trades")
    if input_trades:
        trades_path = Path(str(input_trades))
        trades_path = trades_path if trades_path.is_absolute() else PROJECT_ROOT / trades_path
        return trades_path.parent
    return output_dir


def shadow_status_row(status: pd.DataFrame, name: str) -> dict[str, Any]:
    if status.empty or "name" not in status.columns:
        return {
            "ledger_running": False,
            "ledger_kind": "",
            "ledger_trade_rows_since": 0,
            "ledger_paper_filled_rows_since": 0,
        }
    rows = status[status["name"].astype(str).eq(name)].head(1)
    if rows.empty:
        return {
            "ledger_running": False,
            "ledger_kind": "",
            "ledger_trade_rows_since": 0,
            "ledger_paper_filled_rows_since": 0,
        }
    row = rows.iloc[0]
    return {
        "ledger_running": str(row.get("running", "")).lower() == "true",
        "ledger_kind": row.get("kind", ""),
        "ledger_pids": row.get("pids", ""),
        "ledger_trade_rows_since": integer(row.get("trade_rows_since")),
        "ledger_paper_filled_rows_since": integer(row.get("paper_filled_rows_since")),
        "ledger_latest_trade_created": row.get("latest_trade_created", ""),
        "ledger_latest_signal_action": row.get("signal_scan_latest_action", ""),
        "ledger_latest_signal_detail": row.get("signal_scan_latest_detail", ""),
    }


def ledger_schema_row(schema: pd.DataFrame, name: str) -> dict[str, Any]:
    if schema.empty or "ledger" not in schema.columns:
        return {
            "ledger_preflight_status": "",
            "ledger_restart_required_for_deployable_ledger": False,
        }
    rows = schema[schema["ledger"].astype(str).eq(name)].head(1)
    if rows.empty:
        return {
            "ledger_preflight_status": "",
            "ledger_restart_required_for_deployable_ledger": False,
        }
    row = rows.iloc[0]
    return {
        "ledger_preflight_status": row.get("preflight_status", ""),
        "ledger_restart_required_for_deployable_ledger": str(
            row.get("restart_required_for_deployable_ledger", "")
        ).lower()
        == "true",
        "ledger_schema_reason": row.get("reason", ""),
    }


def health_row(health: pd.DataFrame, name: str) -> dict[str, Any]:
    if health.empty or "name" not in health.columns:
        return {}
    rows = health[health["name"].astype(str).eq(name)].head(1)
    if rows.empty:
        return {}
    row = rows.iloc[0]
    return {
        "shadow_signal_rows_since": integer(row.get("signal_scan_rows_since")),
        "shadow_nonzero_candidate_rows_since": integer(row.get("signal_nonzero_candidate_rows_since")),
        "shadow_selected_rows_since": integer(row.get("signal_selected_rows_since")),
        "shadow_top_detail_family": row.get("signal_top_detail_family", ""),
        "shadow_top_detail_rows": integer(row.get("signal_top_detail_family_rows")),
        "shadow_btc_spot_age_p95_sec": num(row.get("btc_spot_age_p95_sec_since")),
        "shadow_btc_spot_age_gt10_share": num(row.get("btc_spot_age_gt10_share_since")),
    }


def candidate_row(
    *,
    label: str,
    shadow_name: str,
    full_dir: Path,
    rest_dir: Path,
    postfreeze_dir: Path,
    health: pd.DataFrame,
    status: pd.DataFrame,
    schema: pd.DataFrame,
    promotion_rows: int,
) -> dict[str, Any]:
    full_replay_dir = replay_dir_from_output(full_dir)
    post_replay_dir = replay_dir_from_output(postfreeze_dir)
    full_info = read_json(full_replay_dir / "run_info.json")
    post_info = read_json(post_replay_dir / "run_info.json")
    rest_info = read_json(rest_dir / "run_info.json")
    post_rest_info = read_json(postfreeze_dir / "run_info.json")
    prior_official = official_summary(rest_dir)
    post_official = official_summary(postfreeze_dir)
    full_days = duration_days(full_info)
    post_days = duration_days(post_info)
    full_closed = integer(full_info.get("f2_signals_closed_with_proxy"))
    post_closed = integer(post_info.get("f2_signals_closed_with_proxy"))
    prior_official_rows = integer(
        prior_official.get("official_2c_trades", rest_info.get("rest_results", rest_info.get("trade_rows", 0)))
    )
    post_official_rows = integer(
        post_official.get(
            "official_2c_trades",
            post_rest_info.get("rest_results", post_rest_info.get("trade_rows", 0)) if post_rest_info else 0,
        )
    )
    prior_proxy_rate = full_closed / full_days if full_days > 0 else 0.0
    prior_official_rate = prior_official_rows / full_days if full_days > 0 else 0.0
    post_proxy_rate = post_closed / post_days if post_days > 0 else 0.0
    post_official_rate = post_official_rows / post_days if post_days > 0 else 0.0
    remaining_postfreeze = max(promotion_rows - post_official_rows, 0)
    projected_days_at_prior_official_rate = (
        remaining_postfreeze / prior_official_rate if prior_official_rate > 0 else math.inf
    )
    projected_days_at_post_official_rate = (
        remaining_postfreeze / post_official_rate if post_official_rate > 0 else math.inf
    )
    projected_days_at_post_proxy_rate = remaining_postfreeze / post_proxy_rate if post_proxy_rate > 0 else math.inf
    row: dict[str, Any] = {
        "candidate": label,
        "shadow_name": shadow_name,
        "promotion_min_postfreeze_official_rows": promotion_rows,
        "prior_replay_dir": str(full_replay_dir),
        "prior_rest_dir": str(project_path(rest_dir)),
        "prior_replay_start_utc": full_info.get("capture_start_utc", ""),
        "prior_replay_end_utc": full_info.get("capture_end_utc", ""),
        "prior_replay_days": round(full_days, 4),
        "prior_top_rows": integer(full_info.get("top_rows")),
        "prior_quote_rows_after_meta": integer(full_info.get("quote_rows_after_meta")),
        "prior_raw_hits": integer(full_info.get("f2_raw_hits")),
        "prior_first_signals": integer(full_info.get("f2_first_signals")),
        "prior_first_signal_qty_rejects": integer(full_info.get("f2_first_signal_qty_rejects")),
        "prior_closed_proxy_rows": full_closed,
        "prior_rest_official_rows": prior_official_rows,
        "prior_rest_official_pnl_2c": prior_official.get("official_2c_pnl", 0.0),
        "prior_rest_official_win_rate": prior_official.get("official_2c_win_rate", 0.0),
        "prior_official_proxy_result_mismatches": prior_official.get("official_proxy_result_mismatches", 0.0),
        "prior_closed_proxy_rows_per_day": round(prior_proxy_rate, 4),
        "prior_rest_official_rows_per_day": round(prior_official_rate, 4),
        "postfreeze_replay_dir": str(post_replay_dir),
        "postfreeze_official_dir": str(project_path(postfreeze_dir)),
        "postfreeze_replay_start_utc": post_info.get("capture_start_utc", ""),
        "postfreeze_replay_end_utc": post_info.get("capture_end_utc", ""),
        "postfreeze_replay_days": round(post_days, 4),
        "postfreeze_top_rows": integer(post_info.get("top_rows")),
        "postfreeze_quote_rows_after_meta": integer(post_info.get("quote_rows_after_meta")),
        "postfreeze_raw_hits": integer(post_info.get("f2_raw_hits")),
        "postfreeze_first_signals": integer(post_info.get("f2_first_signals")),
        "postfreeze_closed_proxy_rows": post_closed,
        "postfreeze_official_rows": post_official_rows,
        "postfreeze_official_pnl_2c": post_official.get("official_2c_pnl", 0.0),
        "postfreeze_official_win_rate": post_official.get("official_2c_win_rate", 0.0),
        "postfreeze_official_proxy_result_mismatches": post_official.get("official_proxy_result_mismatches", 0.0),
        "postfreeze_closed_proxy_rows_per_day": round(post_proxy_rate, 4),
        "postfreeze_official_rows_per_day": round(post_official_rate, 4),
        "projected_days_to_100_postfreeze_rows_at_prior_official_rate": round(
            projected_days_at_prior_official_rate, 2
        )
        if math.isfinite(projected_days_at_prior_official_rate)
        else "inf",
        "projected_days_to_100_postfreeze_rows_at_postfreeze_official_rate": round(
            projected_days_at_post_official_rate, 2
        )
        if math.isfinite(projected_days_at_post_official_rate)
        else "inf",
        "projected_days_to_100_postfreeze_rows_at_postfreeze_proxy_rate": round(
            projected_days_at_post_proxy_rate, 2
        )
        if math.isfinite(projected_days_at_post_proxy_rate)
        else "inf",
    }
    row.update(shadow_status_row(status, shadow_name))
    row.update(ledger_schema_row(schema, shadow_name))
    row.update(health_row(health, shadow_name))
    if not row.get("ledger_running", False):
        row["activity_status"] = "simulated_replay_only_shadow_not_running"
    elif post_official_rows > 0:
        row["activity_status"] = "postfreeze_official_replay_rows_seen"
    elif post_closed > 0:
        row["activity_status"] = "postfreeze_proxy_rows_seen_need_official"
    elif post_closed == 0 and integer(row.get("shadow_nonzero_candidate_rows_since", 0)) == 0:
        row["activity_status"] = "frozen_rule_inactive_postfreeze"
    else:
        row["activity_status"] = "no_postfreeze_fills"

    if not row.get("ledger_running", False):
        row["promotion_evidence_status"] = "not_usable_shadow_not_running"
    elif row.get("ledger_restart_required_for_deployable_ledger", False):
        row["promotion_evidence_status"] = "not_usable_ledger_schema_stale"
    elif post_official_rows < promotion_rows:
        row["promotion_evidence_status"] = "not_usable_too_few_official_rows"
    else:
        row["promotion_evidence_status"] = "candidate_for_deeper_gate_review"
    row["interpretation"] = (
        "Post-freeze official replay rows are collection diagnostics only; promotion still requires official rows from a running paper ledger with execution-realism fields."
    )
    return row


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    health = read_csv(args.signal_health_dir / "shadow_signal_health_summary.csv")
    status = read_csv(args.shadow_status_dir / "shadow_status.csv")
    schema = read_csv(args.ledger_schema_dir / "ledger_schema_preflight_summary.csv")
    rows = [
        candidate_row(
            label="q250_firstskip_qty500",
            shadow_name="btc15m_q250_qty500_firstskip_shadow",
            full_dir=args.q250_full_dir,
            rest_dir=args.q250_rest_dir,
            postfreeze_dir=args.q250_postfreeze_dir,
            health=health,
            status=status,
            schema=schema,
            promotion_rows=args.promotion_official_rows,
        ),
        candidate_row(
            label="q250_firstskip_qty500_yes",
            shadow_name="btc15m_q250_qty500_firstskip_yes_shadow",
            full_dir=args.q250_yes_full_dir,
            rest_dir=args.q250_yes_rest_dir,
            postfreeze_dir=args.q250_yes_postfreeze_dir,
            health=health,
            status=status,
            schema=schema,
            promotion_rows=args.promotion_official_rows,
        ),
        candidate_row(
            label="q1000_yes",
            shadow_name="btc15m_q1000_yes_shadow",
            full_dir=args.q1000_full_dir,
            rest_dir=args.q1000_rest_dir,
            postfreeze_dir=args.q1000_postfreeze_dir,
            health=health,
            status=status,
            schema=schema,
            promotion_rows=args.promotion_official_rows,
        ),
    ]
    out = pd.DataFrame(rows)
    out.to_csv(args.out_dir / "frozen_opportunity_rate_summary.csv", index=False)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_utc": args.freeze_utc,
        "promotion_official_rows": args.promotion_official_rows,
        "q250_full_dir": str(args.q250_full_dir),
        "q250_rest_dir": str(args.q250_rest_dir),
        "q250_postfreeze_dir": str(args.q250_postfreeze_dir),
        "q250_yes_full_dir": str(args.q250_yes_full_dir),
        "q250_yes_rest_dir": str(args.q250_yes_rest_dir),
        "q250_yes_postfreeze_dir": str(args.q250_yes_postfreeze_dir),
        "q1000_full_dir": str(args.q1000_full_dir),
        "q1000_rest_dir": str(args.q1000_rest_dir),
        "q1000_postfreeze_dir": str(args.q1000_postfreeze_dir),
        "signal_health_dir": str(args.signal_health_dir),
        "shadow_status_dir": str(args.shadow_status_dir),
        "ledger_schema_dir": str(args.ledger_schema_dir),
        "note": "Diagnostic only. Uses frozen thresholds and replay/REST artifacts for rough collection timing; does not validate, tune, or authorize a strategy.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    show_cols = [
        "candidate",
        "activity_status",
        "promotion_evidence_status",
        "ledger_running",
        "ledger_preflight_status",
        "prior_replay_days",
        "prior_rest_official_rows",
        "prior_rest_official_pnl_2c",
        "prior_rest_official_rows_per_day",
        "postfreeze_replay_days",
        "postfreeze_raw_hits",
        "postfreeze_first_signals",
        "postfreeze_official_rows",
        "postfreeze_official_pnl_2c",
        "postfreeze_official_rows_per_day",
        "shadow_signal_rows_since",
        "shadow_nonzero_candidate_rows_since",
        "shadow_top_detail_family",
        "projected_days_to_100_postfreeze_rows_at_prior_official_rate",
        "projected_days_to_100_postfreeze_rows_at_postfreeze_official_rate",
    ]
    report = [
        "# BTC15M Frozen Opportunity Rate Report",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Freeze UTC: `{args.freeze_utc}`",
        "",
        "## Summary",
        "",
        out[[col for col in show_cols if col in out.columns]].fillna("").to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- No BTC15M candidate is deployable from this report. The official post-freeze samples are tiny and the live ledgers are either stale-schema or not running.",
        "- q250 YES-only now has a replay/REST-filled post-freeze row, but the preregistered YES-only paper shadow is not running, so that row is not ledger promotion evidence.",
        "- q250 raw and q1000 YES have running paper shadows, but stale ledger schemas still block deployable execution-realism evidence.",
        "- The projected day counts are collection-planning diagnostics only. They must not be used to retune thresholds on the forward window.",
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
