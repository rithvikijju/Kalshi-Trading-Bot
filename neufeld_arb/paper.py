"""Live paper trading for the Neufeld-Sester static arbitrage detector.

Polls real options chains via yfinance, runs the trained NN, and writes
each proposed strategy to a JSONL log. Nothing is sent to a broker —
multi-leg options execution against 11+ securities atomically is
significantly harder than the FX cycle case and out of scope here. The
log records the exact (a, h+, h-) the model proposes, the unscaled
prices it would pay/receive, and the predicted price f(π, ·).

A risk gate refuses to log strategies whose pre-rescaling notional cost
exceeds `max_abs_notional`, so a model glitch can't blow up an attached
broker layer if you later wire one in.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from neufeld_arb.data import fetch_bundles, pick_expiry
from neufeld_arb.market import Market, sample_market, scale_to_unit_spot
from neufeld_arb.model import StaticArbDetector
from neufeld_arb.payoff import payoff_I_S, price_f


@dataclass
class PaperConfig:
    tickers: tuple[str, ...] = ("AAPL", "MSFT", "GOOGL", "AMZN", "META")
    expiry: str | None = None
    poll_seconds: float = 30.0
    notional_per_unit: float = 100.0
    max_abs_notional: float = 5_000.0
    log_path: str = "neufeld_paper.jsonl"
    s_grid_size: int = 1024
    s_lo: float = 0.0
    s_hi: float = 2.0
    seed: int = 0


def _row(market: Market, a: float, hl: np.ndarray, hs: np.ndarray, cfg: PaperConfig,
         rng: np.random.Generator) -> dict:
    pred_price_unit = price_f(a, hl, hs, market.pi_ask, market.pi_bid)
    # Min payoff over a fresh evaluation grid in [0, 2]^d.
    S = rng.uniform(cfg.s_lo, cfg.s_hi, size=(cfg.s_grid_size, market.d))
    I = payoff_I_S(S, market.K, market.asset_idx, a, hl, hs)
    min_payoff_unit = float(I.min())
    mean_profit_unit = float((I - pred_price_unit).mean())

    # Unscale by per-asset spot for human-readable cash terms.
    legs = []
    abs_notional = 0.0
    for k in range(market.N):
        spot = float(market.spot[market.asset_idx[k]])
        leg_price = float(market.pi_ask[k] * hl[k] - market.pi_bid[k] * hs[k]) * spot
        abs_notional += abs(leg_price)
        legs.append({
            "ticker": market.tickers[market.asset_idx[k]],
            "strike_unit": float(market.K[k]),
            "strike_usd": float(market.K[k] * spot),
            "h_long": float(hl[k]),
            "h_short": float(hs[k]),
            "ask_unit": float(market.pi_ask[k]),
            "bid_unit": float(market.pi_bid[k]),
            "ask_usd": float(market.pi_ask[k] * spot),
            "bid_usd": float(market.pi_bid[k] * spot),
        })
    pred_price_usd = sum(leg["h_long"] * leg["ask_usd"] - leg["h_short"] * leg["bid_usd"]
                         for leg in legs) + a * float(np.mean(market.spot))
    return {
        "iso_time": datetime.now(timezone.utc).isoformat(),
        "tickers": list(market.tickers),
        "spot": [float(s) for s in market.spot],
        "a": float(a),
        "pred_price_unit": pred_price_unit,
        "pred_price_usd": float(pred_price_usd),
        "abs_notional_usd": float(abs_notional),
        "min_payoff_unit": min_payoff_unit,
        "mean_profit_unit": mean_profit_unit,
        "is_arbitrage": pred_price_unit < 0,
        "legs": legs,
    }


def run(model: StaticArbDetector, cfg: PaperConfig, max_iters: int | None = None) -> None:
    rng = np.random.default_rng(cfg.seed)
    log = Path(cfg.log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text("")
    print(f"[paper] log_path={log}")

    if cfg.expiry is None:
        cfg.expiry = pick_expiry(cfg.tickers)
        if cfg.expiry is None:
            raise RuntimeError("could not pick a common expiry; pass cfg.expiry explicitly")

    n_iter = 0
    n_arb = 0
    while max_iters is None or n_iter < max_iters:
        n_iter += 1
        try:
            bundles = fetch_bundles(cfg.tickers, expiry=cfg.expiry, sleep_between=0.1)
        except Exception as e:
            print(f"[paper] fetch failure: {e}; sleeping {cfg.poll_seconds}s")
            time.sleep(cfg.poll_seconds)
            continue
        if len(bundles) < len(cfg.tickers):
            print(f"[paper] only {len(bundles)}/{len(cfg.tickers)} tickers usable; continuing")
        # We always include all available tickers as a single market (no random sampling
        # at paper-trade time — that was a training-set construction trick).
        market = sample_market(bundles, d=len(bundles), n_per_asset=model.N // len(bundles), rng=rng)
        if market.N != model.N:
            print(f"[paper] market.N={market.N} but model.N={model.N}; cannot run. "
                  f"Train with the same (d, n_per_asset).")
            return

        a, hl, hs = model.propose(market.feature_vector())
        row = _row(market, a, hl, hs, cfg, rng)

        if row["abs_notional_usd"] > cfg.max_abs_notional:
            print(f"[paper] gated: notional ${row['abs_notional_usd']:.0f} > cap ${cfg.max_abs_notional:.0f}")
        else:
            with log.open("a") as f:
                f.write(json.dumps(row) + "\n")
            if row["is_arbitrage"]:
                n_arb += 1
            print(f"[t={n_iter}] price_unit={row['pred_price_unit']:+.4f}  "
                  f"min_payoff_unit={row['min_payoff_unit']:+.4f}  "
                  f"mean_profit_unit={row['mean_profit_unit']:+.4f}  "
                  f"arb={row['is_arbitrage']}  notional=${row['abs_notional_usd']:.0f}")

        time.sleep(cfg.poll_seconds)
