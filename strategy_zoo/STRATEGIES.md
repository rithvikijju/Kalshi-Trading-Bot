# 50-Strategy Registry

Goal: find at least ONE strategy that survives stress test with realistic profit metrics scalable in ~1 year.

Cost model (REALISTIC):
- Hyperliquid perp: 2.5 bps taker, 1 bp slippage → 7 bps RT
- Coinbase spot: 5 bps taker, 2 bps slippage → 14 bps RT
- Kraken funding: 0 funding fees, 16 bps RT spot
- Funding rate paid at 1h on perps

All Sharpes annualized. Stress tests: OOS train/test, regime splits, perturbed costs, capacity at $10k/$100k/$1M.

## A. Microstructure & Stochastic (1-min BTC/ETH)
01. **Order Flow Imbalance** — volume-signed OFI predicting next-min return
02. **Hawkes Self-Excitation** — jump clustering, fade after burst
03. **Volume-Synchronized PIN (VPIN)** — toxic-flow detection, avoid trading
04. **Kyle's Lambda Lite** — price impact regression, trade only when lambda is low
05. **Hampel-Filtered Mean Reversion** — robust z-score on cleaned returns
06. **Avellaneda-Stoikov MM** — quote optimal bid/ask spread
07. **Realized-Vol Regime Switching** — RV terciles → momentum/MR rotation
08. **Markov-Switching HMM** — 2-state hidden regime, trade per state

## B. Funding-Rate (8-yr BTC/ETH funding)
09. **ETH Basis-Aware Funding Arb** — my known baseline, re-validate
10. **Funding-Rate Momentum** — long when funding ROC rising
11. **Funding Z-Score Reversion** — short when funding > 3σ from 30d mean
12. **Funding-Implied Carry Cycle** — DCC regime switch on |funding|
13. **Funding Day-of-Week Seasonality** — weekend funding vs weekday
14. **Funding Cross-Asset Spread** — long BTC funding short ETH funding when spread extreme
15. **Funding Overshoot Reversion** — fade in 1h after extreme print

## C. Statistical Arbitrage (20 alts at 1h)
16. **PCA Factor Residual** — eigenportfolio neutral, trade residuals
17. **Sparse-PCA Cluster Pairs** — L1-penalized loadings
18. **Random-Matrix-Theory Cleaned Eigenportfolio** — Marchenko-Pastur denoise
19. **Johansen Cointegration Pairs** — rolling Johansen test, pair-trade
20. **Wavelet Decomposition** — trade specific frequency band (decompose w/ db4)
21. **Kalman Beta Hedge** — time-varying β, long alt vs short BTC residual
22. **Riemannian Covariance Mean** — geometric-mean cov for robust portfolios
23. **Copula Tail Dependence** — Gumbel copula, trade tail decoupling

## D. Cross-Sectional & Factor
24. **XS Momentum (alts)** — long top-N short bottom-N by trailing return
25. **XS Reversal** — short-term reversal premium
26. **XS Vol-Scaled Momentum** — momentum / vol
27. **Idiosyncratic Reversal** — residual-on-BTC reversal
28. **PCA Risk-Parity Tilt** — overweight low-eigenvalue baskets
29. **Information-Driven Reweight** — Kelly-style sizing on signal IC

## E. Information-Theoretic & Nonlinear
30. **Transfer Entropy Lead-Lag** — find lead coin (e.g., does BTC lead alts?)
31. **Mutual-Information Pair Selection** — pairs by MI not corr
32. **Hurst Exponent Regime** — trend when H>0.55, MR when H<0.45
33. **DFA (Detrended Fluctuation)** — fractal scaling for regime
34. **Permutation Entropy** — complexity-based filter
35. **Recurrence Quantification Analysis** — RP-based regime

## F. Machine Learning
36. **LightGBM Microstructure** — 1-min BTC features → next-5min sign
37. **Online Bandit (LinUCB)** — choose strategy each hour
38. **Conformal-Prediction Gated** — trade only when prediction interval excludes 0
39. **Autoencoder Anomaly Trade** — fade reversion after anomaly
40. **Bayesian Online Changepoint** — re-fit after BOCPD changepoint
41. **Variational Autoencoder Regime** — learn latent regime embeddings
42. **Contrastive Regime Embedding** — SimCLR-style, cluster regimes

## G. Cross-Venue / Spread
43. **Kalshi-Polymarket Arb** — already operational, add to portfolio
44. **Perp-Spot Basis Arb** — short perp / long spot when basis > funding
45. **Triangular FX (BTC/ETH/USDT)** — 3-leg cyclic arb (paper, needs exchange)
46. **CME Futures Basis** — daily-basis carry on CME contracts (need data)

## H. Calendar / Event
47. **Hour-of-Day Liquidity** — trade in Asia handoff window only
48. **Funding Settlement Clock** — front-run funding payment
49. **End-of-Month Re-balance Flow** — last 3 days of month, alt outperformance
50. **FOMC-Day Vol-Compression Fade** — post-FOMC, fade IV crush via funding spike
