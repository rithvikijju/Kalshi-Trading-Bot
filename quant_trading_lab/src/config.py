"""Central config. Loads .env from project root. Single source of truth for
paths, API keys, and the hard live-trading safety locks."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PARQUET_DIR = DATA_DIR / "parquet"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = DATA_DIR / "reports"
for d in (RAW_DIR, PARQUET_DIR, PROCESSED_DIR, REPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name, default)
    if v is None or v == "":
        return None
    return v


@dataclass
class Keys:
    polygon: Optional[str] = field(default_factory=lambda: _env("POLYGON_API_KEY"))
    alpaca_id: Optional[str] = field(default_factory=lambda: _env("ALPACA_API_KEY"))
    alpaca_secret: Optional[str] = field(default_factory=lambda: _env("ALPACA_SECRET_KEY"))
    alpaca_paper_url: str = field(default_factory=lambda: _env("ALPACA_PAPER_BASE_URL") or "https://paper-api.alpaca.markets")
    databento: Optional[str] = field(default_factory=lambda: _env("DATABENTO_API_KEY"))
    fred: Optional[str] = field(default_factory=lambda: _env("FRED_API_KEY"))
    kalshi_id: Optional[str] = field(default_factory=lambda: _env("KALSHI_API_KEY_ID"))
    kalshi_key_path: Optional[str] = field(default_factory=lambda: _env("KALSHI_PRIVATE_KEY_PATH"))
    kalshi_key_pem: Optional[str] = field(default_factory=lambda: _env("KALSHI_PRIVATE_KEY"))
    hyperliquid_addr: Optional[str] = field(default_factory=lambda: _env("HYPERLIQUID_WALLET_ADDRESS"))
    hyperliquid_pk: Optional[str] = field(default_factory=lambda: _env("HYPERLIQUID_PRIVATE_KEY"))
    binance_id: Optional[str] = field(default_factory=lambda: _env("BINANCE_API_KEY"))
    binance_secret: Optional[str] = field(default_factory=lambda: _env("BINANCE_API_SECRET"))
    okx_id: Optional[str] = field(default_factory=lambda: _env("OKX_API_KEY"))
    okx_secret: Optional[str] = field(default_factory=lambda: _env("OKX_API_SECRET"))
    okx_passphrase: Optional[str] = field(default_factory=lambda: _env("OKX_API_PASSPHRASE"))
    kraken_id: Optional[str] = field(default_factory=lambda: _env("KRAKEN_API_KEY"))
    kraken_secret: Optional[str] = field(default_factory=lambda: _env("KRAKEN_API_SECRET"))
    openai: Optional[str] = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    anthropic: Optional[str] = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    slack_webhook: Optional[str] = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    discord_webhook: Optional[str] = field(default_factory=lambda: _env("DISCORD_WEBHOOK_URL"))


@dataclass
class Safety:
    """Two independent locks; BOTH must be exactly 'true' for live execution."""
    live_enabled: bool = field(default_factory=lambda: (_env("LIVE_TRADING_ENABLED") or "false").lower() == "true")
    risk_acknowledged: bool = field(default_factory=lambda: (_env("I_UNDERSTAND_REAL_MONEY_RISK") or "false").lower() == "true")

    @property
    def can_trade_live(self) -> bool:
        return self.live_enabled and self.risk_acknowledged


KEYS = Keys()
SAFETY = Safety()
DATABASE_URL = _env("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'lab.sqlite'}"


# ---- Provider availability matrix ----------------------------------------
PROVIDERS = [
    dict(name="yfinance",       asset="ETFs/stocks/crypto-spot",  paid=False, key_required=False, has_hist=True, has_live=False, has_ws=False, exec_support=False, key_present=True,                          recommended="Free historical fallback only"),
    dict(name="FRED",           asset="macro/rates/CPI/FOMC",     paid=False, key_required=True,  has_hist=True, has_live=False, has_ws=False, exec_support=False, key_present=KEYS.fred is not None,         recommended="Primary macro/rates source"),
    dict(name="Polygon",        asset="equities/ETFs intraday",   paid=True,  key_required=True,  has_hist=True, has_live=True,  has_ws=True,  exec_support=False, key_present=KEYS.polygon is not None,      recommended="Best free-tier 1m equities data"),
    dict(name="Alpaca",         asset="equities/ETFs + paper/live", paid=False, key_required=True, has_hist=True, has_live=True, has_ws=True,  exec_support=True,  key_present=KEYS.alpaca_id is not None,    recommended="Paper trading + zero-cost equity exec"),
    dict(name="Databento",      asset="futures/options/historical", paid=True, key_required=True,  has_hist=True, has_live=True,  has_ws=False, exec_support=False, key_present=KEYS.databento is not None,    recommended="Use only when futures data is needed"),
    dict(name="Kalshi",         asset="US event contracts",       paid=False, key_required=True,  has_hist=True, has_live=True,  has_ws=True,  exec_support=True,  key_present=KEYS.kalshi_id is not None,    recommended="Macro/political/sports event signals"),
    dict(name="Polymarket",     asset="crypto-settled events",    paid=False, key_required=False, has_hist=True, has_live=True,  has_ws=False, exec_support=False, key_present=True,                          recommended="Compare with Kalshi for divergences"),
    dict(name="ccxt (Binance)", asset="crypto spot+perp",         paid=False, key_required=False, has_hist=True, has_live=True,  has_ws=True,  exec_support=True,  key_present=True,                          recommended="Funding rate history + signals"),
    dict(name="Hyperliquid",    asset="crypto perp + onchain",    paid=False, key_required=True,  has_hist=True, has_live=True,  has_ws=True,  exec_support=True,  key_present=KEYS.hyperliquid_addr is not None, recommended="Funding arb venue, fast settlement"),
]


def provider_status() -> list[dict]:
    """Snapshot of which providers are usable with current keys."""
    return [dict(p) for p in PROVIDERS]


def check_keys() -> dict:
    """Returns {present: [...], missing: [...], unusable: [...]} for the
    notebook setup cell."""
    present, missing, unusable = [], [], []
    for p in PROVIDERS:
        if not p["key_required"]:
            present.append(p["name"])
        elif p["key_present"]:
            present.append(p["name"])
        else:
            missing.append(p["name"])
    return dict(present=present, missing=missing, unusable=unusable,
                live_enabled=SAFETY.live_enabled,
                risk_acknowledged=SAFETY.risk_acknowledged,
                can_trade_live=SAFETY.can_trade_live)
