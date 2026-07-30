# Pinnacle/Circa odds → Kalshi sports — NEW CANDIDATE (HIGH-PRIORITY)

## Thesis
Pinnacle's closing line is the sharpest publicly observable sports price (40-year
track record, no/low vig, accepts sharp action). Convert to de-vigged fair
probability `p_pin`. Compare to Kalshi market mid `p_kalshi`. Trade the gap when
|p_kalshi - p_pin| > 3% and OI > $5K.

## Why the mispricing exists
- Kalshi sports launched late 2024; most sharps haven't fully migrated. Sharp circles
  still trade with sportsbooks where order-book-style exchange access didn't exist.
- Kalshi DMM layer (Citadel, Jump) prices off internal models, not necessarily
  Pinnacle. They are not arbitraging the gap because (a) they don't hold Pinnacle
  accounts (firm policy / legal), (b) at their size the gap doesn't matter.
- US legal status of Pinnacle access is gray — small operators face the same friction
  but for some it's tractable; large firms can't accept the regulatory hair.

## Who's on the other side of the trade
Kalshi retail sports flow (now $9.9B/mo per Bitcoin News May 2026). Mispricing
created by stale lines, biased priors, narrative trading.

## Naive baseline (must beat)
Buy-and-hold the favorite. Empirically loses to vig long-term. Edge = beat the
de-vigged Pinnacle mid by enough to clear Kalshi fee (~1.75¢ at p=0.50).

## Data needed
- Pinnacle (or Circa Sports for US-legal access) odds API at closing moment.
- Kalshi sports market mid at corresponding moment.
- Settlement outcomes.

## Backtest design
- Universe: Kalshi NBA team-future + game-line + total markets, NFL when in season.
- Period: from Kalshi sports approval (~late 2024) through now. ~1.5 years.
- IS / OOS: 50/50 chronological split.
- Entry: |Kalshi_mid - Pinnacle_devig| > 3% AND OI > $5K AND time-to-event < 30min.
- Exit: hold to settlement.
- Position sizing: quarter-Kelly per market, capped at min(OI*0.1, $1K/market).
- Cost model: Kalshi maker/taker fees per current schedule. Slippage = top-of-book.

## Validation gauntlet
- [ ] **OOS**: TBD — needs the backtest run on real data
- [ ] **Walk-forward**: TBD
- [ ] **Net of costs**: TBD — 3% gap needs to clear ~3.5% round-trip at p=0.5
- [ ] **Regime check**: NBA in-season vs off-season; check both
- [ ] **Look-ahead**: ensure Pinnacle price used is timestamped to BEFORE Kalshi trade
- [ ] **Multiple testing**: low (single hypothesis)
- [ ] **Capacity**: $1M-$5M/yr at 3% gap × 200 games × 5% capture × $50K notional
- [ ] **Mechanism**: ✓ (above)

## Capacity estimate
- Kalshi NBA team-future OI: $12M-$17M per market (per `SESSION_HIGHLIGHTS`).
- Per-game notional deployable: $50K-$500K.
- Annual edge: $1M-$5M IF 3% gap captures on 200 games/yr.
- This is **2-3 orders of magnitude larger** than the K6 sleeve.

## Decay risk
18-36 months. Drivers:
1. Pinnacle alternatives emerge for US persons (Circa expanding, Stake.us, etc.).
2. Kalshi DMMs start pricing off Pinnacle directly (low prob if regulatory line holds).
3. Sharp community migrates to Kalshi → gap tightens to 1-2%.

## Risk profile
- Per-trade: bounded loss (binary contract).
- Portfolio: correlation to NBA/NFL season schedule. Off-season → zero PnL months.
- Tail: Pinnacle data outage on game day = positions unhedged. Mitigate with kill
  switch on data feed.

## Legal/access prerequisites
**HARD GATES — RESOLVE FIRST:**
1. Is it legal for the operator (US resident or otherwise) to access Pinnacle data?
2. If no — does Circa Sports offer a sufficient-coverage API for sharps?
3. If no — does any US-legal sportsbook provide a closing-line equivalent?

If all three are no, this candidate is dead-on-arrival. **Spend 2-4 hours
researching this before any code.**

## Fundability verdict
Marginal as a standalone fund — too tied to event seasonality and legal-gray data
access for a clean LP narrative. **Highly fundable as a sleeve within a multi-strategy
firm** — uncorrelated to anything else in the portfolio, demonstrably high-Sharpe
historically if the gap is real, capacity-aware.

## Next step
1. (2-4h) Legal/data research: Pinnacle vs Circa vs other sharp references.
2. (1-2d) If green: pull 100 historical NBA games of Kalshi + Pinnacle data, run the
   gap distribution. If median gap > 2.5%, build the full backtest.
3. (1-2 weeks) Full backtest + walk-forward.
4. (1 week) Live paper at $100 stake.
