"""
Central configuration for the TopStep ICT bot.

Everything secret comes from the environment (never commit keys):
    TOPSTEP_USERNAME      your TopstepX username
    TOPSTEP_API_KEY       API key from the TopstepX dashboard (loginKey auth)
    TOPSTEP_ACCOUNT_NAME  (optional) which funded/eval account to trade; if unset we
                          pick the first active account returned by /Account/search
    DISCORD_BOT_TOKEN     discord bot token
    DISCORD_CHANNEL_ID    channel id the bot posts alerts to (int)

Instrument tick sizes / values below are *fallbacks*. At runtime the ProjectX
Contract/search response carries the authoritative tickSize / tickValue and we use
those — so we never mis-size because of a stale constant.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- API endpoints
API_BASE = "https://api.topstepx.com"
USER_HUB = "https://rtc.topstepx.com/hubs/user"
MARKET_HUB = "https://rtc.topstepx.com/hubs/market"


# ---------------------------------------------------------------- instruments
@dataclass(frozen=True)
class Instrument:
    """A tradeable contract. `search` is the text we hand to /Contract/search.

    tick_size / tick_value are fallbacks only — the live contract metadata wins.
    `kind` groups risk behaviour (index futures vs crypto futures behave differently).
    """
    key: str                 # our internal handle, e.g. "MNQ"
    search: str              # search text for /Contract/search, e.g. "MNQ"
    kind: str                # "index" | "crypto"
    tick_size: float         # price increment (fallback)
    tick_value: float        # $ per tick per contract (fallback)
    # microstructure cost floor in $ per round-turn per contract (commission+half-spread*2),
    # used by the regime/edge filter so we never scalp moves that can't pay costs.
    rt_cost: float = 0.0


# Defaults favour MICRO contracts — funded-account risk is about surviving the
# trailing drawdown, and micros let the risk engine size precisely. Full-size
# ES/NQ are included but off by default (see ENABLED below).
INSTRUMENTS: dict[str, Instrument] = {
    # index futures (the documented real-edge venue: order-flow microstructure)
    "MNQ": Instrument("MNQ", "MNQ", "index", 0.25, 0.50, rt_cost=1.50),
    "MES": Instrument("MES", "MES", "index", 0.25, 1.25, rt_cost=1.50),
    "NQ":  Instrument("NQ",  "NQ",  "index", 0.25, 5.00, rt_cost=4.00),
    "ES":  Instrument("ES",  "ES",  "index", 0.25, 12.50, rt_cost=4.00),
    # crypto futures (CME micros — only crypto TopStep allows)
    "MBT": Instrument("MBT", "MBT", "crypto", 5.0, 0.50, rt_cost=1.74),
    "MET": Instrument("MET", "MET", "crypto", 0.5, 0.05, rt_cost=1.20),
}

# Which instruments the bot actually watches/trades. Micros by default.
ENABLED = ["MNQ", "MES", "MBT"]


# ---------------------------------------------------------------- account/risk
# TopStep Combine / funded params. Mirror of topstep_strategy/risk_engine.py ACCOUNTS,
# kept here so the bot has a single config surface. VERIFY against your live account.
ACCOUNTS = {
    "50K":  dict(start=50_000,  target=3_000, trail_dd=2_000, daily_loss=1_000, max_ct=5),
    "100K": dict(start=100_000, target=6_000, trail_dd=3_000, daily_loss=2_000, max_ct=10),
    "150K": dict(start=150_000, target=9_000, trail_dd=4_500, daily_loss=3_000, max_ct=15),
}


@dataclass
class BotConfig:
    # account
    account_size: str = "50K"
    per_trade_risk: float = 200.0       # $ budgeted loss per trade (drives base sizing)
    loss_streak_halt: int = 3
    giveback_frac: float = 0.5
    daily_giveback_lock: bool = True

    # execution
    mode: str = "sim"                   # "sim" (paper) | "live" — promotion is explicit
    require_manual_approve: bool = False  # if True, Discord /approve gates every entry
    max_concurrent_positions: int = 2
    flatten_before_close_min: int = 2   # flatten this many minutes before session close

    # signal engine
    bar_minutes: int = 1
    min_target_r: float = 0.0           # R-multiple target floor (set by a shipped model)
    warmup_bars: int = 300              # seeded from /History/retrieveBars
    min_confidence: float = 0.55        # ML confidence floor to take a setup
    min_rr: float = 1.5                 # minimum reward:risk to take a setup

    # killzone gating (times in US/Eastern). Outside these, only manage, don't enter.
    trade_only_in_killzones: bool = True

    # verbose: stream the per-bar reasoning (structure read + every setup + why skipped)
    verbose: bool = False

    # microstructure capture: record the tape (every trade) + book snapshots to build a
    # real order-flow dataset going forward (the data short-term/microstructure edges need).
    capture_microstructure: bool = True
    book_snap_hz: float = 2.0           # max book snapshots/sec/instrument (throttle)

    # credentials (pulled from env; never hard-code)
    username: str = field(default_factory=lambda: os.getenv("TOPSTEP_USERNAME", ""))
    api_key: str = field(default_factory=lambda: os.getenv("TOPSTEP_API_KEY", ""))
    account_name: str = field(default_factory=lambda: os.getenv("TOPSTEP_ACCOUNT_NAME", ""))
    discord_token: str = field(default_factory=lambda: os.getenv("DISCORD_BOT_TOKEN", ""))
    discord_channel: int = field(
        default_factory=lambda: int(os.getenv("DISCORD_CHANNEL_ID", "0") or "0"))

    # persistence
    db_path: str = "topstep_bot/data/capture.duckdb"
    model_path: str = "topstep_bot/ml/confidence_model.pkl"

    def __post_init__(self):
        # adopt the timeframe + target rule of a shipped (OOS-validated) model so live
        # serving matches what was validated. Only applied if a model has been promoted.
        meta = Path("topstep_bot/ml/deep_meta.json")
        if meta.exists():
            try:
                m = json.loads(meta.read_text())
                if m.get("bar_minutes"):
                    self.bar_minutes = int(m["bar_minutes"])
                if m.get("min_target_r"):
                    self.min_target_r = float(m["min_target_r"])
            except Exception:
                pass

    def account_params(self) -> dict:
        return ACCOUNTS[self.account_size]

    def enabled_instruments(self) -> list[Instrument]:
        return [INSTRUMENTS[k] for k in ENABLED]
