# V3 Final Report — 10 hypotheses + 2 iterative rounds

Generated 2026-05-15. Tested 33 distinct strategies across 3 rounds on 7 days
of live capture (`live_capture_gapless_20260512_paused.duckdb`). Goal: find a
real scalable edge beyond the live bot's current T1/T2/T3.

## Bottom line

**No entirely new scalable edge was found.** The market is too efficient
relative to the bot's latency budget. However, one **concrete actionable
finding** emerged that 2–3×'s the existing T1's contribution.

## The actionable finding

**T1 monotonicity arb is depth-capped at `t1_max_qty_per_leg = 5`, but the
actual book depth on these arb opportunities is much larger.**

```
T1 pair depth distribution (n=30 arb pairs at edge ≥ 1.5¢):
  median = 17 contracts
  p75    = 20 contracts
  p95    = 52 contracts
  max    = 52 contracts

Backtest PnL by qty cap (same 30 trades):
  cap= 5  →  +$33.70 / 7d   (current bot setting)
  cap=10  →  +$61.69 / 7d
  cap=20  → +$108.66 / 7d
  cap=50  → +$197.48 / 7d   (depth saturates)
```

**Holds out-of-sample.** Both first-half (days 1–3.5) and second-half
(days 3.5–7) show ~$0.25/pair regardless of cap.

### Caveat (honest)

The 100% backtest win rate assumes **atomic fill at the displayed price**.
A sub-second persistence check on the raw ws feed showed many T1 arbs break
in <500ms — within the bot's 200–500ms order RTT. Bimodal:

- Active books: arb gone in 50–500ms (HFT-eaten, uncatchable)
- Quiet books / one-sided ticks: arb persists 5s+ (capturable)

The live bot already gets ~35 T1 fills/week, and the depth of those fills is
known to average 3–4 contracts, not the full 17 median. So:

- **Best case (cap=20, all depth realized):** $108/week (4× current $27)
- **Likely case (cap=15, ~60% depth realized):** $50–70/week (2× current)
- **Floor:** raising the cap can't reduce PnL — partial fills just give less

### Recommended action

In `strategy_cells/02_config.py`, raise:
```python
't1_max_qty_per_leg': 5  →  15  (or 20 for aggressive)
't1_max_dollars_per_pair': $X → $X * 3
```

And monitor: if avg fill qty stays at 3–4, depth not materializing in live; if
it climbs to 10+, the scaling is real.

## Marginally interesting (sub-significance)

**C2 yes-add whale signal** — when a single delta event adds ≥30 (or ≥500)
contracts on the YES side of a market, that market's YES side tends to win:

```
C2 yes-add-30:  n=10, win=80%, +$4.94, t=+1.90   (just below sig)
C2 yes-add-500: n=11, win=82%, +$5.81, t=+1.74   (just below sig)
C2 no-add-30:   n=10, win=20%, -$11.25, t=-3.06  (suspiciously symmetric)
C2 no-add-500:  n=11, win=18%, -$11.11, t=-2.93
```

The symmetry is suspect — BTC drifted down the 7 days, so "YES wins more
often" could be a regime artifact, not the whale signal. With more sample
(2–3 more weeks) this could mature into a real micro-edge, OR get washed out.

**Verdict:** Not deploy-worthy yet. Worth implementing as a logged-only
signal to gather more data.

## What does NOT work (and why)

Every other hypothesis lost money. Categorized:

| Category | Hypotheses | Reason |
|----------|-----------|--------|
| Directional from microstructure | H32, H34, H38, H40 + inversions | **Fee floor.** At entry price ~0.5, fee = 7¢. Breakeven win rate = 57%. Real microstructure edge above noise = 1–3%. Net negative. |
| Pin / strangle | H36 | Spot often blows through both strikes near close; not enough $1-cost pairs |
| Far-ITM extreme | H37 d≥2σ, d≥3σ | At 0.94+ price, fee still ~7% × 0.94 = 6.6¢, while edge is 1–3¢ |
| Pre-open persistence | H39 | 86% win rate, but the 14% losses are large; net negative |
| Maker / passive | C3 | Adverse selection: your limit only fills when prices move against you |
| Lead/lag | H32 | Coinbase spot IS what's driving Kalshi quotes, so no genuine "lead" |
| Calm regime T2 | H35 | T2 doesn't extend; calm sample too small |

## Methodological caveats

1. **7-day sample is tiny.** With ~5–30 trades per strategy, the 95% CI is
   wide. The "winning" T1 result is robust because it's risk-free arb (math),
   not statistical.
2. **Backtest assumes fill at displayed price.** Sub-second checks confirm
   this is optimistic. Cuts likely range from 30% to 70% of theoretical PnL.
3. **Settlement data is ground truth**, no peeking issue here.
4. **All tests are on KXBTCD only.** Other markets (ETH, ranges) untested.
5. **Latency model used was 1-second (next snapshot).** Real bot RTT is
   200–400ms; the 1-second snapshot under-counts available trades.

## The big-picture honest assessment

Kalshi BTC hourly markets, at 200–400ms latency from a non-colocated bot,
are at the efficient frontier. The only edges are:

1. **Structural arb** (T1 — risk-free monotonicity violations)
2. **High-confidence convergence** near settlement (T2 — but data truncates
   6min before close, so live exploit needs careful TTC tuning)
3. **Asymmetric persistence** (T3 — but only the NO-side variant works)

These are already deployed. The bot is well-positioned.

To go beyond this, you'd need one of:

- **Sub-100ms latency / colocation** — enables capturing the 50% of T1 arbs
  that currently evaporate before order RTT
- **Better-than-Coinbase spot** — Coinbase IS the input to Kalshi's MMs, so
  using only Coinbase, you cannot lead Kalshi. ETF flow data, Bitstamp,
  OKX, exchange-internal Binance feeds could potentially lead.
- **Inventory-bearing market making** — quote both sides and earn spread.
  Requires queue position and risk management; categorically different
  business from arb.
- **Multiple events traded together** — currently the bot trades KXBTCD only.
  Adding KXETH, hourly ranges, or non-BTC contracts could double trade
  frequency without per-strategy degradation.

The user's earlier framing was about "AI / sentiment analysis." Honest
opinion after running the data: **sentiment is not where the edge is.**
BTC short-horizon (1-hour binary) outcomes are dominated by recent spot
volatility, not news. Sentiment as a **regime filter** (pause trading
when CPI/FOMC imminent) remains defensible — but it's risk control, not
alpha.

## Concrete next steps (ranked by ROI)

1. **Raise `t1_max_qty_per_leg` from 5 → 15.** Best PnL/effort ratio. Single
   config change. Worst case: no change. Best case: 3× T1 contribution.
2. **Add C2 yes-add signal as logged-only.** Track for 2-3 weeks. If win
   rate stays >75% with n>40, deploy at small size.
3. **Add a risk-regime filter** (LLM call every 5min checking news feed for
   CPI/FOMC/Mt-Gox-tier events). Pauses or shrinks T2/T3 during high-risk
   windows. Pure risk control — won't increase PnL, will reduce blowups.
4. **Expand to KXETH and KXBTCD ranges.** Same code, different events.
   Doubles addressable opportunities. Requires re-validating sigma calibration
   per market.

## Files produced

- `backtest_outputs/HYPOTHESES_V3.md` — original 10-hypothesis design doc
- `backtest_outputs/run_v3_hypotheses.py` — Round 1 (10 hypotheses)
- `backtest_outputs/run_v3_round2.py` — Round 2 (5 inversions + 5 new ideas)
- `backtest_outputs/run_v3_round3.py` — Round 3 (T1 depth + delta + maker + decision-log)
- `backtest_outputs/validate_t1_scaling.py` — OOS split + depth feasibility
- `backtest_outputs/t1_subsecond_persistence.py` — raw ws feed persistence check
- `backtest_outputs/v3/results.json` — Round 1 numbers
- `backtest_outputs/v3/round2_results.json` — Round 2 numbers
- `backtest_outputs/v3/round3_results.json` — Round 3 numbers
- `backtest_outputs/v3/run_log.txt`, `round2_log.txt`, `round3_log.txt` — full output
