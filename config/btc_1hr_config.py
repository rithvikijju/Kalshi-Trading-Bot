"""
Centralized configuration for the Kalshi BTC 1-hour trading strategy.

This module stores ALL tuneable parameters in one place so that backtest,
live-paper, and live-prod scripts can share the same defaults.
"""

import math
import os
from pathlib import Path

# ────────────────────────────────────────────────────────────────────────────
# 1. BANKROLL & POSITION SIZING
# ────────────────────────────────────────────────────────────────────────────
CFG = {
    "bankroll":           9.00,       # starting equity in USD
    "kelly_fraction":     0.25,       # quarter-Kelly
    "max_per_market":     0.20,       # 20 % of equity per market
    "max_total_risk":     0.50,       # 50 % of equity total at risk (wider for tiny bankroll)

    # ── scanner thresholds ──────────────────────────────────────────────
    "min_edge_cents":     4.0,        # notebook tuned from 5.0 to 4.0
    "kalshi_fee_cents":   0.70,
    "max_spread_cents":   3,          # notebook tuned to 3 (was 5 default)
    "min_entry_price":    0.40,
    "max_entry_price":    0.60,
    "min_liquidity":      0,          # min open-interest
    "min_ttl_min":        5,          # minutes remaining before close
    "max_ttl_hours":      4.0,        # max hours remaining
    "max_concurrent_signals": 3,
    "use_momentum_drift": False,

    # ── position-management ─────────────────────────────────────────────
    "stop_loss_pct":          0.20,
    "take_profit_cents":      5.0,
    "time_exit_min_ttl_m":    3,
    "max_position_age_min":   90,

    # ── mode ────────────────────────────────────────────────────────────
    #   "paper"  → log only, no real orders
    #   "live"   → place real orders on Kalshi (requires prod creds + ack)
    "mode":                          "paper",
    "i_acknowledge_real_money_risk": False,

    # ── BTC data ────────────────────────────────────────────────────────
    "btc_ticker":       "BTC-USD",
    "btc_data_days":    7,            # days of 1m candle history to pull (7 days = fast boot)
    "btc_refresh_days": 2,            # how far back to refresh each loop

    # ── walk-forward eval ───────────────────────────────────────────────
    "wf_n_folds":          5,
    "wf_n_eval_per_fold":  500,
    "wf_horizon_min":      60,

    # ── empirical sample cache ──────────────────────────────────────────
    "emp_horizons": [15, 30, 60, 120, 240],
    "emp_n_samples": 5000,

    # ── event whitelist (prefix match) ──────────────────────────────────
    "event_whitelist": ["KXBTC", "KXBTCD"],

    # ── loop timing ─────────────────────────────────────────────────────
    "loop_interval_sec": 15,          # 15 seconds — fastest safe interval for Kalshi rate limits

    # ── data paths ──────────────────────────────────────────────────────
    "db_dir":  str(Path.home() / ".btc_kalshi_bot"),
    "db_name": "paper_trades.db",
}


def ceil_to_cent(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 0.0
    return math.ceil((value - 1e-12) * 100.0) / 100.0


def kalshi_fee_dollars(price: float, contracts: int = 1, liquidity: str = "taker") -> float:
    """Estimate Kalshi fees in dollars using the current event-contract formula."""
    p = min(1.0, max(0.0, float(price)))
    multiplier = 0.07 if liquidity == "taker" else 0.0175
    return ceil_to_cent(multiplier * contracts * p * (1.0 - p))


# ────────────────────────────────────────────────────────────────────────────
# 2. CREDENTIALS
# ────────────────────────────────────────────────────────────────────────────
_CREDS_DIR = Path(__file__).resolve().parent.parent  # project root
_CREDS_FILE = _CREDS_DIR / "credentials.env"


def load_credentials():
    """
    Read API_KEY_ID_KALSHI and PRIVATE_KEY_PATH from
    ``~/.kalshi/credentials.env``.  Returns a dict with those two keys
    or raises if the file is missing / incomplete.
    """
    if not _CREDS_FILE.exists():
        raise FileNotFoundError(
            f"Credential file not found at {_CREDS_FILE}.\n"
            f"Create it with:\n"
            f"  API_KEY_ID_KALSHI=your_api_key_here\n"
            f"  PRIVATE_KEY_PATH=/path/to/your/private_key.pem"
        )

    creds = {}
    with open(_CREDS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()

    required = ("API_KEY_ID_KALSHI", "PRIVATE_KEY_PATH")
    missing = [k for k in required if k not in creds]
    if missing:
        raise ValueError(
            f"Missing credential keys: {missing} in {_CREDS_FILE}"
        )
    return creds


# ────────────────────────────────────────────────────────────────────────────
# 3. CONSTANTS
# ────────────────────────────────────────────────────────────────────────────
MINUTES_PER_YEAR = 60 * 24 * 365

TERMINAL_STATUSES = {"settled", "finalized", "determined", "resolved", "closed"}

# ────────────────────────────────────────────────────────────────────────────
# 4. DB PATH HELPER
# ────────────────────────────────────────────────────────────────────────────
def get_db_path():
    """Return the full path to the paper-trades SQLite database."""
    d = Path(CFG["db_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return str(d / CFG["db_name"])
