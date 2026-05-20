# BTC15M Apr 1-7 Deep-Dive Pre-Registration

Created: 2026-05-14

Scope is fixed before testing:

- Market: Kalshi `KXBTC15M`.
- Train/study window: events/snapshots in Apr 1-4, 2026 UTC.
- Validation/test window: events/snapshots in Apr 5-7, 2026 UTC.
- Do not expand outside Apr 1 00:00:00 UTC through Apr 8 00:00:00 UTC without written approval.
- Data source for this pass: Predexon historical snapshots plus local BTC 1-minute spot. This is a research/backfill source, not a local websocket replay.

Replay rules:

- Decisions use only quote/BTC state available at or before the decision timestamp.
- BTC features use as-of 1-minute close at or before quote timestamp, with max-age checks.
- YES and NO books remain separate. NO executable ask is derived from the resting YES bid only because that is how Kalshi complementary books execute; no strategy may assume an arbitrary NO mid equals `1 - YES mid` for signal generation.
- Fill simulation uses top-of-book visible size, taker fee, and first qualifying signal per event. No future-best row, future-best RR, or candle high/low.
- Reject unreliable quote rows: duplicate rows, missing level-0 size for the side traded, crossed/invalid spreads, isolated sparse snapshots, stale BTC, or missing official settlement.
- Results are scored after all fees. Target-win sizing is reported separately from one-contract normalized results.

Rejection criteria unless explicitly stricter:

- Validation Sharpe < 0.8 or validation PnL <= 0 after fees.
- Validation max drawdown worse than validation PnL in magnitude.
- Train positive but validation negative.
- Excessive turnover/capacity mismatch: more than 1 trade per event, or median visible top qty < required contracts.
- Any signal requiring future information, non-causal event selection, or future-best quote selection.

Pre-registered hypotheses:

1. Current BTC15M lowdd momentum transfers.
   Test: TTL 4-5m, spread <=2c, entry 5-80c, 2m YES-mid move >=12.5c in side direction, BTC 3m aligned, abs 3m YES-mid <=35c, RR >=0.33.
   Reject if validation PnL <= 0 or Sharpe < 0.8.

2. BTC1H research fair-value model transfers to BTC15M after horizon rescaling.
   Test: empirical/lognormal probability above strike using rolling BTC returns at 15m horizon, trade when net edge >= calibrated threshold and spread <=2c.
   Reject if validation PnL <= 0 or edge deciles are not monotonic.

3. Liquid cheap-tail first signal.
   Test: first side per event with TTL 2-8m, spread <=1c, visible qty >=50, entry <=60c, RR >=0.5.
   Reject if validation PnL <= 0 or win rate is below breakeven by entry bucket.

4. Ultra-liquid cheap-tail.
   Test: same as hypothesis 3 but visible qty >=250.
   Reject if validation PnL <= 0.

5. Cheap-tail only when BTC 3m aligns with side.
   Test: hypothesis 3 plus side-adjusted BTC 3m return >=0.
   Reject if validation PnL <= 0 or trade count <20.

6. Cheap-tail only when BTC 5m aligns with side.
   Test: hypothesis 3 plus side-adjusted BTC 5m return >=0.
   Reject if validation PnL <= 0 or trade count <20.

7. Cheap-tail only when BTC 1m impulse aligns.
   Test: hypothesis 3 plus side-adjusted BTC 1m return >=0.
   Reject if validation PnL <= 0 or train/test sign flips.

8. Cheap-tail contrarian to BTC 1m overreaction.
   Test: hypothesis 3 plus side-adjusted BTC 1m return <=0 and side-adjusted 5m return >=0.
   Reject if validation PnL <= 0.

9. Orderbook imbalance aligns with side.
   Test: side-adjusted top/full depth imbalance >=0 at entry.
   Reject if validation PnL <= 0 or imbalance deciles are not monotonic.

10. Strong imbalance threshold.
    Test: side-adjusted imbalance >=0.25 with spread <=2c.
    Reject if validation PnL <= 0 or trade count <20.

11. Microprice pressure.
    Test: side-adjusted microprice minus mid >=0.5c, spread <=2c.
    Reject if validation PnL <= 0.

12. Microprice reversal.
    Test: side-adjusted microprice minus mid <=-0.5c with cheap entry.
    Reject if validation PnL <= 0.

13. Spread tightening before entry.
    Test: spread decreased over previous 60-120 seconds and current spread <=1c.
    Reject if validation PnL <= 0.

14. Spread widening avoidance.
    Test: current lowdd or cheap-tail but reject if spread widened by >=1c over previous 60 seconds.
    Reject if validation PnL <= 0 or it does not improve base max drawdown.

15. Quote speed low-stability filter.
    Test: quote speed <=5c over previous quote change for current/cheap-tail.
    Reject if validation PnL <= 0.

16. Quote speed high-momentum filter.
    Test: quote speed >=10c and side agrees with the move.
    Reject if validation PnL <= 0.

17. Time-to-close 2-4m.
    Test: cheap-tail only in TTL 2-4m.
    Reject if validation PnL <= 0.

18. Time-to-close 4-6m.
    Test: cheap-tail only in TTL 4-6m.
    Reject if validation PnL <= 0.

19. Time-to-close 6-8m.
    Test: cheap-tail only in TTL 6-8m.
    Reject if validation PnL <= 0.

20. Entry price band 10-30c.
    Test: first signal with entry 10-30c, spread <=1c, RR >=1.5.
    Reject if validation PnL <= 0.

21. Entry price band 30-60c.
    Test: first signal with entry 30-60c, spread <=1c, RR >=0.5.
    Reject if validation PnL <= 0.

22. Avoid very cheap lottery contracts.
    Test: cheap-tail with entry >=10c.
    Reject if validation PnL <= 0 or it does not improve base drawdown.

23. Top-quantity wall support.
    Test: side visible qty / opposite visible qty >=2, spread <=2c.
    Reject if validation PnL <= 0.

24. Opposite-wall fade.
    Test: trade against side when opposite wall ratio >=2 and BTC agrees with contrarian move.
    Reject if validation PnL <= 0.

25. Depth imbalance with liquidity wall.
    Test: side-adjusted full depth imbalance >=0.2 and visible side qty >=100.
    Reject if validation PnL <= 0.

26. Fair-value plus orderbook confirmation.
    Test: BTC-horizon fair-value edge >=5c and side-adjusted imbalance >=0.
    Reject if validation PnL <= 0 or edge deciles fail monotonicity.

27. Fair-value plus momentum confirmation.
    Test: fair-value edge >=5c and side-adjusted BTC 3m return >=0.
    Reject if validation PnL <= 0.

28. Gradient-boosted classifier on causal microstructure features.
    Test: train on Apr 1-4 only, choose threshold on internal train folds, evaluate once on Apr 5-7.
    Reject if validation PnL <= 0, Sharpe <0.8, or permutation p-value after multiple-comparison adjustment is not significant.

29. Logistic regression calibrated probability.
    Test: same feature set as 28 with linear model and calibration.
    Reject if validation PnL <= 0 or calibration is worse than market price baseline.

30. Small neural network classifier.
    Test: train on Apr 1-4 only with early stopping inside train; evaluate once on Apr 5-7.
    Reject if validation PnL <= 0 or materially worse than tree model.

31. Cross-sectional event graph features.
    Test: features from all strikes in the current event, including rank of strike, local slope, convexity, and adjacent depth.
    Reject if validation PnL <= 0 or feature importance is unstable across train folds.

32. Market-maker fade after stale one-sided quote.
    Test: trade only after side quote has stayed stable while BTC moved favorably for >=60s.
    Reject if validation PnL <= 0.

Multiple-comparison control:

- Treat the 32 hypotheses as pre-registered families.
- Apply Bonferroni-adjusted threshold `p < 0.05 / 32` for permutation significance claims.
- A strategy can be operationally interesting without significance, but cannot be called verified alpha unless it passes validation, robustness, and permutation checks.

Deliverable interpretation:

- Passing Predexon validation is not enough for deployment. It only promotes a candidate to live-WS holdout evaluation.
- Live deployment requires local websocket replay and live-ledger agreement on overlapping data.
