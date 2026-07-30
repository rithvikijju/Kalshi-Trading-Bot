"""Backtest the mean-reversion logic on a captured tape (or synthetic paths).

Replays ticks in time order through the real SignalEngine, simulates marketable
fills with an assumed spread + slippage, charges the configured Kalshi fee on
BOTH legs, and reports honest metrics. At most one open position per market.

    python -m strategies.mean_reversion.backtest --synthetic
    python -m strategies.mean_reversion.backtest --tape data/mr_tape.db
"""
from __future__ import annotations
import argparse
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import yaml
from data.models import Side
from strategies.mean_reversion.models import Quote, MRPosition, PosStatus
from strategies.mean_reversion.signal_engine import SignalEngine
from strategies.mean_reversion.fees import trade_fee_cents

CFG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass
class Tick:
    ts: datetime
    market_id: str
    yes_mid: float
    fair_yes: Optional[float] = None
    secs_to_close: Optional[float] = None


def _quote_from_mid(market_id: str, ts: datetime, mid: float,
                    half_spread: float) -> Quote:
    return Quote(market_id=market_id, ts=ts,
                 yes_bid=max(0.01, mid - half_spread),
                 yes_ask=min(0.99, mid + half_spread), source="sim")


def run_backtest(ticks: list[Tick], cfg: dict, half_spread_cents: float = 1.0) -> dict:
    eng = SignalEngine(cfg)
    fee_model = cfg["fees"]["model"]
    slip = cfg["fees"]["slippage_cents"] / 100.0
    half_spread = half_spread_cents / 100.0
    size = cfg["sizing"]["contracts_per_trade"]

    open_pos: dict[str, MRPosition] = {}
    trades: list[dict] = []
    equity = 0.0
    curve: list[float] = []

    for t in ticks:
        q = _quote_from_mid(t.market_id, t.ts, t.yes_mid, half_spread)
        # exit check first
        pos = open_pos.get(t.market_id)
        if pos is not None:
            ex = eng.check_exit(pos, q, t.ts, t.secs_to_close)
            # always update rolling state too (so baseline keeps moving)
            eng.on_quote(q, fair_yes_prob=t.fair_yes, secs_to_close=t.secs_to_close)
            if ex:
                raw_exit, reason = ex
                fill = max(0.01, raw_exit - slip)
                entry_fee = trade_fee_cents(pos.entry_price, fee_model) / 100.0 * pos.size
                exit_fee = trade_fee_cents(fill, fee_model) / 100.0 * pos.size
                pnl = (fill - pos.entry_price) * pos.size - entry_fee - exit_fee
                equity += pnl
                curve.append(equity)
                trades.append({"market": t.market_id, "side": pos.side.value,
                               "type": pos.signal_type.value, "entry": pos.entry_price,
                               "exit": fill, "reason": reason, "pnl": pnl,
                               "fees": entry_fee + exit_fee})
                open_pos.pop(t.market_id, None)
            continue

        # no open position -> look for entry
        sig = eng.on_quote(q, fair_yes_prob=t.fair_yes, secs_to_close=t.secs_to_close)
        if sig:
            fill = min(0.99, sig.entry_price + slip)
            open_pos[t.market_id] = MRPosition(
                market_id=sig.market_id, side=sig.side, size=size,
                entry_price=fill, target_price=sig.target_price,
                stop_price=sig.stop_price, signal_type=sig.type,
                opened_at=t.ts, status=PosStatus.OPEN)

    return _metrics(trades, curve)


def _metrics(trades: list[dict], curve: list[float]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "no signals fired"}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    total = sum(pnls)
    mean = total / n
    sd = (sum((p - mean) ** 2 for p in pnls) / n) ** 0.5 if n > 1 else 0.0
    sharpe = (mean / sd * math.sqrt(n)) if sd > 0 else 0.0   # per-sample, not annualized
    peak = -1e18; mdd = 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = min(mdd, e - peak)
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 3),
        "net_pnl_usd": round(total, 2),
        "avg_pnl_per_trade_usd": round(mean, 4),
        "total_fees_usd": round(sum(t["fees"] for t in trades), 2),
        "pseudo_sharpe": round(sharpe, 2),
        "max_drawdown_usd": round(mdd, 2),
        "exit_reasons": by_reason,
    }


# ─── tape loading ──────────────────────────────────────────────────
def load_tape(db_path: str) -> list[Tick]:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT ts, market_id, yes_mid, last, fair_yes, secs_to_close "
                     "FROM tape ORDER BY ts ASC").fetchall()
    c.close()
    out = []
    for r in rows:
        mid = r["yes_mid"] if r["yes_mid"] is not None else r["last"]
        if mid is None:
            continue
        out.append(Tick(ts=datetime.fromisoformat(r["ts"]), market_id=r["market_id"],
                        yes_mid=float(mid), fair_yes=r["fair_yes"],
                        secs_to_close=r["secs_to_close"]))
    return out


# ─── synthetic generator (validation) ──────────────────────────────
def synth_tape(n_markets: int = 3, n_ticks: int = 1500, seed: int = 7) -> list[Tick]:
    """Mean-reverting (OU) price with injected momentum spikes — exactly the
    regime this strategy is built for, so a correct engine should profit here
    BEFORE fees eat most of it. Deterministic LCG (no Math.random dependency)."""
    state = seed
    def rnd():
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF
    def gauss():
        return (sum(rnd() for _ in range(12)) - 6.0)

    ticks: list[Tick] = []
    t0 = datetime(2026, 6, 13, 20, 0, 0, tzinfo=timezone.utc)
    for m in range(n_markets):
        anchor = 0.45 + 0.1 * m            # per-market mean
        price = anchor
        for i in range(n_ticks):
            # OU pull to anchor + noise
            price += 0.06 * (anchor - price) + 0.012 * gauss()
            # inject fast spikes every ~120 ticks that then revert
            if i % 120 == 30:
                price += 0.10 * (1 if rnd() > 0.5 else -1)
            price = min(0.95, max(0.05, price))
            ticks.append(Tick(ts=t0 + timedelta(seconds=4 * i),
                              market_id=f"SYNTH-{m}", yes_mid=round(price, 4)))
    ticks.sort(key=lambda x: x.ts)
    return ticks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", type=str, default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--half-spread-cents", type=float, default=1.0)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(CFG_PATH))

    if args.synthetic or not args.tape:
        print("== SYNTHETIC backtest (OU + spikes) ==")
        ticks = synth_tape()
    else:
        print(f"== TAPE backtest: {args.tape} ==")
        ticks = load_tape(args.tape)
    print(f"ticks: {len(ticks)}")
    res = run_backtest(ticks, cfg, half_spread_cents=args.half_spread_cents)
    import json
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
