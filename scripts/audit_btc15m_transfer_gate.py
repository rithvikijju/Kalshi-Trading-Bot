#!/usr/bin/env python3
"""Consolidate BTC15M candidate transfer evidence across validation slices.

This is deliberately an audit script, not a search script.  It reads frozen
backtest outputs that already exist, joins the key train/validation/live
evidence, and applies a conservative promotion gate:

* positive Apr1-14 study/training result
* positive Apr15-30 validation result
* positive May1-12 validation result
* positive older-data Jan proxy result when available
* positive live websocket proxy replay
* positive live websocket official subset when available

The goal is to make overfit candidates obvious rather than to rescue them.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_transfer_gate_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


F2_LABELS = {
    "base_f2": "base_f2",
    "ttl10_12_entry55_q50": "strict_ttl10_12_entry55_q50",
    "ttl6_12": "ttl6_12",
}

REGIME_TO_LIVE = {
    "edge12_to_28_highrv32": "edge12_to_28_highrv32",
    "highrv320_q1_base": "highrv320_q1_base",
    "highrv320_q100_base": "highrv320_q100_base",
    "liquid_highrv32_not_against": "liquid_highrv32_not_against",
    "strict_plus_highrv20": "strict_plus_highrv20",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-train-trades", type=int, default=25)
    parser.add_argument("--min-live-trades", type=int, default=5)
    return parser.parse_args()


def latest_dir(pattern: str) -> Path:
    matches = [p for p in BACKTEST_ROOT.glob(pattern) if p.is_dir()]
    if not matches:
        raise FileNotFoundError(f"no backtest output matching {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_f2_summary(path: Path, split_label: str) -> pd.DataFrame:
    df = pd.read_csv(path / "summary.csv")
    df = df[df["split"].eq("all")].copy()
    out = []
    for _, row in df.iterrows():
        strategy = str(row["strategy"])
        label = F2_LABELS.get(strategy, strategy)
        out.append(
            {
                "candidate": label,
                "source_family": "f2_range",
                "slice": split_label,
                "trades": int(row["trades"]),
                "pnl": float(row["pnl_stress"]),
                "win_rate": float(row["win_rate"]),
                "max_dd": float(row["max_dd"]),
                "sharpe": float(row["sharpe"]),
                "path": str(path.relative_to(PROJECT_ROOT)),
            }
        )
    return pd.DataFrame(out)


def read_live_summary(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path / "f2_live_ws_summary.csv")
    out = []
    for _, row in df.iterrows():
        mode = str(row["result_mode"])
        if mode not in {"proxy_2c", "official_2c_subset"}:
            continue
        out.append(
            {
                "candidate": "base_f2",
                "source_family": "f2_live_ws",
                "slice": f"live_ws_{mode}",
                "trades": int(row["trades"]),
                "pnl": float(row["pnl"]),
                "win_rate": float(row["win_rate"]),
                "max_dd": float(row["max_dd"]),
                "sharpe": float(row["sharpe"]),
                "path": str(path.relative_to(PROJECT_ROOT)),
            }
        )
    return pd.DataFrame(out)


def read_regime_wide(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path / "rule_summary_wide.csv")
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        rule = str(row["rule"])
        for col_prefix, split_label in [
            ("train_apr1_14", "apr1_14_train"),
            ("val_apr15_30", "apr15_30_val"),
            ("val_may1_12", "may1_12_val"),
            ("external", "jan_proxy_external"),
        ]:
            trades_col = f"trades_{col_prefix}"
            pnl_col = f"pnl_{col_prefix}"
            wr_col = f"win_rate_{col_prefix}"
            dd_col = f"max_dd_{col_prefix}"
            if trades_col not in row.index or pnl_col not in row.index:
                continue
            if pd.isna(row[trades_col]) or pd.isna(row[pnl_col]):
                continue
            rows.append(
                {
                    "candidate": rule,
                    "source_family": "regime_family",
                    "slice": split_label,
                    "trades": int(float(row[trades_col])),
                    "pnl": float(row[pnl_col]),
                    "win_rate": float(row[wr_col]) if wr_col in row.index and not pd.isna(row[wr_col]) else float("nan"),
                    "max_dd": float(row[dd_col]) if dd_col in row.index and not pd.isna(row[dd_col]) else float("nan"),
                    "sharpe": float("nan"),
                    "path": str(path.relative_to(PROJECT_ROOT)),
                }
            )
    return pd.DataFrame(rows)


def read_regime_live(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path / "regime_live_ws_summary.csv")
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        mode = str(row["result_mode"])
        if mode not in {"proxy_2c", "official_2c_subset"}:
            continue
        rows.append(
            {
                "candidate": str(row["rule"]),
                "source_family": "regime_live_ws",
                "slice": f"live_ws_{mode}",
                "trades": int(row["trades"]),
                "pnl": float(row["pnl"]),
                "win_rate": float(row["win_rate"]),
                "max_dd": float(row["max_dd"]),
                "sharpe": float(row["sharpe"]),
                "path": str(path.relative_to(PROJECT_ROOT)),
            }
        )
    return pd.DataFrame(rows)


def promotion_table(evidence: pd.DataFrame, min_train_trades: int, min_live_trades: int) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate, group in evidence.groupby("candidate", sort=True):
        by_slice = {str(r["slice"]): r for _, r in group.iterrows()}
        def val(slice_name: str, field: str, default: object = float("nan")) -> object:
            row = by_slice.get(slice_name)
            return row[field] if row is not None and field in row.index else default

        required_slices = ["apr1_14_train", "apr15_30_val", "may1_12_val"]
        positive_required = []
        for slice_name in required_slices:
            if slice_name in by_slice:
                positive_required.append(float(val(slice_name, "pnl")) > 0)
            else:
                positive_required.append(False)

        jan_ok = True
        if "jan_proxy_external" in by_slice:
            jan_ok = float(val("jan_proxy_external", "pnl")) >= 0

        live_proxy_ok = False
        if "live_ws_proxy_2c" in by_slice:
            live_proxy_ok = (
                int(val("live_ws_proxy_2c", "trades", 0)) >= min_live_trades
                and float(val("live_ws_proxy_2c", "pnl")) >= 0
            )

        live_official_ok = True
        if "live_ws_official_2c_subset" in by_slice and int(val("live_ws_official_2c_subset", "trades", 0)) >= min_live_trades:
            live_official_ok = float(val("live_ws_official_2c_subset", "pnl")) >= 0

        train_trades_ok = int(val("apr1_14_train", "trades", 0)) >= min_train_trades
        passes = all(positive_required) and jan_ok and live_proxy_ok and live_official_ok and train_trades_ok

        failure_reasons = []
        if not train_trades_ok:
            failure_reasons.append("too_few_train_trades")
        for slice_name, ok in zip(required_slices, positive_required):
            if not ok:
                failure_reasons.append(f"{slice_name}_not_positive")
        if not jan_ok:
            failure_reasons.append("jan_proxy_not_positive")
        if "live_ws_proxy_2c" not in by_slice:
            failure_reasons.append("missing_live_ws_proxy")
        elif not live_proxy_ok:
            failure_reasons.append("live_ws_proxy_not_positive_or_too_few")
        if not live_official_ok:
            failure_reasons.append("live_ws_official_negative")

        rows.append(
            {
                "candidate": candidate,
                "train_trades": val("apr1_14_train", "trades", 0),
                "train_pnl": val("apr1_14_train", "pnl"),
                "apr15_30_trades": val("apr15_30_val", "trades", 0),
                "apr15_30_pnl": val("apr15_30_val", "pnl"),
                "may1_12_trades": val("may1_12_val", "trades", 0),
                "may1_12_pnl": val("may1_12_val", "pnl"),
                "jan_trades": val("jan_proxy_external", "trades", 0),
                "jan_pnl": val("jan_proxy_external", "pnl"),
                "live_proxy_trades": val("live_ws_proxy_2c", "trades", 0),
                "live_proxy_pnl": val("live_ws_proxy_2c", "pnl"),
                "live_official_trades": val("live_ws_official_2c_subset", "trades", 0),
                "live_official_pnl": val("live_ws_official_2c_subset", "pnl"),
                "passes_promotion_gate": passes,
                "failure_reasons": ";".join(failure_reasons),
            }
        )
    out = pd.DataFrame(rows)
    out["sort_score"] = (
        pd.to_numeric(out["train_pnl"], errors="coerce").fillna(-999)
        + pd.to_numeric(out["apr15_30_pnl"], errors="coerce").fillna(-999)
        + pd.to_numeric(out["may1_12_pnl"], errors="coerce").fillna(-999)
        + pd.to_numeric(out["live_proxy_pnl"], errors="coerce").fillna(-999)
    )
    return out.sort_values(["passes_promotion_gate", "sort_score"], ascending=[False, False]).drop(columns=["sort_score"])


def markdown_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    show = df.head(max_rows).copy()
    for col in show.columns:
        if pd.api.types.is_float_dtype(show[col]):
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
        else:
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), int(show[col].astype(str).map(len).max())) for col in show.columns}
    lines = ["| " + " | ".join(col.ljust(widths[col]) for col in show.columns) + " |"]
    lines.append("| " + " | ".join("-" * widths[col] for col in show.columns) + " |")
    for _, row in show.iterrows():
        lines.append("| " + " | ".join(str(row[col]).ljust(widths[col]) for col in show.columns) + " |")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "apr1_14": latest_dir("btc15m_f2_robust_apr1_14_fresh_*"),
        "apr15_30": latest_dir("btc15m_f2_predexon_apr15_30_rerun_*"),
        "may1_12": latest_dir("btc15m_f2_predexon_may1_12_rerun_*"),
        "jan": latest_dir("btc15m_f2_predexon_jan08_09_partial_*"),
        "live_f2": latest_dir("btc15m_f2_exact_live_ws_refresh_*"),
        "regime": latest_dir("btc15m_regime_rule_family_jan0809ext_*"),
        "regime_live": latest_dir("btc15m_regime_live_ws_*"),
    }

    evidence_frames = [
        read_f2_summary(paths["apr1_14"], "apr1_14_train"),
        read_f2_summary(paths["apr15_30"], "apr15_30_val"),
        read_f2_summary(paths["may1_12"], "may1_12_val"),
        read_f2_summary(paths["jan"], "jan_proxy_external"),
        read_live_summary(paths["live_f2"]),
    ]
    regime = read_regime_wide(paths["regime"])
    regime_live = read_regime_live(paths["regime_live"])
    regime = regime[regime["candidate"].isin(REGIME_TO_LIVE.keys())].copy()
    regime_live = regime_live[regime_live["candidate"].isin(set(REGIME_TO_LIVE.values()))].copy()
    evidence_frames.extend([regime, regime_live])

    evidence = pd.concat(evidence_frames, ignore_index=True)
    table = promotion_table(evidence, args.min_train_trades, args.min_live_trades)

    evidence.to_csv(args.out_dir / "slice_evidence.csv", index=False)
    table.to_csv(args.out_dir / "promotion_gate.csv", index=False)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "paths": {k: str(v.relative_to(PROJECT_ROOT)) for k, v in paths.items()},
        "min_train_trades": args.min_train_trades,
        "min_live_trades": args.min_live_trades,
        "rule": "No candidate is promotable unless it is positive on Apr1-14, Apr15-30, May1-12, Jan proxy when available, and live websocket proxy replay.",
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    passed = table[table["passes_promotion_gate"].astype(bool)]
    report = [
        "# BTC15M Transfer Gate Audit",
        "",
        "This audit consolidates frozen BTC15M candidates across train, validation, outside-time, and live websocket evidence. It does not search or tune thresholds.",
        "",
        "## Verdict",
        "",
        f"Candidates passing promotion gate: `{len(passed)}`.",
    ]
    if passed.empty:
        report.append("")
        report.append("No BTC15M F2/regime candidate is deployable under this conservative gate. The common failure is positive April performance followed by May or live websocket weakness.")
    report.extend(
        [
            "",
            "## Promotion Gate Table",
            "",
            markdown_table(table, max_rows=40),
            "",
            "## Input Artifacts",
            "",
        ]
    )
    for key, path in paths.items():
        report.append(f"- `{key}`: `{path.relative_to(PROJECT_ROOT)}`")
    (args.out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(markdown_table(table, max_rows=40))
    print(f"Wrote {args.out_dir}")


if __name__ == "__main__":
    main()
