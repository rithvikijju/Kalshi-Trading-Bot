#!/usr/bin/env python3
"""Research $100 scaling rules for the BTC 1-hour research signal.

The script intentionally reuses the already-selected research signal stream from
the corrected DuckDB replay. It does not search for new signals. It only asks:
given the same entries, how should a $100 bankroll size them?
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.risk_adjusted_research import RiskSizingConfig, choose_risk_adjusted_contracts
DEFAULT_HISTORICAL = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "risk_adjusted_research_duckdb_20260506_114616"
    / "duckdb"
    / "fixed_1_trades.csv"
)
DEFAULT_LIVE_DB = Path.home() / ".btc_kalshi_bot" / "research_live_trades.db"
TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")
CURRENT_LIVE_PROCESS_START = pd.Timestamp("2026-05-08T04:46:29Z")


@dataclass(frozen=True)
class Candidate:
    name: str
    max_contracts: int
    no_side_contract_cap: int
    kelly_fraction: float
    edge_confidence: float
    medium_entry_cap: float
    high_entry_cap: float
    max_per_market_fraction: float = 0.20
    max_total_exposure_fraction: float = 0.50
    scale_min_edge_cents: float | None = None
    scale_side: str | None = None

    def config(self, bankroll: float) -> RiskSizingConfig:
        return RiskSizingConfig(
            starting_bankroll=bankroll,
            max_contracts=self.max_contracts,
            max_per_market_fraction=self.max_per_market_fraction,
            max_total_exposure_fraction=self.max_total_exposure_fraction,
            kelly_fraction=self.kelly_fraction,
            edge_confidence=self.edge_confidence,
            medium_entry_cap=self.medium_entry_cap,
            high_entry_cap=self.high_entry_cap,
            no_side_contract_cap=self.no_side_contract_cap,
        )


CURRENT_CANDIDATE = Candidate(
        "current",
        max_contracts=3,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.55,
        high_entry_cap=0.65,
)


CANDIDATES = [
    CURRENT_CANDIDATE,
    Candidate(
        "yes_scale_no1_k25",
        max_contracts=5,
        no_side_contract_cap=1,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
    ),
    Candidate(
        "yes_scale_no1_k35",
        max_contracts=5,
        no_side_contract_cap=1,
        kelly_fraction=0.35,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
    ),
    Candidate(
        "yes_scale_no2_k25",
        max_contracts=5,
        no_side_contract_cap=2,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
    ),
    Candidate(
        "yes_scale_no2_k35",
        max_contracts=5,
        no_side_contract_cap=2,
        kelly_fraction=0.35,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
    ),
    Candidate(
        "yes_scale_no1_lowconf",
        max_contracts=5,
        no_side_contract_cap=1,
        kelly_fraction=0.30,
        edge_confidence=0.35,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
    ),
    Candidate(
        "wide_current_no2",
        max_contracts=5,
        no_side_contract_cap=2,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.65,
        high_entry_cap=0.80,
    ),
    Candidate(
        "aggressive_yes_no1",
        max_contracts=6,
        no_side_contract_cap=1,
        kelly_fraction=0.40,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.90,
        max_total_exposure_fraction=0.40,
    ),
    Candidate(
        "edge16_scale_any_no3_k25",
        max_contracts=5,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=16.0,
    ),
    Candidate(
        "edge17_scale_any_no3_k25",
        max_contracts=5,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=17.0,
    ),
    Candidate(
        "edge18_scale_any_no3_k25",
        max_contracts=5,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=18.0,
    ),
    Candidate(
        "edge16_scale_no_only_no3_k25",
        max_contracts=5,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=16.0,
        scale_side="no",
    ),
    Candidate(
        "edge17_scale_no_only_no3_k25",
        max_contracts=5,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=17.0,
        scale_side="no",
    ),
    Candidate(
        "edge18_scale_no_only_no3_k25",
        max_contracts=5,
        no_side_contract_cap=3,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=18.0,
        scale_side="no",
    ),
    Candidate(
        "edge16_scale_no_only_no2_k25",
        max_contracts=5,
        no_side_contract_cap=2,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.75,
        high_entry_cap=0.85,
        scale_min_edge_cents=16.0,
        scale_side="no",
    ),
]


class OfficialResults:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        if cache_path.exists():
            self.cache: dict[str, dict[str, Any]] = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            self.cache = {}
        self.session = requests.Session()

    def get(self, ticker: str) -> dict[str, Any]:
        ticker = str(ticker).upper()
        cached = self.cache.get(ticker)
        if cached and cached.get("fetched_at"):
            return cached
        out: dict[str, Any]
        try:
            response = self.session.get(
                f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}",
                timeout=10,
            )
            if not response.ok:
                out = {"result": None, "status": f"http{response.status_code}", "expiration_value": None}
            else:
                market = response.json().get("market", {})
                value = market.get("expiration_value")
                try:
                    value = float(value) if value is not None else None
                except Exception:
                    value = None
                out = {
                    "result": (market.get("result") or "").lower() or None,
                    "status": market.get("status"),
                    "expiration_value": value,
                    "close_time": market.get("close_time"),
                }
        except Exception as exc:
            out = {"result": None, "status": type(exc).__name__, "expiration_value": None}
        out["fetched_at"] = datetime.now(timezone.utc).isoformat()
        self.cache[ticker] = out
        time.sleep(0.015)
        return out

    def write(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")


def split_name(close_time: pd.Timestamp) -> str:
    close_time = pd.Timestamp(close_time).tz_convert("UTC")
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def normalize_historical(path: Path, official: OfficialResults) -> tuple[pd.DataFrame, dict[str, Any]]:
    trades = pd.read_csv(path)
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["close_time"] = pd.to_datetime(trades["close_time"], utc=True)
    rows = []
    known = 0
    mismatches = 0
    unknown = 0
    for _, row in trades.iterrows():
        ticker = str(row["market_ticker"]).upper()
        proxy = str(row["settlement"]).lower()
        official_row = official.get(ticker)
        result = official_row.get("result")
        if result in {"yes", "no"}:
            known += 1
            mismatches += int(result != proxy)
        else:
            unknown += 1
            result = proxy
        rows.append({**row.to_dict(), "official_or_proxy_settlement": result, "split": split_name(row["close_time"])})
    out = pd.DataFrame(rows)
    return out, {
        "rows": int(len(out)),
        "official_known": int(known),
        "official_unknown": int(unknown),
        "official_proxy_mismatches": int(mismatches),
    }


def normalize_live(path: Path, official: OfficialResults, cutoff: pd.Timestamp | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        return pd.DataFrame(), {"rows": 0, "error": f"missing {path}"}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute("SELECT * FROM research_live_trades ORDER BY created_at, id")]
    conn.close()
    out = []
    open_count = 0
    for row in rows:
        if "filled" not in str(row.get("status", "")).lower():
            continue
        entry_time = pd.Timestamp(row["created_at"]).tz_convert("UTC")
        if cutoff is not None and entry_time < cutoff:
            continue
        official_row = official.get(row["market_ticker"])
        result = official_row.get("result")
        if result not in {"yes", "no"}:
            open_count += 1
            continue
        price = row.get("actual_entry_price")
        if price is None:
            price = row.get("entry_price")
        fee = row.get("actual_fee_paid")
        if fee is None:
            fee = row.get("entry_fee_estimate")
        out.append(
            {
                "event_ticker": str(row["event_ticker"]).upper(),
                "market_ticker": str(row["market_ticker"]).upper(),
                "side": str(row["side"]).lower(),
                "entry_time": entry_time,
                "close_time": pd.Timestamp(row["close_time"]).tz_convert("UTC"),
                "entry_price": float(price),
                "entry_fee": float(fee or 0.0),
                "contracts": int(row["contracts"]),
                "model_p_yes": float(row.get("model_p_yes") or 0.5),
                "net_edge_cents": float(row.get("net_edge_cents") or 0.0),
                "official_or_proxy_settlement": result,
                "split": "live",
            }
        )
    return pd.DataFrame(out), {"rows": len(rows), "settled_fills": len(out), "open_fills": open_count}


def drawdown(pnls: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    return max_dd


def unit_cost(entry_price: float, contracts: int) -> tuple[float, float]:
    fee = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
    return entry_price * contracts + fee, fee


def effective_candidate(candidate: Candidate, row: dict[str, Any]) -> Candidate:
    """Use the candidate only on its gated rows; otherwise keep current sizing."""
    if candidate.scale_min_edge_cents is None and not candidate.scale_side:
        return candidate

    try:
        edge = float(row.get("net_edge_cents", 0.0) or 0.0)
    except Exception:
        edge = 0.0
    side = str(row.get("side", "")).lower()
    if candidate.scale_min_edge_cents is not None and edge < candidate.scale_min_edge_cents:
        return CURRENT_CANDIDATE
    if candidate.scale_side and side != candidate.scale_side.lower():
        return CURRENT_CANDIDATE
    return candidate


def simulate(frame: pd.DataFrame, candidate: Candidate, bankroll: float) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    cash = bankroll
    open_positions: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    for _, row_obj in frame.sort_values(["entry_time", "market_ticker"]).iterrows():
        row = row_obj.to_dict()
        row_candidate = effective_candidate(candidate, row)
        config = row_candidate.config(bankroll)
        now = pd.Timestamp(row["entry_time"])
        still_open = []
        for pos in open_positions:
            if pd.Timestamp(pos["close_time"]) <= now:
                cash += float(pos["payout"])
                settled.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open
        active = sum(float(pos["premium"]) for pos in open_positions)
        decision = choose_risk_adjusted_contracts(
            entry_price=float(row["entry_price"]),
            model_p_yes=float(row.get("model_p_yes", 0.5)),
            side=str(row["side"]),
            bankroll=max(0.0, cash + active),
            available_cash=max(0.0, cash),
            active_exposure=active,
            config=config,
            available_qty=float(row.get("available_qty", 999) or 999),
            liquidity="taker",
            net_edge_cents=float(row.get("net_edge_cents", 0.0) or 0.0),
        )
        if decision.contracts <= 0:
            continue
        premium, fee = unit_cost(float(row["entry_price"]), decision.contracts)
        result = str(row["official_or_proxy_settlement"]).lower()
        payout = decision.contracts * (1.0 if str(row["side"]).lower() == result else 0.0)
        cash -= premium
        open_positions.append(
            {
                "candidate": candidate.name,
                "entry_time": row["entry_time"],
                "close_time": row["close_time"],
                "split": row.get("split", ""),
                "event_ticker": row["event_ticker"],
                "market_ticker": row["market_ticker"],
                "side": str(row["side"]).lower(),
                "settlement": result,
                "entry_price": float(row["entry_price"]),
                "model_p_yes": float(row.get("model_p_yes", 0.5)),
                "net_edge_cents": float(row.get("net_edge_cents", 0.0)),
                "contracts": decision.contracts,
                "fee": fee,
                "premium": premium,
                "payout": payout,
                "pnl": payout - premium,
                "sizing_reason": decision.reason,
                "sizing_policy": row_candidate.name,
                "sizing_budget": decision.budget,
                "sizing_risk_budget": decision.risk_budget,
                "sizing_full_kelly_fraction": decision.full_kelly_fraction,
                "sizing_applied_kelly_fraction": decision.applied_kelly_fraction,
            }
        )
    for pos in open_positions:
        cash += float(pos["payout"])
        settled.append(pos)
    return pd.DataFrame(settled).sort_values(["close_time", "entry_time", "market_ticker"]).reset_index(drop=True)


def summarize(trades: pd.DataFrame, source: str, candidate: Candidate, split: str, bankroll: float) -> dict[str, Any]:
    if trades.empty:
        return {
            "source": source,
            "candidate": candidate.name,
            "split": split,
            "trades": 0,
            "contracts": 0,
            "premium": 0.0,
            "pnl": 0.0,
            "return_on_start": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "yes_contracts": 0,
            "no_contracts": 0,
            **{f"cfg_{key}": value for key, value in asdict(candidate).items()},
        }
    pnl = trades["pnl"].astype(float)
    premium = trades["premium"].astype(float)
    return {
        "source": source,
        "candidate": candidate.name,
        "split": split,
        "trades": int(len(trades)),
        "contracts": int(trades["contracts"].sum()),
        "premium": float(premium.sum()),
        "pnl": float(pnl.sum()),
        "return_on_start": float(pnl.sum() / bankroll) if bankroll else 0.0,
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": float(pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum())) if (pnl < 0).any() else math.inf,
        "max_drawdown": drawdown(pnl.tolist()),
        "avg_contracts": float(trades["contracts"].mean()),
        "max_contracts_seen": int(trades["contracts"].max()),
        "yes_contracts": int(trades.loc[trades["side"] == "yes", "contracts"].sum()),
        "no_contracts": int(trades.loc[trades["side"] == "no", "contracts"].sum()),
        **{f"cfg_{key}": value for key, value in asdict(candidate).items()},
    }


def run_source(source: str, frame: pd.DataFrame, candidates: list[Candidate], bankroll: float, output_dir: Path) -> pd.DataFrame:
    rows = []
    for candidate in candidates:
        trades = simulate(frame, candidate, bankroll)
        trades.to_csv(output_dir / f"{source}_{candidate.name}_trades.csv", index=False)
        split_values = ["all"]
        if "split" in trades.columns and source == "historical":
            split_values = ["train", "validation", "test", "all"]
        for split in split_values:
            part = trades if split == "all" else trades[trades["split"] == split]
            rows.append(summarize(part, source, candidate, split, bankroll))
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-trades", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--live-db", type=Path, default=DEFAULT_LIVE_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "scaling_sizing_research")
    parser.add_argument("--bankroll", type=float, default=100.0)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    official = OfficialResults(args.output_dir / "official_results_cache.json")
    historical, historical_report = normalize_historical(args.historical_trades, official)
    live_current, live_report = normalize_live(args.live_db, official, CURRENT_LIVE_PROCESS_START)
    official.write()

    all_summary = pd.concat(
        [
            run_source("historical", historical, CANDIDATES, args.bankroll, args.output_dir),
            run_source("live_current", live_current, CANDIDATES, args.bankroll, args.output_dir),
        ],
        ignore_index=True,
    )
    all_summary.to_csv(args.output_dir / "scaling_summary.csv", index=False)
    (args.output_dir / "run_report.json").write_text(
        json.dumps(
            {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "bankroll": args.bankroll,
                "historical_report": historical_report,
                "live_report": live_report,
                "candidates": [asdict(candidate) for candidate in CANDIDATES],
                "notes": [
                    "Historical source is the already-selected corrected DuckDB research signal stream.",
                    "Historical settlement uses official Kalshi result where currently available; otherwise BTC proxy settlement from the original replay.",
                    "Live source is actual filled live research ledger rows after current process start, settled by official Kalshi result.",
                ],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(f"output_dir={args.output_dir}")
    print(f"historical_report={historical_report}")
    print(f"live_report={live_report}")
    show = all_summary[
        (all_summary["split"].isin(["train", "validation", "test", "all"]))
        & (all_summary["source"].isin(["historical", "live_current"]))
    ].copy()
    show = show.sort_values(["source", "split", "pnl"], ascending=[True, True, False])
    cols = [
        "source",
        "split",
        "candidate",
        "trades",
        "contracts",
        "premium",
        "pnl",
        "return_on_start",
        "return_on_premium",
        "win_rate",
        "max_drawdown",
        "yes_contracts",
        "no_contracts",
    ]
    print(show[cols].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
