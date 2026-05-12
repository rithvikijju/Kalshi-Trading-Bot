"""kalshi_v2 — clean, modular Kalshi BTC trading bot.

Architecture (matches the task brief):
  data.py      — REST + WebSocket data layer (replaces rate-limited polling)
  model.py     — fair value model (empirical sample bank, drift-removed,
                  vol+kurt joint matching)
  robust.py    — HRDNN-inspired robust mispricing filter + Lipschitz sizing
  sfm.py       — SFM cell as an optional realized-vol predictor
  features.py  — make_features helper for offline σ research
  strategy.py  — mispricing detection / signal generation
  risk.py      — risk preflight (mode-aware, scaled to live balance) +
                  hard ticker dedup
  execution.py — order placement (smart limit + 30s expiration) + position
                  management
  portfolio.py — paper + live portfolio metrics with mark-to-market
  paper_db.py  — sqlite persistence for trades + ticks
  config.py    — CFG dict and constants
  main.py      — orchestration + trading loop
"""
from .config import CFG, RISK_PCT, RISK_FLOOR, RISK_CEIL
