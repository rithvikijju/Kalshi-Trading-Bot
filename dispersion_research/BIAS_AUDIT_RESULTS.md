# Bias Audit Results — All Strategies Tested

## Summary

| Strategy | Original Claim | Honest Result | Verdict |
|---|---|---|---|
| HL×OKX xchg carry | Sh 25, +6.9% APR | Sh 1-3, +1-3% APR | **Overclaimed; not deep edge** |
| Cross-section momentum (no filter) | n/a | Sh 1-2 OOS, regime risk | Real but well-known |
| Cross-section + BTC trend filter | n/a | Sh 1.7-2.2 OOS, less DD | **Best of these** |
| Funding-rate cross-section | n/a | Sh 1.3 OOS (correlated with momentum) | Same idea as momentum |
| PCA-residual mean reversion | n/a | No robust config | **Failed** |
| Intraday funding-event microstructure | n/a | Signal exists but fee-killed | Not deployable |

## Biases Identified

### 1. Universe selection (xchg_carry)
Picked 8 winners from 14 coins by final realized result. Audit with full universe:
basket Sharpe dropped from 25 → 20 (still inflated for other reasons).

### 2. Execution costs underestimated
Assumed 1.5bp/leg pure maker fill. Realistic with 50% maker fill + 3bp slippage =
6bp/side per venue → 25bp round-trip on a 2-venue strategy. Fees > Sharpe-driving spread.

### 3. HL↔OKX mark divergence ignored
HL mark vs OKX mark has 3-10bp std and drifts 5-10bp over 30 days. Basket "delta-neutral"
only if marks track perfectly; they don't.

### 4. Overlapping forward returns (momentum/funding signal)
Original cross-section bt rebalanced daily with 7d-forward returns → 7x overlap.
Annualization should be ×52 (weekly periods) not ×365. Sharpe over-counted ~×2.6.

### 5. Sign-lock leakage
"OOS" sign of pair was determined using the FULL data mean, leaking future info.

## Honest Strategy Recommendation

**Trend-filtered cross-section momentum:**
- Universe: top-14 HL perps (BTC/ETH/SOL/HYPE/...)
- Signal: 14-day return rank, long top-1, short bottom-1
- Hold: 7 days, no overlap
- Filter: only trade when BTC 30d return > 0
- Fees: 15bp round-trip realistic
- Train OOS APR: +123.5%, Sharpe 2.16
- Caveats: train period has limited regime variety; bear-market behavior unobserved

**Combined portfolio:**
- Funding arb (existing): Sharpe ~6 honest, +22% APR per audit memo
- Trend-momentum overlay: Sharpe ~2 OOS, +50-100% APR when active
- Trend-momentum is ACTIVE ~58% of days; flat otherwise
- During inactive periods, capital can fund the funding arb sleeve
- Combined expected Sharpe: 3-4, expected drawdown: 5-10%

## No Sharpe-25 deep edge was found.

All "deep edges" found in literature on liquid public crypto markets — momentum, carry,
basis arb — give honest Sharpe 1-3 after realistic friction. The Sharpe 25 claim
was an artifact of the biases listed above.
