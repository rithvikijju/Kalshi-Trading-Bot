"""
Multi-source signal extraction for the Kalshi taker model.

The thesis: no single signal crosses the cost hurdle, but a stack of many weak, partially
independent signals can. So we mine EVERYTHING observable from the captured series:

  ORDER BOOK (Kalshi perp):
    micro_dev, book imbalance at depth 1/3/5/10, OFI (Cont) raw + EWMA, spread (ticks),
    book slope (how fast size thins away from the touch)
  TAPE (public trades):
    trade imbalance over short/long windows, trade intensity, aggressor-run length,
    signed-size acceleration
  SPOT (Coinbase, the lead source):
    multi-horizon spot momentum (1s/3s/10s/30s/60s), spot-vs-perp basis + its change,
    realized vol regime, spot acceleration
  TIME:
    seconds-into-minute, hour (regime)

Each is computed per snapshot from the rolling history, so the model sees a synchronized
cross-section of dozens of signals. The label is the forward microprice move; the model
learns which COMBINATION predicts a move big enough to pay the spread.
"""
from __future__ import annotations

from collections import deque

import numpy as np

from .orderflow import OFIState, best_bid_ask, book_imbalance, microprice, mid

# canonical feature order — calibration and serving must agree
FEATURES = [
    # book
    "micro_dev", "imb1", "imb3", "imb5", "imb10", "ofi", "ofi_ewma", "spread_t", "book_slope",
    # tape
    "tr_imb_s", "tr_imb_l", "tr_intensity", "aggr_run", "signed_accel",
    # spot / cross-source
    "spot_mom_1s", "spot_mom_3s", "spot_mom_10s", "spot_mom_30s", "spot_mom_60s",
    "basis", "basis_chg", "spot_vol", "spot_accel",
    # NEW independent sources: spot-side order flow (Coinbase L2) + cross-exchange divergence
    "cb_micro_dev", "cb_imb", "cb_ofi", "binance_div", "kraken_div", "xexch_disp",
    # time
    "sec_into_min", "hour",
]


def _book_slope(levels, ref_px, side, n=5):
    """How quickly resting size thins as you walk away from the touch (cancellation pressure
    proxy). Higher = steep wall near touch; lower = thin/illiquid."""
    lv = sorted(levels, key=(lambda x: -x[0]) if side == "bid" else (lambda x: x[0]))[:n]
    if len(lv) < 2 or ref_px <= 0:
        return 0.0
    sizes = [s for _, s in lv]
    near = sum(sizes[:max(1, n // 2)])
    far = sum(sizes[max(1, n // 2):]) or 1.0
    return (near - far) / (near + far)


class SignalExtractor:
    """Feed snapshots in time order; get a feature dict per snapshot once warm."""

    def __init__(self, hz=2.0, mult=1e4):
        self.hz = hz
        self.mult = mult
        self.ofi = OFIState()
        self.ofi_ewma = 0.0
        self.cb_ofi = OFIState()           # spot-side (Coinbase L2) order-flow imbalance
        self.spot_hist = deque(maxlen=int(120 * hz))     # (ts, spot)
        self.mid_hist = deque(maxlen=int(120 * hz))
        self.trade_buf = deque(maxlen=400)               # recent (ts, signed_size)
        self.last_signed = 0.0

    def _spot_at(self, now, lag_s):
        target = now - lag_s
        best = None
        for tt, s in self.spot_hist:
            if tt <= target:
                best = s
            else:
                break
        return best if best is not None else (self.spot_hist[0][1] if self.spot_hist else None)

    def update(self, ts, bids, asks, trades, spot, extra=None):
        if not bids or not asks:
            return None
        extra = extra or {}
        bb, bbs, ba, bas = best_bid_ask(bids, asks)
        m = mid(bids, asks)
        mp = microprice(bids, asks)
        tick = 1e-4
        inc = self.ofi.update(bb, bbs, ba, bas)
        self.ofi_ewma = 0.9 * self.ofi_ewma + 0.1 * inc
        self.spot_hist.append((ts, spot if spot else (m * self.mult)))
        self.mid_hist.append((ts, mp))

        # tape features
        buy = sum(float(t.get("count", 0)) for t in trades if t.get("taker_side") == "bid")
        sell = sum(float(t.get("count", 0)) for t in trades if t.get("taker_side") == "ask")
        for t in trades:
            sz = float(t.get("count", 0)) * (1 if t.get("taker_side") == "bid" else -1)
            self.trade_buf.append((ts, sz))
        signed_now = buy - sell
        tot = buy + sell
        tr_imb_s = (buy - sell) / tot if tot > 0 else 0.0
        # longer-window trade imbalance (last ~10s)
        recent = [s for (tt, s) in self.trade_buf if tt >= ts - 10]
        tr_imb_l = (sum(recent) / sum(abs(x) for x in recent)) if recent and sum(abs(x) for x in recent) > 0 else 0.0
        tr_intensity = len(recent) / 10.0
        # aggressor run
        run = 0
        for (_, s) in reversed(self.trade_buf):
            if s == 0:
                continue
            if run == 0:
                cur = np.sign(s); run = 1
            elif np.sign(s) == cur:
                run += 1
            else:
                break
        signed_accel = signed_now - self.last_signed
        self.last_signed = signed_now

        # spot momentum at multiple horizons (bp)
        def mom(lag):
            s0 = self._spot_at(ts, lag)
            s_now = self.spot_hist[-1][1]
            return (s_now / s0 - 1) * 1e4 if s0 else 0.0
        spot_mom = {h: mom(h) for h in (1, 3, 10, 30, 60)}

        # basis (spot-implied fair vs perp mid), bp
        spot_fair = self.spot_hist[-1][1] / self.mult
        basis = (spot_fair - m) / m * 1e4 if m else 0.0
        prev_basis = getattr(self, "_pb", basis)
        basis_chg = basis - prev_basis
        self._pb = basis

        # spot realized vol (bp) + acceleration over last ~30 obs
        sv = [s for (_, s) in list(self.spot_hist)[-int(30 * self.hz):]]
        spot_vol = (np.std(np.diff(sv)) / np.mean(sv) * 1e4) if len(sv) > 3 and np.mean(sv) > 0 else 0.0
        spot_accel = spot_mom[1] - mom(2)

        # ---- NEW independent sources (spot-side order flow + cross-exchange) ----
        cb_micro_dev = cb_imb = cb_ofi_inc = binance_div = kraken_div = xexch_disp = 0.0
        sbk = extra.get("spot_book")       # Kraken spot depth top-10
        sp_mid = None
        if sbk and sbk.get("bids") and sbk.get("asks"):
            sb = [(float(p), float(s)) for p, s in sbk["bids"]]
            sa = [(float(p), float(s)) for p, s in sbk["asks"]]
            sp_mid = mid(sb, sa)
            cb_micro_dev = (microprice(sb, sa) - sp_mid) if sp_mid else 0.0
            cb_imb = book_imbalance(sb, sa, 5)
            sbb, sbbs, sba, sbas = best_bid_ask(sb, sa)
            cb_ofi_inc = self.cb_ofi.update(sbb, sbbs, sba, sbas)
            kraken_div = (sp_mid - spot) / spot * 1e4 if spot else 0.0
        ref = spot if spot else sp_mid
        mids_x = [v for v in [sp_mid, spot] if v]
        bn = extra.get("binance")
        if bn and ref:
            bn_mid = (bn["bid"] + bn["ask"]) / 2.0; mids_x.append(bn_mid)
            binance_div = (bn_mid - ref) / ref * 1e4
        if len(mids_x) >= 2 and ref:
            xexch_disp = (max(mids_x) - min(mids_x)) / ref * 1e4

        import datetime
        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)

        feat = {
            "micro_dev": (mp - m) / tick if m else 0.0,
            "imb1": book_imbalance(bids, asks, 1), "imb3": book_imbalance(bids, asks, 3),
            "imb5": book_imbalance(bids, asks, 5), "imb10": book_imbalance(bids, asks, 10),
            "ofi": inc, "ofi_ewma": self.ofi_ewma,
            "spread_t": (ba - bb) / tick if (ba and bb) else 0.0,
            "book_slope": _book_slope(bids, bb, "bid") - _book_slope(asks, ba, "ask"),
            "tr_imb_s": tr_imb_s, "tr_imb_l": tr_imb_l, "tr_intensity": tr_intensity,
            "aggr_run": run * (cur if run else 0) if run else 0.0, "signed_accel": signed_accel,
            "spot_mom_1s": spot_mom[1], "spot_mom_3s": spot_mom[3], "spot_mom_10s": spot_mom[10],
            "spot_mom_30s": spot_mom[30], "spot_mom_60s": spot_mom[60],
            "basis": basis, "basis_chg": basis_chg, "spot_vol": spot_vol, "spot_accel": spot_accel,
            "cb_micro_dev": cb_micro_dev / 1e-2 if sp_mid else 0.0, "cb_imb": cb_imb,
            "cb_ofi": cb_ofi_inc, "binance_div": binance_div, "kraken_div": kraken_div,
            "xexch_disp": xexch_disp,
            "sec_into_min": dt.second + dt.microsecond / 1e6, "hour": dt.hour,
            # bookkeeping (not features):
            "_mid": m, "_micro": mp, "_spread": ba - bb, "_ts": ts,
        }
        return feat

    def vector(self, feat):
        return np.array([feat.get(k, 0.0) for k in FEATURES], dtype=float)
