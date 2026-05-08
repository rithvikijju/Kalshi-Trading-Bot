"""Risk preflight + mode-aware sizing.

Every prospective entry routes through risk_preflight() before placing.
The first check is ALWAYS hard ticker dedup — no second entry on a market
that already has an unsettled trade in the relevant scope (paper or
live/shadow).

The remaining checks fire only in live or live_shadow mode and are scaled
to the live Kalshi balance (with floors/ceilings) so they automatically
adapt when the user funds or withdraws money.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .config import (CFG, RISK_PCT, RISK_FLOOR, RISK_CEIL, RISK_FIXED,
                      PAPER_TAGS, LIVE_TAGS, SHADOW_TAGS, LIVE_OR_SHADOW)
from .paper_db import ticker_already_open, _conn


# ── Live balance cache (set by execution layer when it reads balance)
_LIVE_BALANCE = {"value": None, "ts": None}


def set_live_balance(usd: Optional[float]):
    if usd is not None and usd > 0:
        _LIVE_BALANCE["value"] = float(usd)
        _LIVE_BALANCE["ts"]    = datetime.now(timezone.utc)


def get_live_balance() -> Optional[float]:
    return _LIVE_BALANCE.get("value")


# ── Effective risk limits scaled to live balance
def effective_risk_limits() -> Dict[str, float]:
    bal = get_live_balance() or CFG.get("bankroll", 100_000.0)
    def clamp(name, has_ceiling=True):
        amt = bal * RISK_PCT[name]
        if has_ceiling:
            amt = min(amt, RISK_CEIL[name])
        amt = max(amt, RISK_FLOOR[name])
        return amt
    return {
        "balance":              bal,
        "max_per_trade":        clamp("max_per_trade"),
        "max_total_exposure":   clamp("max_total_exposure"),
        "daily_loss_limit":    -clamp("daily_loss_limit"),
        "min_account_balance":  clamp("min_account_balance", has_ceiling=False),
        "max_concurrent":       RISK_FIXED["max_concurrent"],
        "min_entry_price":      RISK_FIXED["min_entry_price"],
        "max_entry_price":      RISK_FIXED["max_entry_price"],
    }


def active_bankroll() -> float:
    """Bankroll for sizing. live/shadow ⇒ live balance × 0.95;
    paper ⇒ CFG['bankroll']. Never falls back to paper while in live mode."""
    mode = CFG.get("mode", "paper")
    if mode in ("live", "live_shadow"):
        bal = get_live_balance()
        if bal is not None and bal > 0:
            return bal * 0.95
        return 0.01    # refuse to size if balance unknown
    return CFG.get("bankroll", 100_000.0)


def size_for_mode(entry_price: float, kelly_fraction: Optional[float] = None) -> int:
    mode = CFG.get("mode", "paper")
    if mode in ("live", "live_shadow"):
        cap = effective_risk_limits()["max_per_trade"]
    else:
        cap = CFG.get("bankroll", 100_000.0) * CFG.get("max_per_market", 0.02)
    if kelly_fraction is not None and kelly_fraction > 0:
        cap = min(cap, cap * max(kelly_fraction, 0.05))
    return max(1, int(cap / max(0.01, float(entry_price))))


# ── DB-side counters scoped to live/shadow only
def _live_open_count() -> int:
    real = LIVE_OR_SHADOW
    ph = ",".join("?" * len(real))
    conn = _conn()
    n = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE settled=0 AND trade_type IN ({ph})",
        real).fetchone()[0]
    conn.close()
    return int(n)


def _live_open_exposure() -> float:
    real = LIVE_OR_SHADOW
    ph = ",".join("?" * len(real))
    conn = _conn()
    row = conn.execute(
        f"SELECT COALESCE(SUM(entry_price * contracts), 0) FROM trades "
        f"WHERE settled=0 AND trade_type IN ({ph})", real).fetchone()
    conn.close()
    return float(row[0])


def _live_pnl_today() -> float:
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    real = LIVE_OR_SHADOW
    ph = ",".join("?" * len(real))
    conn = _conn()
    row = conn.execute(
        f"SELECT COALESCE(SUM(pnl_dollars), 0) FROM trades "
        f"WHERE settled=1 AND trade_type IN ({ph}) "
        f"AND COALESCE(settled_at, timestamp_utc) >= ?",
        list(real) + [today_start]).fetchone()
    conn.close()
    return float(row[0])


# ── Risk block log
RISK_BLOCKS: List[dict] = []


def _log_block(ticker, side, contracts, entry, strategy, reasons):
    RISK_BLOCKS.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker, "side": side, "contracts": contracts,
        "entry_price": entry, "strategy": strategy, "reasons": reasons,
    })
    if len(RISK_BLOCKS) > 50:
        RISK_BLOCKS[:] = RISK_BLOCKS[-50:]


# ── Single canonical preflight
def risk_preflight(ticker: str, side: str, contracts: int,
                    entry_price: float, strategy: str = "?") -> Tuple[bool, list]:
    """Returns (allow, reasons). Always allow in paper mode (after the hard
    ticker dedup, which applies in all modes)."""
    reasons: list = []
    mode = CFG.get("mode", "paper")
    relevant_tags = LIVE_OR_SHADOW if mode in ("live", "live_shadow") else PAPER_TAGS

    # 1) ALWAYS: hard ticker dedup (mode-aware)
    if ticker_already_open(ticker, relevant_tags):
        reasons.append(f"already open on {ticker}")
        _log_block(ticker, side, contracts, entry_price, strategy, reasons)
        return False, reasons

    # 2) ALWAYS: sanity
    if contracts <= 0:
        reasons.append("contracts <= 0")
    if entry_price <= 0 or entry_price >= 1:
        reasons.append(f"invalid entry price {entry_price}")
    if reasons:
        _log_block(ticker, side, contracts, entry_price, strategy, reasons)
        return False, reasons

    # 3) PAPER: stop here
    if mode == "paper":
        return True, []

    # 4) LIVE / LIVE_SHADOW: full gates
    cost = float(contracts) * float(entry_price)
    lim  = effective_risk_limits()
    bal  = get_live_balance() or 0
    if bal < lim["min_account_balance"]:
        reasons.append(f"balance ${bal:.2f} < ${lim['min_account_balance']:.2f}")
    if cost > lim["max_per_trade"]:
        reasons.append(f"cost ${cost:.2f} > ${lim['max_per_trade']:.2f} "
                        f"({RISK_PCT['max_per_trade']*100:.0f}% of ${lim['balance']:.2f})")
    cur = _live_open_exposure()
    if cur + cost > lim["max_total_exposure"]:
        reasons.append(f"exposure ${cur+cost:.2f} > ${lim['max_total_exposure']:.2f}")
    if _live_open_count() >= lim["max_concurrent"]:
        reasons.append(f"open {_live_open_count()} >= {lim['max_concurrent']}")
    pnl_today = _live_pnl_today()
    if pnl_today < lim["daily_loss_limit"]:
        reasons.append(f"today PnL ${pnl_today:+.2f} < ${lim['daily_loss_limit']:.2f}")
    if entry_price < lim["min_entry_price"]:
        reasons.append(f"entry {entry_price:.3f} < {lim['min_entry_price']}")
    if entry_price > lim["max_entry_price"]:
        reasons.append(f"entry {entry_price:.3f} > {lim['max_entry_price']}")

    allow = (len(reasons) == 0)
    if not allow:
        _log_block(ticker, side, contracts, entry_price, strategy, reasons)
    return allow, reasons
