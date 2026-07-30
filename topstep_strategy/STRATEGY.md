# TopStep Strategy — Honest Research Findings & Framework

**Goal:** a fast in/out TopStep funded-account strategy aggregating Kalshi crypto-perp data
(price/volume/LOB) to scalp price changes.
**Data used:** Kalshi BTC/ETH perp 1-min OHLC + microsecond trade tape + OI (13 days),
Binance 1s spot, Coinbase 1-min. Code: `risk_engine.py`, `regime_filter.py`, and the test
scripts in `../kalshi_perp_edge/topstep_*`.

---

## 1. Hard constraints (these reshape the request)

- **TopStep is FUTURES ONLY. Options are prohibited** (also stocks, forex, spot crypto).
  An options strategy is not deployable on TopStep — full stop.
- **Tradeable crypto = CME Micro Bitcoin (MBT, 0.1 BTC) and Micro Ether (MET, 0.1 ETH).**
  This is the only place Kalshi crypto data is directly relevant.
- **MBT cost floor is brutal for scalping:** tick = $0.50 = a $5 BTC move; round-turn
  commission+spread ≈ $1.74 on ~$6,500 notional ≈ **2.7bp ≈ a ~$17 BTC move just to break
  even.** Micro-scalping 1-bp wiggles is dead on arrival (same fee-floor problem as Kalshi
  binaries). A signal must predict **$20+ BTC moves**.
- **Rules:** End-of-Day *trailing* max drawdown (gentle — rides intraday dips), soft daily
  loss limit, consistency rule only during the Combine eval.

## 2. What I tested — and the bias-free verdict

Using the 13-day aligned dataset (timestamp-corrected; same convention trap as last
session — Coinbase candles label by bucket *start*, fixed with +60s):

| Test | Result | Read |
|---|---|---|
| Direction: signed ret → next signed move | corr **+0.016** (1m), slightly **negative** at 3–5m | unpredictable / mild reversion |
| Kalshi basis, OI×price "new money" → direction | ≈ **0** | no positioning edge |
| Vol clustering: \|move\| → next \|move\| | corr **+0.30** | **real, but directionless** |
| Time-of-day directional drift | max **0.49bp**, t<2, all hours | no session edge |
| Momentum / reversion / breakout scalp, net of 2.7bp | **net-negative everywhere** except n=60 noise (+0.4–1.3bp ≈ $0.84/trade) | no viable edge |

**Conclusion: there is no Kalshi-data-driven directional edge on CME micro crypto futures.**
BTC direction is unpredictable at 1s, 1min, and session scales; the only robust signal
(vol clustering) carries no direction; and MBT's cost floor kills micro-scalps. I won't
package a losing scalp as a strategy.

This matches prior work: Kalshi perps are efficiently priced and their order flow is
uninformed (see `../kalshi_perp_edge/KALSHI_PERP_EDGE.md`).

## 3. What IS real and deployable now

- **`risk_engine.py` — the funded-account risk engine.** Most funded traders fail on the
  *trailing drawdown / daily loss*, not on signal. Models TopStep's EOD-trailing DD,
  daily-loss soft lock, max contracts, plus prudent self-guards (per-trade risk sizing,
  loss-streak halt, give-back lock). Instrument- and edge-agnostic: plug in *any* real edge
  and it tells you if it survives the rules. **This is the highest-value artifact here** —
  because the funded-account math (capped downside = eval fee, upside = 80–90% of a large
  account) is *itself* the positive-EV mechanism, but only if you don't blow the account.
- **`regime_filter.py` — Kalshi crypto vol-regime classifier.** The one legit use of Kalshi
  data: tells you *when* crypto moves are large enough to bother and when to cut size. Note:
  BTC is "HOT" ~57% of the time, so vol is rarely the constraint — **direction is.**

## 4. The two paths to a REAL edge (need data I don't have in this environment)

**(A) Order-flow scalping on LIQUID index futures (MES / MNQ) — the real funded-account edge.**
Short-term edges in futures scalping are microstructure: order-flow imbalance, absorption/
sweep at key levels, opening-range, liquidity voids. These exist on ES/NQ, not in cross-asset
crypto prediction. Kalshi's role shrinks to a secondary *risk-on/off overnight* input.
*Blocker:* needs L2 / footprint / tick data (Databento, CME, or a broker feed). I can't
validate it here, but it's the path with a real, documented edge. **Pipeline:** Databento
MBO/MBP-10 for ES → build order-flow-imbalance + absorption features → walk-forward test →
risk_engine.py for survival → forward paper on a sim/eval account.

**(B) Kalshi EVENT markets as a macro catalyst for index-futures event scalps — the creative
angle that survives.** Not the perps — Kalshi's *prediction markets* (CPI, FOMC, NFP,
jobless claims) price outcome probabilities pre-release. The index futures move on the
*surprise* (actual vs priced). Kalshi's implied distribution quantifies the expected
outcome, so the deviation drives a brief, directional, high-conviction scalp on MES/MNQ
around scheduled releases. This genuinely "aggregates different data," uses Kalshi's unique
edge, and fits funded-account risk (rare, sized, defined). *Blocker:* needs index-futures
intraday data around events + Kalshi event-market history (the latter IS accessible via the
Kalshi API). Worth a dedicated research pass.

## 5. Honest bottom line

The fastest way to "trade positive" on a TopStep funded account is **not** Kalshi→crypto
micros (no edge). It is: a **real order-flow edge on a liquid future + disciplined risk
engine**, where the funded-account structure does the heavy lifting on EV. Kalshi crypto
data contributes a vol-regime/risk filter, not alpha. The most promising *new* use of Kalshi
is its **event markets** for macro-catalyst index scalps (path B).

**Next steps I can take now:** (1) build out path B by pulling Kalshi event-market history
via the API and characterizing the pre/post-release repricing; (2) stand up the Databento
pipeline spec for path A. Tell me which to pursue.
