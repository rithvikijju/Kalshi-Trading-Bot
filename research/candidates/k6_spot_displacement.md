# K6 spot-displacement (Kalshi BTC hourly binaries) — DEPLOYED SURVIVOR

*Already passed the gauntlet during prior research. Catalogued here for completeness.*

## Thesis
When BTC spot moves $50-$500 past a Kalshi hourly strike with ≤15 min to close, the
Kalshi MM ask is stale (anchored to pre-move probability). Buy the favored side at
the stale ask, hold to settlement.

## Mechanism
Kalshi MMs reprice on a 200ms-2min lag after fast BTC moves. The "stale window" is
the structural inefficiency. Validated by user across 8 strike/direction buckets.

## Validation summary (from `SESSION_HIGHLIGHTS.txt`)
- 65-day OOS backtest, n=1991 trades
- Win 77-99% across 8 buckets
- Avg +$0.039/trade after Kalshi fees
- Daily Sharpe 21.9, MDD -$5.98
- Live paper $100 → $128.59 in 9 days

## Capacity
~$5K/yr regardless of capital deployed. Bounded by Kalshi BTC binary top-of-book
depth (median 17, p95 52).

## Verdict
**SURVIVED** — deployed, running. Small-capital amplifier role.

## Decay clock
12-18 months. Once Kalshi BTC binary volume crosses ~$1B/mo (currently $200M/mo
estimate), HFTs will tighten the stale window from minutes to seconds.

## Extensions worth doing
- Add Deribit-IV cross-check overlay (see `NOVELTY.md` candidate 1)
- K6 × KCF signal stacking (see `NOVELTY.md` candidate 3) — 1-day backtest
