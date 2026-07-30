"""Crypto broker shell. Paper-only by default — actual exchange execution is
deferred until the funding-arb sleeve has been verified on paper for ≥30 days
(see RISK_POLICY.md)."""
from __future__ import annotations
from .broker_base import Broker, Order, Position
from .paper_broker import PaperBroker
from ..config import SAFETY


class CryptoPaperBroker(PaperBroker):
    name = "crypto_paper"


def make_crypto_broker(venue: str, run_id: str, live: bool = False,
                       starting_cash: float = 100_000.0,
                       price_fn=None) -> Broker:
    """Returns a crypto broker. By default this is a paper broker that uses
    the supplied price_fn (e.g. ccxt last price) to settle paper trades.
    Live mode requires SAFETY.can_trade_live AND venue-specific credentials,
    and is intentionally not implemented yet to keep the system safe."""
    if live and not SAFETY.can_trade_live:
        raise RuntimeError("Live crypto trading refused — see RISK_POLICY.md")
    if live:
        raise NotImplementedError(
            f"Live execution for {venue} not yet implemented. Run on paper for "
            f"30+ days first.")
    return CryptoPaperBroker(run_id=run_id, starting_cash=starting_cash, price_fn=price_fn)
