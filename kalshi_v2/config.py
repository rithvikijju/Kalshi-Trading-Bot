"""Single source of truth for configuration. Re-importing this module never
mutates state — CFG is created once at module load.
"""
from pathlib import Path

CFG = {
    # ──── Mode + safety ────────────────────────────────────────
    "mode":              "paper",       # paper / live_shadow / live
    "live_enabled":      False,
    "bankroll":          100_000.0,     # paper bankroll; live uses Kalshi balance
    "kalshi_fee_cap":    0.07,          # max per-contract round-trip ≈ 7c
    # ──── Scan windows + edge thresholds ──────────────────────
    "event_series":      ("KXBTC", "KXBTCD"),
    "scan_min_ttl_min":  5,
    "scan_max_ttl_hours": 4.0,
    "min_edge_cents":    2.5,
    "max_spread_cents":  3,
    "min_entry_price":   0.20,
    "max_entry_price":   0.80,
    "min_liquidity":     0,             # KXBTC buckets often expose no OI
    "min_model_confidence": 0.0,
    # ──── Position sizing + concurrency ────────────────────────
    "max_concurrent_signals": 1,        # top-1 by edge per scan
    "max_per_market":    0.02,          # paper: 2% of bankroll per market
    "arb_max_dollars_per_trade": 1000,
    # ──── Position management ──────────────────────────────────
    "stop_loss_pct":     0.20,
    "take_profit_cents": 5.0,
    "time_exit_min_ttl_m": 3,
    "max_position_age_min": 90,
    # ──── WebSocket ────────────────────────────────────────────
    "ws_url": "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
    "ws_reconnect_base_sec": 2.0,
    "ws_reconnect_max_sec": 60.0,
    "ws_subscribe_channels": ("ticker", "orderbook_delta", "fill"),
    # ──── Polling cadence (when REST fallback used) ────────────
    "rest_book_interval_sec": 10,
    "spot_poll_sec":          2.0,
    "decision_interval_sec":  5.0,
    # ──── Order placement ──────────────────────────────────────
    "order_buffer_cents": 2,
    "order_expiration_sec": 30,
    # ──── Robust filter (HRDNN-inspired) ───────────────────────
    "robust_enabled":         True,
    "robust_n_bootstrap":     16,        # |P| in the ambiguity set
    "robust_min_pass_rate":   1.0,       # require 100% of P measures positive
    "robust_min_mean_edge_c": 1.0,       # require mean edge ≥ 1c after fees
    "lipschitz_position_L":   200,       # max contracts difference per cent of price
    # ──── SFM σ-predictor (optional) ───────────────────────────
    "sfm_enabled":            False,
    "sfm_horizon_min":        60,
    "sfm_K_frequencies":      4,
    "sfm_hidden_size":        32,
    # ──── Persistence ──────────────────────────────────────────
    "db_path":           str(Path("~/.btc_kalshi_bot/v2.db").expanduser()),
    # ──── Risk preflight (live/shadow only) ────────────────────
    "i_acknowledge_real_money_risk": False,
}

# ── Risk gates as fractions of LIVE balance (clamped by floor/ceiling) ────
RISK_PCT = {
    "max_per_trade":        0.10,
    "max_total_exposure":   0.30,
    "daily_loss_limit":     0.10,
    "min_account_balance":  0.05,
}
RISK_FLOOR = {
    "max_per_trade":         5.0,
    "max_total_exposure":   10.0,
    "daily_loss_limit":      5.0,
    "min_account_balance":   5.0,
}
RISK_CEIL = {
    "max_per_trade":       500.0,
    "max_total_exposure": 2000.0,
    "daily_loss_limit":    500.0,
}
RISK_FIXED = {
    "max_concurrent":     3,
    "min_entry_price":    0.20,
    "max_entry_price":    0.80,
}

# ── Trade-type tags for portfolio separation ──────────────────────────────
PAPER_TAGS  = ("v2", "single", "arb", "cross_arb", None)
LIVE_TAGS   = ("live", "v2_live")
SHADOW_TAGS = ("v2_shadow", "live_shadow")
LIVE_OR_SHADOW = LIVE_TAGS + SHADOW_TAGS
