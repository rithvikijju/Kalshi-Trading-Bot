"""Live paper trading for the Neufeld-Sester static arbitrage detector.

Polls real options chains via yfinance, runs the trained NN, and writes
each proposed strategy to a JSONL log. Nothing is sent to a broker —
multi-leg options execution against 11+ securities atomically is
significantly harder than the FX cycle case and out of scope here. The
log records the exact (a, h+, h-) the model proposes, the unscaled
prices it would pay/receive, and the predicted price f(π, ·).

Each row carries diagnostics that let you separate "the NN says arb"
from "actually tradeable":

  * `min_payoff_unit`            min I_S over a fresh dense grid; must be
                                 >= 0 for the strategy to be a strict
                                 model-free arbitrage.
  * `pred_price_stressed_unit`   f(π, ·) recomputed after a per-leg
                                 slippage haircut: longs pay
                                 ask*(1+s/2) and shorts receive
                                 bid*(1-s/2). Approximates "miss the
                                 quote by s/2 bps on each leg".
  * `lsip_target_value_unit`     V(K, π) computed by scipy.linprog when
                                 cfg.compute_lsip_target=True. The LP
                                 ground truth — if it's >= 0 the market
                                 doesn't admit arbitrage at all.

A risk gate refuses to log strategies whose pre-rescaling notional cost
exceeds `max_abs_notional`, and `require_feasibility` skips logging
rows where `min_payoff_unit < 0` so the JSONL stops being dominated by
infeasible NN proposals.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from neufeld_arb.data import fetch_bundles, pick_expiry
from neufeld_arb.lsip import lsip_value
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
    s_grid_size: int = 4096           # dense feasibility grid (was 1024)
    s_lo: float = 0.0
    s_hi: float = 2.0
    seed: int = 0
    require_feasibility: bool = False  # skip rows whose min_payoff_unit < 0
    slippage_bps_per_leg: float = 5.0  # per-leg haircut applied to bid/ask
    compute_lsip_target: bool = False  # add LP ground-truth value per row
    lsip_grid_size: int = 512


def _row(market: Market, a: float, hl: np.ndarray, hs: np.ndarray, cfg: PaperConfig,
         rng: np.random.Generator) -> dict:
    pred_price_unit = price_f(a, hl, hs, market.pi_ask, market.pi_bid)

    # Stressed price: longs pay a haircut above ask, shorts receive a
    # haircut below bid. This is "miss the quote by `slippage` on each
    # leg", a coarse but honest standin for the gap between Yahoo's free
    # snapshot and what an actual broker fill would look like.
    half = cfg.slippage_bps_per_leg * 1e-4 / 2.0
    ask_stressed = market.pi_ask * (1.0 + half)
    bid_stressed = market.pi_bid * (1.0 - half)
    pred_price_stressed_unit = price_f(a, hl, hs, ask_stressed, bid_stressed)

    # Min payoff over a fresh evaluation grid in [0, 2]^d.
    S = rng.uniform(cfg.s_lo, cfg.s_hi, size=(cfg.s_grid_size, market.d))
    I = payoff_I_S(S, market.K, market.asset_idx, a, hl, hs)
    min_payoff_unit = float(I.min())
    mean_profit_unit = float((I - pred_price_unit).mean())

    # Optional LP ground truth: solve the LSIP and report its value.
    lsip_target_value_unit = None
    lsip_is_arbitrage = None
    if cfg.compute_lsip_target:
        res = lsip_value(market, grid_size=cfg.lsip_grid_size,
                         a_min=-1.0, H=1.0, s_lo=cfg.s_lo, s_hi=cfg.s_hi, rng=rng)
        if res.success:
            lsip_target_value_unit = float(res.value)
            lsip_is_arbitrage = bool(res.is_arbitrage)

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
    pred_price_stressed_usd = pred_price_usd + (
        pred_price_stressed_unit - pred_price_unit
    ) * float(np.mean(market.spot))

    return {
        "iso_time": datetime.now(timezone.utc).isoformat(),
        "tickers": list(market.tickers),
        "spot": [float(s) for s in market.spot],
        "a": float(a),
        "pred_price_unit": pred_price_unit,
        "pred_price_usd": float(pred_price_usd),
        "pred_price_stressed_unit": float(pred_price_stressed_unit),
        "pred_price_stressed_usd": float(pred_price_stressed_usd),
        "slippage_bps_per_leg": cfg.slippage_bps_per_leg,
        "abs_notional_usd": float(abs_notional),
        "min_payoff_unit": min_payoff_unit,
        "feasibility_grid_size": cfg.s_grid_size,
        "mean_profit_unit": mean_profit_unit,
        "is_arbitrage_nn": pred_price_unit < 0,
        "is_arbitrage_after_slippage": pred_price_stressed_unit < 0,
        "is_strictly_feasible": min_payoff_unit >= -1e-6,
        "lsip_target_value_unit": lsip_target_value_unit,
        "lsip_is_arbitrage": lsip_is_arbitrage,
        "legs": legs,
    }


def run(model: StaticArbDetector, cfg: PaperConfig, max_iters: int | None = None) -> None:
    rng = np.random.default_rng(cfg.seed)
    log = Path(cfg.log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text("")
    print(f"[paper] log_path={log}")
    print(f"[paper] feasibility filter={'ON' if cfg.require_feasibility else 'OFF'}  "
          f"feasibility_grid={cfg.s_grid_size}  "
          f"slippage={cfg.slippage_bps_per_leg} bps/leg  "
          f"lsip_target={'ON' if cfg.compute_lsip_target else 'OFF'}")

    if cfg.expiry is None:
        cfg.expiry = pick_expiry(cfg.tickers)
        if cfg.expiry is None:
            raise RuntimeError("could not pick a common expiry; pass cfg.expiry explicitly")

    n_iter = 0
    n_proposed = 0
    n_logged = 0
    n_filtered_feasibility = 0
    n_filtered_notional = 0
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
        n_proposed += 1

        # Gates.
        if row["abs_notional_usd"] > cfg.max_abs_notional:
            n_filtered_notional += 1
            print(f"[t={n_iter}] gated: notional ${row['abs_notional_usd']:.0f} > "
                  f"cap ${cfg.max_abs_notional:.0f}")
        elif cfg.require_feasibility and not row["is_strictly_feasible"]:
            n_filtered_feasibility += 1
            print(f"[t={n_iter}] filtered: min_payoff_unit={row['min_payoff_unit']:+.4f} (infeasible)")
        else:
            with log.open("a") as f:
                f.write(json.dumps(row) + "\n")
            n_logged += 1
            tag = []
            if row["is_arbitrage_nn"]:
                tag.append("nn=arb")
            if row["is_arbitrage_after_slippage"]:
                tag.append("after_slip=arb")
            if row["is_strictly_feasible"]:
                tag.append("feasible")
            if row["lsip_is_arbitrage"] is True:
                tag.append("lsip=arb")
            elif row["lsip_is_arbitrage"] is False:
                tag.append("lsip=no_arb")
            print(
                f"[t={n_iter}] price_unit={row['pred_price_unit']:+.4f}  "
                f"after_slip={row['pred_price_stressed_unit']:+.4f}  "
                f"min_payoff_unit={row['min_payoff_unit']:+.4f}  "
                f"notional=${row['abs_notional_usd']:.0f}  "
                f"[{', '.join(tag) or 'none'}]"
            )

        time.sleep(cfg.poll_seconds)

    print(f"\n[paper] session done: proposed={n_proposed} logged={n_logged} "
          f"filtered_feasibility={n_filtered_feasibility} filtered_notional={n_filtered_notional}")
