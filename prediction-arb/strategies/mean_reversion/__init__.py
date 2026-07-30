"""Live in-game mean-reversion strategy for Kalshi high-volatility markets.

Buy the dip / fade the spike: when a price overshoots on a momentum burst it
tends to revert toward its short-run baseline. Flagship use case is NBA game
winner markets (a team goes on a run, its win-prob price spikes past fair value,
then comes back), but the price-based core works on any volatile binary.

Built on the edge-bot prediction-arb infrastructure (KalshiClient, fee model,
logger). Single-leg directional, so it ships its own SQLite portfolio + risk
gate rather than reusing the two-leg arb portfolio.
"""
