"""Cross-exchange carry config.

Sign convention: signal_sign = +1 means HL_funding > OKX_funding on average → SHORT HL, LONG OKX.
Sign = -1 means OKX > HL → LONG HL, SHORT OKX.
"""

# Coin universe from 6-mo backtest sign-lock
UNIVERSE = {
    'HYPE':   {'sign': +1, 'avg_spread_apr': 9.57,  'sharpe': 28.7},
    'LINK':   {'sign': +1, 'avg_spread_apr': 9.00,  'sharpe': 42.6},
    'PENDLE': {'sign': +1, 'avg_spread_apr': 8.37,  'sharpe': 16.0},
    'XLM':    {'sign': +1, 'avg_spread_apr': 6.23,  'sharpe': 15.5},
    'BCH':    {'sign': +1, 'avg_spread_apr': 4.05,  'sharpe': 12.7},
    'LTC':    {'sign': +1, 'avg_spread_apr': 3.67,  'sharpe': 15.4},
    'XRP':    {'sign': -1, 'avg_spread_apr': -3.22, 'sharpe': 14.9},
    'AVAX':   {'sign': +1, 'avg_spread_apr': 10.69, 'sharpe': 37.2},
}

# Per-coin notional in USD (equal weight)
NOTIONAL_PER_COIN = 1000.0  # $1k per leg → $8k × 2 = $16k gross notional

# Sign-flip rules
SIGN_FLIP_WINDOW_DAYS = 30
SIGN_FLIP_TRIGGER_PCT = 0.50  # if rolling 30d sign is opposite > 50% of time, close

# Fees
HL_MAKER_BP  = 1.5
OKX_MAKER_BP = 2.0
FEE_BP_PER_LEG = (HL_MAKER_BP + OKX_MAKER_BP)  # bp per round-trip half-leg

# Polling
POLL_INTERVAL_SEC = 60  # check funding every minute
PERSISTENCE_REVIEW_HOURS = 24  # log NAV + check sign daily

# Paper bankroll
PAPER_BANKROLL = 5000.0
