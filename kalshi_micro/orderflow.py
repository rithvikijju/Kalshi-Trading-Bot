"""
Order-flow fair-value engine for Kalshi order books (perp + binary).

The signals you described, computed from the book + tape (Kalshi gives real sizes):
  * microprice      — size-weighted touch: leans toward the heavier side; predicts the next
                      quote move better than the mid.
  * book imbalance  — (bid_size - ask_size)/(sum) over top-N levels.
  * OFI             — Cont-Kukanov-Stoikov order-flow imbalance from changes in the best
                      bid/ask price AND size between snapshots (the canonical short-horizon
                      price-pressure signal).
  * trade imbalance — signed taker volume from the public tape (taker_side).

Fair value is FV = mid + beta . signals, where the betas are NOT hardcoded — they are fit
from captured data (calibrate.py) by regressing the realized next-horizon mid change on the
signals. This module only computes the raw signals + applies a coefficient vector; the
coefficients come from data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# canonical feature order (calibration + serving must agree)
FEATURES = ["micro_dev", "book_imb", "ofi", "trade_imb", "spread"]


def _levels(raw):
    """[['5.41','1026'], ...] -> [(price, size), ...] floats."""
    return [(float(p), float(s)) for p, s in raw]


def best_bid_ask(bids, asks):
    """bids/asks as [(px,sz)]; returns (best_bid_px, best_bid_sz, best_ask_px, best_ask_sz)."""
    bb = max(bids, key=lambda x: x[0]) if bids else (0.0, 0.0)
    ba = min(asks, key=lambda x: x[0]) if asks else (0.0, 0.0)
    return bb[0], bb[1], ba[0], ba[1]


def microprice(bids, asks):
    bp, bs, ap, as_ = best_bid_ask(bids, asks)
    tot = bs + as_
    if tot <= 0 or bp <= 0 or ap <= 0:
        return (bp + ap) / 2.0 if (bp and ap) else 0.0
    # heavier bid -> micro pulls toward the ask (price likely to tick up)
    return (ap * bs + bp * as_) / tot


def mid(bids, asks):
    bp, _, ap, _ = best_bid_ask(bids, asks)
    return (bp + ap) / 2.0 if (bp and ap) else 0.0


def book_imbalance(bids, asks, levels=5):
    b = sum(s for _, s in sorted(bids, key=lambda x: -x[0])[:levels])
    a = sum(s for _, s in sorted(asks, key=lambda x: x[0])[:levels])
    return (b - a) / (b + a) if (b + a) > 0 else 0.0


def trade_imbalance(trades):
    """signed taker volume / total, from public trades w/ taker_side ('bid' buy, 'ask' sell)."""
    buy = sum(float(t.get("count", 0)) for t in trades if t.get("taker_side") == "bid")
    sell = sum(float(t.get("count", 0)) for t in trades if t.get("taker_side") == "ask")
    tot = buy + sell
    return (buy - sell) / tot if tot > 0 else 0.0


@dataclass
class OFIState:
    """Cont et al. order-flow imbalance accumulator across book snapshots."""
    pb: float = 0.0; pbs: float = 0.0; pa: float = 0.0; pas: float = 0.0
    started: bool = False

    def update(self, bb, bbs, ba, bas) -> float:
        if not self.started:
            self.pb, self.pbs, self.pa, self.pas = bb, bbs, ba, bas
            self.started = True
            return 0.0
        e = 0.0
        # bid side contribution
        if bb > self.pb:
            e += bbs
        elif bb == self.pb:
            e += bbs - self.pbs
        else:
            e -= self.pbs
        # ask side contribution (opposite sign)
        if ba < self.pa:
            e -= bas
        elif ba == self.pa:
            e -= bas - self.pas
        else:
            e += self.pas
        self.pb, self.pbs, self.pa, self.pas = bb, bbs, ba, bas
        return e


def binary_to_book(ob: dict):
    """Kalshi binary {'yes':[[cents,size]], 'no':[[cents,size]]} -> YES (bids, asks) in $ (0-1).
    A NO bid at q¢ is a YES ask at (100-q)¢. Prices normalized to dollars."""
    yes = _levels(ob.get("yes") or [])
    no = _levels(ob.get("no") or [])
    bids = [(p / 100.0, s) for p, s in yes]
    asks = [((100.0 - p) / 100.0, s) for p, s in no]
    return bids, asks


def features(bids, asks, ofi_increment, trades, levels=5) -> dict:
    m = mid(bids, asks)
    mp = microprice(bids, asks)
    bp, _, ap, _ = best_bid_ask(bids, asks)
    return {
        "mid": m,
        "micro_dev": (mp - m) if m else 0.0,          # microprice deviation from mid
        "book_imb": book_imbalance(bids, asks, levels),
        "ofi": ofi_increment,
        "trade_imb": trade_imbalance(trades),
        "spread": (ap - bp) if (ap and bp) else 0.0,
    }


def fair_value(feat: dict, coefs: dict | None) -> float:
    """FV = mid + sum(beta_k * signal_k). coefs from calibration; None -> microprice fallback."""
    m = feat.get("mid", 0.0)
    if not coefs:
        return m + feat.get("micro_dev", 0.0)          # sensible prior: lean to microprice
    return m + sum(coefs.get(k, 0.0) * feat.get(k, 0.0) for k in FEATURES)
