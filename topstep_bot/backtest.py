"""
Backtester — replay captured 1-min bars through the SAME signal engine + risk model.

Because we have no purchased history, this replays the tape the live bot has captured into
capture.duckdb (and/or bars seeded from /History/retrieveBars). It exits each setup by
walking forward bar-by-bar until the stop or target is touched (stop checked first =
conservative), books PnL net of round-turn cost through the risk engine, and reports the
honest, small-sample stats. Treat results as PAPER until the sample is large and OOS.

    python -m topstep_bot.backtest --instrument MNQ
"""
from __future__ import annotations

import argparse

import duckdb

from .config import BotConfig, INSTRUMENTS
from .broker.base import Bar, Side
from .ml.confidence import ConfidenceModel
from .risk.engine import LiveRiskManager
from .risk.sizing import decide_size
from .broker.base import ContractMeta
from .signals.engine import SignalEngine
from .signals.sessions import current_killzone


def load_bars(db_path: str, instrument: str) -> list[Bar]:
    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute(
        "SELECT instrument, ts, o, h, l, c, v FROM bars WHERE instrument=? ORDER BY ts",
        [instrument]).fetchall()
    con.close()
    return [Bar(*r) for r in rows]


def walk_exit(bars: list[Bar], i0: int, setup) -> tuple[float, int]:
    """From bar i0+1 forward, return (exit_price, exit_idx). Stop checked before target."""
    long = setup.direction is Side.BUY
    for j in range(i0 + 1, len(bars)):
        b = bars[j]
        if long:
            if b.l <= setup.stop:
                return setup.stop, j
            if b.h >= setup.target:
                return setup.target, j
        else:
            if b.h >= setup.stop:
                return setup.stop, j
            if b.l <= setup.target:
                return setup.target, j
    return bars[-1].c, len(bars) - 1     # unresolved: mark to last close


def run(cfg: BotConfig, instrument: str, only_killzone: bool = True):
    import os
    if not os.path.exists(cfg.db_path):
        print(f"no capture DB at {cfg.db_path} yet — run `python -m topstep_bot.run "
              f"--mode sim` first to capture a tape, then backtest.")
        return
    bars = load_bars(cfg.db_path, instrument)
    if len(bars) < 80:
        print(f"only {len(bars)} bars captured for {instrument} — keep the live capture "
              f"running to build the tape, then backtest.")
        return
    inst = INSTRUMENTS[instrument]
    meta = ContractMeta(instrument, instrument, inst.tick_size, inst.tick_value, instrument)
    eng = SignalEngine(min_bars=max(60, cfg.warmup_bars // 4))
    model = ConfidenceModel(cfg.model_path)
    risk = LiveRiskManager(cfg)

    trades, wins, gross, fees_paid = 0, 0, 0.0, 0.0
    i = eng.min_bars
    while i < len(bars):
        window = bars[:i + 1]
        setup = eng.generate(instrument, window)
        if setup is None or setup.rr < cfg.min_rr or \
                (only_killzone and current_killzone(setup.ts) is None):
            i += 1
            continue
        conf = model.predict(setup)
        dec = decide_size(setup, meta, risk, conf, cfg.min_confidence, 0, 0.0)
        if dec.size <= 0:
            i += 1
            continue
        exit_px, j = walk_exit(bars, i, setup)
        dpp = meta.tick_value / meta.tick_size
        pnl = (exit_px - setup.entry) * setup.direction.sign * dec.size * dpp
        fee = inst.rt_cost * dec.size
        net = pnl - fee
        risk.on_closed_trade(net)
        trades += 1
        wins += 1 if net > 0 else 0
        gross += pnl
        fees_paid += fee
        i = j + 1                      # no overlapping trades
        if risk.eng.blown or risk.eng.passed:
            break

    net = gross - fees_paid
    print(f"\n=== Backtest {instrument} (PAPER, small-sample) ===")
    print(f"bars={len(bars)} trades={trades} win_rate={(wins/trades if trades else 0):.1%}")
    print(f"gross ${gross:+.2f}  fees ${fees_paid:.2f}  NET ${net:+.2f}")
    print(f"avg net/trade ${ (net/trades) if trades else 0:+.2f}")
    print(f"risk: {risk.snapshot()}")
    print(f"model: {'TRAINED' if model.trained else 'PRIOR (cold-start heuristic)'}")
    if trades < 30:
        print("⚠️  <30 trades — not statistically meaningful. Keep capturing.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--all-hours", action="store_true", help="ignore killzone gating")
    args = ap.parse_args()
    cfg = BotConfig()
    run(cfg, args.instrument, only_killzone=not args.all_hours)


if __name__ == "__main__":
    main()
