# 15 novel Kalshi strategies — N1-N15

Avoiding everything in `kalshi_HYPOTHESES.md` (K1-K15). The goal is high-frequency, high-edge ideas that can compound a $100 stake.

## Goal-aware constraints

- $100 capital → typical position size $1–5 per contract → need many trades/day
- Multiply means need average edge per trade > fee + slippage with high frequency
- Kalshi fee floor: `min(7¢, 7%·P·(1−P))` so anything at extreme prices is fine, anything near 0.50 needs 7¢+ edge
- 65-day research DB span; walk-forward 35d train / 30d test

## A. Cross-strike density / multi-strike structure

**N1. Implied density smoothing.** Fit a smooth function (cubic spline or kernel) through all 188 strikes' yes_mid as a function of strike. Strikes whose mid deviates >2σ from the smoothed curve are local mispricings — buy the cheap side, sell the rich side. Trade the chain reversion.

**N2. Multi-hop monotonicity.** T1 catches pair violations of `P(>K_lo) ≥ P(>K_hi)`. N2 extends to triplets and quartets: `P(>K1) ≥ P(>K2) ≥ P(>K3) ≥ P(>K4)`. The transitive arb opportunities exist beyond what T1's pair scan finds.

**N3. Implied density tail-fitting.** Fit a parametric tail (e.g., student-t) to the implied PDF derived from strike chain. When market-implied tail is too thin (Kalshi underprices a tail strike), buy the tail.

## B. Inter-temporal / cross-event

**N4. Same-strike across consecutive hours.** Compare `KXBTCD-26MAY1916.T80000` vs `KXBTCD-26MAY1917.T80000`. If hour-N is at 0.65 and hour-N+1 is at 0.40, the implied drift trajectory is inconsistent with any plausible spot model — one of them is mispriced.

**N5. Hourly settlement autocorrelation.** Does the previous hour's settlement direction predict this hour's mispricing? If BTC had an up-hour, are the next hour's YES strikes systematically underpriced (regression to fair after settlement noise)?

**N6. Path-trajectory arb.** Given current spot S and hour-end of close at C minutes, the trajectory implied by all strike mids should be consistent with a single Brownian motion. Find strikes whose implied prob is inconsistent with the average implied σ of nearby strikes.

## C. Spot-Kalshi cross-feed (beyond what K6/K7 did)

**N7. Spot reversal fade.** When BTC reverses direction sharply (e.g., +$200 in 60s after a −$200 in prior 60s), the strikes in the reversal zone are anchored to the wrong direction. K6 catches the lag; N7 catches the *reversal-specific* lag which is more severe.

**N8. Cross-exchange spot divergence trigger.** Use coinbase_ticker_all (BTC-USD) divergence from internal Coinbase BRTI. When the venue-specific price moves but BRTI hasn't caught up, predict imminent mid move on Kalshi.

**N9. Coinbase volume burst → Kalshi lag.** Big print on Coinbase (>$1M in one trade) often precedes Kalshi quote updates by 100ms-2s. Catch the lag.

## D. Microstructure (genuinely different from K11)

**N10. OFI acceleration (second-derivative).** K11 tested OFI level → mid move (weak). N10 tests `Δ(OFI)/Δt` — *changes* in OFI predict bigger moves than absolute level.

**N11. Quote-flicker detection.** When `yes_ask` flickers between two values rapidly (e.g., 0.65 → 0.66 → 0.65 → 0.66 in <2s), the market maker is uncertain. Post limit orders at the lower flicker price to capture rebate-like behavior.

**N12. Depth asymmetry build-up.** When `yes_bid_qty` is growing while `yes_ask_qty` is shrinking over a 30-second window (or vice versa), predict directional mid move.

## E. Vol-implied vs realized

**N13. Kalshi-implied σ vs BTC realized σ.** ATM strike yes_mid implies a σ via Black-Scholes inversion. Compare to BTC realized σ over the prior 15min. When Kalshi-σ > realized-σ + 20%, sell volatility (post asks above the implied curve); when Kalshi-σ < realized-σ − 20%, buy vol.

**N14. GARCH(1,1) σ-forecast vs Kalshi σ.** Fit GARCH(1,1) to recent BTC log-returns to forecast σ over the next 60 min. Compare to Kalshi-implied σ. Same trade logic as N13 but with a better σ forecast.

## F. Behavioral

**N15. Round-strike anchoring.** Strikes at $X0,000 (round numbers) have systematic retail flow. Compare implied prob at round strike vs the smoothed chain through non-round neighbors. Trade the gap if it's a consistent direction.

---

## Backtest methodology (rigorous, no look-ahead)

For every strategy:

1. **Walk-forward split.** Days 1-35 for parameter selection (in-sample). Days 36-65 for out-of-sample validation. Report both.
2. **No look-ahead in features.** σ, EWMA, regime indicators use only data available at signal time. No future-tick info.
3. **Fill modeling.** Fill at *next* observed ask, not the signal-tick ask. Conservative — captures real exec slippage.
4. **Fee model.** Exact Kalshi fee: `ceil(0.07 × P × (1−P) × 100) / 100` per contract.
5. **Event-level de-correlation.** Group trades by `event_ticker`. Sharpe computed on per-event PnL aggregates, not per-trade.
6. **Persistence note.** Where possible, verify with live_capture_gapless_20260512_paused.duckdb that signals persist long enough to fill at 200-400ms RTT. Flag as TODO if too compute-heavy.
7. **First-pass filters.** Reject if: (a) < 20 signals over 65 days, (b) negative IS Sharpe, (c) OOS Sharpe falls > 50% from IS.

## Output

A summary table per strategy:

| ID | Strategy | n_IS | win_IS | edge_IS | SR_IS | n_OOS | win_OOS | edge_OOS | SR_OOS | trades/day | Compounds $100? |

Where "Compounds $100" = true if edge × frequency × win_rate clears the breakeven needed for $100 to grow 50%+ over the OOS window at reasonable position sizing.
