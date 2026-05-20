#!/usr/bin/env python3
"""Research structural BTC15M candidate gates from Predexon snapshot backtests.

This consumes `btc15m_ml_candidates.parquet` emitted by
`backtest_predexon_orderbooks.py`. It does not create new labels or use future
quote values in decisions; every hypothesis uses only fields already available
at the candidate timestamp, then scores against the known settled result.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402
from scripts.backtest_predexon_orderbooks import max_drawdown_from_pnl  # noqa: E402


@dataclass(frozen=True)
class Hypothesis:
    name: str
    description: str
    mask: Callable[[pd.DataFrame], pd.Series]
    score: Callable[[pd.DataFrame], pd.Series]


def latest_candidates() -> Path:
    paths = sorted(
        PROJECT_ROOT.glob("backtest_outputs/predexon_research_*/btc15m_ml_candidates.parquet"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not paths:
        raise SystemExit("No btc15m_ml_candidates.parquet found. Run backtest_predexon_orderbooks.py first.")
    return paths[0]


def utc(values) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def datetime_ns(values) -> np.ndarray:
    return pd.to_datetime(values, utc=True, errors="coerce").astype("datetime64[ns, UTC]").astype("int64").to_numpy()


def split_events(df: pd.DataFrame) -> pd.DataFrame:
    events = (
        df[["event_ticker", "close_time"]]
        .drop_duplicates("event_ticker")
        .sort_values("close_time")
        .reset_index(drop=True)
    )
    n = len(events)
    train_end = max(1, int(n * 0.60))
    val_end = max(train_end + 1, int(n * 0.80))
    events["split"] = "test"
    events.loc[: train_end - 1, "split"] = "train"
    events.loc[train_end : val_end - 1, "split"] = "validation"
    return df.merge(events[["event_ticker", "split"]], on="event_ticker", how="left")


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["available_at", "close_time"]:
        out[col] = utc(out[col])
    num_cols = [
        "entry_price",
        "entry_fee",
        "spread_cents",
        "visible_qty",
        "rr",
        "ttl_min",
        "btc_ret_1m_bps",
        "btc_ret_3m_bps",
        "btc_ret_5m_bps",
        "quote_speed_cents",
        "book_imbalance",
        "yes_mid",
        "side_mid_chg_1m",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "yes_mid_chg_0_5m",
        "yes_mid_chg_1_0m",
        "yes_mid_chg_2_0m",
        "yes_mid_chg_3_0m",
        "bid_depth",
        "ask_depth",
    ]
    for col in num_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = np.nan
    out["side"] = out["side"].astype(str).str.lower()
    out["win"] = pd.to_numeric(out["win"], errors="coerce").fillna(0).astype(int)
    # Recompute fee/pnl defensively so this script is not tied to an older backtest artifact.
    out["entry_fee"] = out["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker"))
    out["pnl"] = out["win"].astype(float) - out["entry_price"].astype(float) - out["entry_fee"].astype(float)
    out["premium"] = out["entry_price"].astype(float) + out["entry_fee"].astype(float)
    out["side_btc_3m"] = np.where(out["side"].eq("yes"), out["btc_ret_3m_bps"], -out["btc_ret_3m_bps"])
    out["side_btc_5m"] = np.where(out["side"].eq("yes"), out["btc_ret_5m_bps"], -out["btc_ret_5m_bps"])
    out["side_book_imbalance"] = out["book_imbalance"]
    out = out.dropna(subset=["event_ticker", "available_at", "close_time", "entry_price", "pnl"])
    out = split_events(out)
    return out.sort_values(["event_ticker", "available_at"]).reset_index(drop=True)


def first_per_event(df: pd.DataFrame, score: pd.Series | None = None, ascending: bool = False) -> pd.DataFrame:
    if df.empty:
        return df
    tmp = df.copy()
    tmp["_score"] = score.loc[tmp.index] if score is not None else -datetime_ns(tmp["available_at"])
    return (
        tmp.sort_values(["event_ticker", "_score", "available_at"], ascending=[True, ascending, True])
        .drop_duplicates("event_ticker", keep="first")
        .drop(columns=["_score"], errors="ignore")
        .reset_index(drop=True)
    )


def first_chronological(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return (
        df.sort_values(["event_ticker", "available_at", "side"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def side_counts(rows: pd.DataFrame) -> tuple[int, int]:
    if rows.empty or "side" not in rows.columns:
        return 0, 0
    pair_mask = rows["side"].eq("pair") if "side" in rows.columns else pd.Series(False, index=rows.index)
    if "legs" in rows.columns:
        yes_pairs = int((pair_mask & rows["legs"].astype(str).str.contains("yes", case=False, na=False)).sum())
        no_pairs = int((pair_mask & rows["legs"].astype(str).str.contains("no", case=False, na=False)).sum())
    else:
        yes_pairs = no_pairs = 0
    return int(rows["side"].eq("yes").sum()) + yes_pairs, int(rows["side"].eq("no").sum()) + no_pairs


def summarize(name: str, description: str, rows: pd.DataFrame) -> dict:
    pnl = rows["pnl"].astype(float) if not rows.empty else pd.Series(dtype=float)
    premium = rows["premium"].astype(float) if not rows.empty else pd.Series(dtype=float)
    return {
        "hypothesis": name,
        "description": description,
        "trades": int(len(rows)),
        "events": int(rows["event_ticker"].nunique()) if not rows.empty else 0,
        "pnl_1_contract": float(pnl.sum()) if len(pnl) else 0.0,
        "return_on_100": float(pnl.sum() / 100.0) if len(pnl) else 0.0,
        "premium": float(premium.sum()) if len(premium) else 0.0,
        "return_on_premium": float(pnl.sum() / premium.sum()) if len(premium) and premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "max_dd": max_drawdown_from_pnl(pnl) if len(pnl) else 0.0,
        "sharpe": trade_sharpe(pnl) if len(pnl) else 0.0,
        "avg_entry": float(rows["entry_price"].mean()) if not rows.empty else np.nan,
        "avg_rr": float(rows["rr"].mean()) if not rows.empty else np.nan,
        "yes_trades": side_counts(rows)[0] if not rows.empty else 0,
        "no_trades": side_counts(rows)[1] if not rows.empty else 0,
        "first_entry": str(rows["available_at"].min()) if not rows.empty else "",
        "last_entry": str(rows["available_at"].max()) if not rows.empty else "",
    }


def pair_row(first: pd.Series, second: pd.Series, name: str) -> dict:
    total_premium = float(first["premium"]) + float(second["premium"])
    pnl = 1.0 - total_premium
    second_is_later = pd.Timestamp(second["available_at"]) >= pd.Timestamp(first["available_at"])
    later = second if second_is_later else first
    earlier = first if second_is_later else second
    row = later.to_dict()
    row.update(
        {
            "hypothesis": name,
            "side": "pair",
            "legs": f"{str(earlier['side'])}->{str(later['side'])}",
            "first_leg_side": str(earlier["side"]),
            "second_leg_side": str(later["side"]),
            "first_leg_time": earlier["available_at"],
            "second_leg_time": later["available_at"],
            "first_leg_price": float(earlier["entry_price"]),
            "second_leg_price": float(later["entry_price"]),
            "entry_price": total_premium,
            "entry_fee": float(first["entry_fee"]) + float(second["entry_fee"]),
            "premium": total_premium,
            "rr": np.inf if total_premium <= 0 else pnl / total_premium,
            "win": int(pnl > 0),
            "pnl": pnl,
            "lock_profit": pnl,
            "available_at": later["available_at"],
            "split": later["split"],
        }
    )
    return row


def cheap_tail_candidates(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        df["side"].isin(["yes", "no"])
        & df["entry_price"].le(0.60)
        & df["rr"].ge(0.50)
    ].copy()


def cheap_pair_lock(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if candidates.empty:
        return pd.DataFrame()
    for _, group in candidates.sort_values(["available_at", "side"]).groupby("event_ticker"):
        first = group.iloc[0]
        opposite = group[(group["side"].ne(first["side"])) & (group["available_at"].gt(first["available_at"]))]
        for _, second in opposite.iterrows():
            if float(first["premium"]) + float(second["premium"]) < 1.0:
                rows.append(pair_row(first, second, "cheap_pair_lock_rr"))
                break
    return pd.DataFrame(rows)


def cheap_tail_position_aware(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if candidates.empty:
        return pd.DataFrame()
    for _, group in candidates.sort_values(["available_at", "side"]).groupby("event_ticker"):
        first = group.iloc[0]
        opposite = group[(group["side"].ne(first["side"])) & (group["available_at"].gt(first["available_at"]))]
        locked = False
        for _, second in opposite.iterrows():
            if float(first["premium"]) + float(second["premium"]) < 1.0:
                rows.append(pair_row(first, second, "cheap_tail_position_aware"))
                locked = True
                break
        if not locked:
            row = first.to_dict()
            row["hypothesis"] = "cheap_tail_position_aware"
            row["legs"] = str(first["side"])
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["available_at", "event_ticker"]).reset_index(drop=True) if rows else pd.DataFrame()


def hypotheses(df: pd.DataFrame) -> list[Hypothesis]:
    true = lambda d: pd.Series(True, index=d.index)
    return [
        Hypothesis("earliest_any", "first executable candidate per event", true, lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("no_only_earliest", "NO side only, earliest per event", lambda d: d["side"].eq("no"), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("yes_only_earliest", "YES side only, earliest per event", lambda d: d["side"].eq("yes"), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("entry_le_60", "entry <= 60c", lambda d: d["entry_price"].le(0.60), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("entry_10_60", "10c <= entry <= 60c", lambda d: d["entry_price"].between(0.10, 0.60), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("entry_20_70", "20c <= entry <= 70c", lambda d: d["entry_price"].between(0.20, 0.70), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("rr_ge_033", "risk/reward >= 0.33", lambda d: d["rr"].ge(0.33), lambda d: d["rr"]),
        Hypothesis("rr_ge_050", "risk/reward >= 0.50", lambda d: d["rr"].ge(0.50), lambda d: d["rr"]),
        Hypothesis("rr_ge_100", "risk/reward >= 1.00", lambda d: d["rr"].ge(1.00), lambda d: d["rr"]),
        Hypothesis("rr_ge_200", "risk/reward >= 2.00", lambda d: d["rr"].ge(2.00), lambda d: d["rr"]),
        Hypothesis("visible_qty_ge_50", "visible top quantity >= 50", lambda d: d["visible_qty"].ge(50), lambda d: d["visible_qty"]),
        Hypothesis("visible_qty_ge_250", "visible top quantity >= 250", lambda d: d["visible_qty"].ge(250), lambda d: d["visible_qty"]),
        Hypothesis("spread_le_1", "spread <= 1c", lambda d: d["spread_cents"].le(1), lambda d: -d["spread_cents"]),
        Hypothesis("spread_le_2", "spread <= 2c", lambda d: d["spread_cents"].le(2), lambda d: -d["spread_cents"]),
        Hypothesis("ttl_2_to_5", "2 to 5 minutes before close", lambda d: d["ttl_min"].between(2, 5), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("ttl_4_to_7", "4 to 7 minutes before close", lambda d: d["ttl_min"].between(4, 7), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("ttl_6_to_9", "6 to 9 minutes before close", lambda d: d["ttl_min"].between(6, 9), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("btc_3m_aligned", "side-aligned BTC 3m return", lambda d: d["side_btc_3m"].ge(0), lambda d: d["side_btc_3m"]),
        Hypothesis("btc_3m_contra", "side-contrarian BTC 3m return", lambda d: d["side_btc_3m"].le(0), lambda d: -d["side_btc_3m"]),
        Hypothesis("btc_5m_aligned", "side-aligned BTC 5m return", lambda d: d["side_btc_5m"].ge(0), lambda d: d["side_btc_5m"]),
        Hypothesis("quote_speed_low", "quote speed <= 5c", lambda d: d["quote_speed_cents"].le(5), lambda d: -d["quote_speed_cents"]),
        Hypothesis("book_imbalance_nonnegative", "book imbalance not against side", lambda d: d["side_book_imbalance"].ge(0), lambda d: d["side_book_imbalance"]),
        Hypothesis("cheap_no_rr_first", "NO, first entry <= 60c, RR >= 0.5", lambda d: d["side"].eq("no") & d["entry_price"].le(0.60) & d["rr"].ge(0.5), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("cheap_yes_rr_first", "YES, first entry <= 60c, RR >= 0.5", lambda d: d["side"].eq("yes") & d["entry_price"].le(0.60) & d["rr"].ge(0.5), lambda d: -pd.Series(datetime_ns(d["available_at"]), index=d.index)),
        Hypothesis("liquid_low_spread_rr", "spread <= 1c, qty >= 50, RR >= 0.5", lambda d: d["spread_cents"].le(1) & d["visible_qty"].ge(50) & d["rr"].ge(0.5), lambda d: d["rr"]),
        Hypothesis("mid_market_30_70", "entry 30c to 70c and spread <= 2c", lambda d: d["entry_price"].between(0.30, 0.70) & d["spread_cents"].le(2), lambda d: d["rr"]),
    ]


def run_hypotheses(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries = []
    picked_rows = []
    for hyp in hypotheses(df):
        mask = hyp.mask(df).fillna(False)
        eligible = df[mask].copy()
        score = hyp.score(df)
        picked = first_per_event(eligible, score=score, ascending=False)
        picked["hypothesis"] = hyp.name
        picked_rows.append(picked)
        for split in ["all", "train", "validation", "test"]:
            sample = picked if split == "all" else picked[picked["split"].eq(split)]
            row = summarize(hyp.name, hyp.description, sample)
            row["split"] = split
            summaries.append(row)

    cheap = cheap_tail_candidates(df)
    derived = [
        (
            "cheap_tail_best_side_first",
            "first cheap-tail side only; no same-market offset",
            first_chronological(cheap).assign(hypothesis="cheap_tail_best_side_first") if not cheap.empty else pd.DataFrame(),
        ),
        (
            "cheap_pair_lock_rr",
            "buy first cheap side, then only buy opposite side if YES+NO all-in cost < $1",
            cheap_pair_lock(cheap),
        ),
        (
            "cheap_tail_position_aware",
            "first cheap side; later opposite side only locks a profitable pair, otherwise hold first side",
            cheap_tail_position_aware(cheap),
        ),
    ]
    for name, description, picked in derived:
        if not picked.empty:
            picked["hypothesis"] = name
            picked_rows.append(picked)
        for split in ["all", "train", "validation", "test"]:
            sample = picked if split == "all" else picked[picked["split"].eq(split)] if not picked.empty and "split" in picked.columns else pd.DataFrame()
            row = summarize(name, description, sample)
            row["split"] = split
            summaries.append(row)
    return pd.DataFrame(summaries), pd.concat(picked_rows, ignore_index=True) if picked_rows else pd.DataFrame()


def run_ml(df: pd.DataFrame, out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df["event_ticker"].nunique() < 50:
        return pd.DataFrame(), pd.DataFrame()
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        print(f"skipping ML: {exc!r}", flush=True)
        return pd.DataFrame(), pd.DataFrame()

    features = [
        "entry_price",
        "spread_cents",
        "visible_qty",
        "rr",
        "ttl_min",
        "btc_ret_1m_bps",
        "btc_ret_3m_bps",
        "btc_ret_5m_bps",
        "side_btc_3m",
        "side_btc_5m",
        "quote_speed_cents",
        "book_imbalance",
        "side_book_imbalance",
        "yes_mid",
        "bid_depth",
        "ask_depth",
    ]
    work = df.dropna(subset=["split", "win"]).copy()
    train = work[work["split"].eq("train")]
    val = work[work["split"].eq("validation")]
    test = work[work["split"].eq("test")]
    if train["event_ticker"].nunique() < 20 or val["event_ticker"].nunique() < 8 or test["event_ticker"].nunique() < 8:
        return pd.DataFrame(), pd.DataFrame()
    models = {
        "hgb_structural_15m": make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(max_iter=100, learning_rate=0.05, max_leaf_nodes=8, l2_regularization=0.20, random_state=14)),
        "mlp_structural_15m": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), MLPClassifier(hidden_layer_sizes=(16, 8), alpha=0.05, early_stopping=True, max_iter=250, random_state=14)),
    }
    rows = []
    trades = []
    for name, model in models.items():
        model.fit(train[features], train["win"].astype(int))
        val_scored = val.copy()
        val_scored["pred_win_prob"] = model.predict_proba(val[features])[:, 1]
        best_thr = 0.60
        best_score = -1e9
        for thr in np.arange(0.54, 0.82, 0.02):
            picked = first_per_event(val_scored[val_scored["pred_win_prob"].ge(thr)], score=val_scored["pred_win_prob"], ascending=False)
            if len(picked) < 3:
                continue
            pnl = picked["pnl"].astype(float)
            score = float(pnl.sum() + max_drawdown_from_pnl(pnl))
            if score > best_score:
                best_score = score
                best_thr = float(thr)
        test_scored = test.copy()
        test_scored["pred_win_prob"] = model.predict_proba(test[features])[:, 1]
        picked = first_per_event(test_scored[test_scored["pred_win_prob"].ge(best_thr)], score=test_scored["pred_win_prob"], ascending=False)
        picked["hypothesis"] = name
        trades.append(picked)
        row = summarize(name, f"event-split ML gate, threshold selected on validation ({best_thr:.2f})", picked)
        row["split"] = "test"
        row["threshold"] = best_thr
        row["train_events"] = int(train["event_ticker"].nunique())
        row["validation_events"] = int(val["event_ticker"].nunique())
        row["test_events"] = int(test["event_ticker"].nunique())
        rows.append(row)
    return pd.DataFrame(rows), pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()


def markdown_or_text(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", type=Path, default=None)
    p.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / f"predexon_structural_15m_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    args = p.parse_args()
    path = args.candidates or latest_candidates()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    df = prepare(pd.read_parquet(path))
    summary, trades = run_hypotheses(df)
    ml_summary, ml_trades = run_ml(df, args.output_dir)
    if not ml_summary.empty:
        summary = pd.concat([summary, ml_summary], ignore_index=True, sort=False)
    if not ml_trades.empty:
        trades = pd.concat([trades, ml_trades], ignore_index=True, sort=False)
    summary = summary.sort_values(["split", "pnl_1_contract", "sharpe"], ascending=[True, False, False]).reset_index(drop=True)
    summary.to_csv(args.output_dir / "structural_hypotheses_summary.csv", index=False)
    trades.to_csv(args.output_dir / "structural_hypotheses_trades.csv", index=False)
    top_test = summary[summary["split"].eq("test")].sort_values(["pnl_1_contract", "max_dd"], ascending=[False, False]).head(15)
    report = [
        "# Predexon BTC15M Structural Candidate Research",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Candidate file: `{path}`",
        f"Rows: {len(df):,}; events: {df['event_ticker'].nunique():,}; time span: `{df['available_at'].min()}` -> `{df['available_at'].max()}`.",
        "",
        "## Top Test Hypotheses",
        "",
        markdown_or_text(top_test),
        "",
        "## Fidelity Notes",
        "",
        "- Event split is chronological by event close time.",
        "- Filters and scores use only candidate-time features.",
        "- This is not a deployment result unless the same rule survives on the live websocket capture holdout.",
    ]
    (args.output_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    ledger = PROJECT_ROOT / "docs" / "2026-05-14_predexon_research.md"
    with ledger.open("a", encoding="utf-8") as f:
        f.write(
            "\n\n## BTC15M Structural Candidate Run\n"
            f"- Output: `{args.output_dir}`\n"
            f"- Candidate rows: {len(df):,}; events: {df['event_ticker'].nunique():,}; source: `{path}`.\n"
            "- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.\n"
        )
    print(markdown_or_text(top_test))
    print(f"Wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
