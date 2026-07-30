"""
Order-flow fair-value market-maker on the Kalshi perp (paper, queue-aware).

The upgrade over kalshi_perp_edge/maker_mm_queue.py: that one centered quotes on spot
(fair = spot/mult) and faded spot moves. THIS one estimates fair value from the Kalshi book
ITSELF — microprice + the calibrated OFI/imbalance signal (fv_coefs_perp.json from
calibrate.py) — i.e. "where will the next quote be?", exactly the framework you described.
Quotes are centered on that FV; we still fade transient spot noise, cap inventory, and
queue-gate fills off the real tape. Fee = 0 (perp promo).

    python -m kalshi_micro.perp_mm --coin BTC          # uses calibrated coefs if present
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi_k6"))
import config
from kalshi_micro import orderflow as of

MULT = {"BTC": 1e4, "ETH": 1e3}
PERP = {"BTC": "KXBTCPERP", "ETH": "KXETHPERP"}
SPOT = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def cb_spot(prod):
    u = f"https://api.exchange.coinbase.com/products/{prod}/ticker"
    d = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "r/1.0"}), timeout=4).read())
    return float(d["price"])


def load_coefs():
    p = Path(__file__).resolve().parent / "fv_coefs_perp.json"
    if p.exists():
        d = json.loads(p.read_text())
        print(f"loaded calibrated FV coefs (OOS corr {d.get('oos_corr')}, "
              f"dir acc {d.get('dir_acc')})", flush=True)
        return d.get("raw", {})
    print("no calibrated coefs yet -> using microprice prior. Run kalshi_micro.calibrate after "
          "capturing.", flush=True)
    return None


class Quote:
    __slots__ = ("px", "size", "ahead")

    def __init__(self, px, size, ahead):
        self.px, self.size, self.ahead = px, size, ahead


def depth_at(levels, px, side):
    tot = 0.0
    for p, s in levels:
        if (side == "bid" and p >= px - 1e-9) or (side == "ask" and p <= px + 1e-9):
            tot += s
    return tot


def run(coin, a):
    mult = MULT[coin]; tk = PERP[coin]; prod = SPOT[coin]
    coefs = load_coefs()
    c = config.KalshiClient(); c.base = "https://external-api.kalshi.com/trade-api/v2"
    log = Path(__file__).resolve().parent / "data" / \
        f"fvmm_{coin}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl"
    f = open(log, "a")
    print(f"[{coin}] FV order-flow MM half={a.half_bp}bp size={a.size} -> {log.name}", flush=True)

    inv = cash = 0.0; fills = 0
    bidq = askq = None; last_id = None
    ofi = of.OFIState(); spot_hist = deque(maxlen=600); i = 0
    while True:
        t0 = time.time()
        try:
            ob = c._get(f"/margin/markets/{tk}/orderbook").get("orderbook", {})
            bids = [(float(p), float(s)) for p, s in (ob.get("bids") or [])]
            asks = [(float(p), float(s)) for p, s in (ob.get("asks") or [])]
            spot = cb_spot(prod)
        except Exception as ex:
            print(" feed", str(ex)[:50], flush=True); time.sleep(1); continue
        if not bids or not asks:
            time.sleep(a.poll); continue
        now = time.time(); spot_hist.append((now, spot))
        spot_fair = spot / mult

        # recent trades (for trade_imb + queue fills)
        trades = []
        try:
            if last_id is None:
                d0 = c._get("/margin/trades", {"ticker": tk, "limit": 1}).get("trades", [])
                last_id = d0[0]["trade_id"] if d0 else "seed"
            else:
                d = c._get("/margin/trades", {"ticker": tk, "limit": 200}).get("trades", [])
                for t in d:
                    if t["trade_id"] == last_id:
                        break
                    trades.append(t)
                last_id = d[0]["trade_id"] if d else last_id
        except Exception:
            pass

        bb, bbs, ba, bas = of.best_bid_ask(bids, asks)
        inc = ofi.update(bb, bbs, ba, bas)
        feat = of.features(bids, asks, inc, trades)
        m = feat["mid"]
        # FAIR VALUE from the order book itself (microprice + calibrated OFI/imbalance),
        # anchored to spot so it can't drift away from the arbitraged level.
        fv_book = of.fair_value(feat, coefs)
        fair = 0.5 * fv_book + 0.5 * spot_fair

        def move_bp(win):
            ref = next((s for (tt, s) in spot_hist if tt >= now - win), spot_hist[0][1])
            return (spot / ref - 1) * 1e4
        mv3, drift = move_bp(3), move_bp(a.drift_s)
        killed = abs(drift) >= a.drift_bp

        half = a.half_bp * 1e-4 * fair
        fade = -a.skew * (mv3 * 1e-4) * fair                 # fade transient spot noise
        inv_skew = -a.inv_kp * (inv / max(a.inv_cap, 1)) * half
        want_bid = min(fair - half + fade + inv_skew, bb)    # passive (don't cross)
        want_ask = max(fair + half + fade + inv_skew, ba)

        def requote(side, want, cur, can):
            if not can:
                return None
            if cur is None or abs(cur.px - want) >= 1e-4:
                ahead = depth_at(bids if side == "bid" else asks, want, side)
                return Quote(want, a.size, ahead)
            return cur
        bidq = requote("bid", want_bid, bidq, (not killed) and inv < a.inv_cap)
        askq = requote("ask", want_ask, askq, (not killed) and inv > -a.inv_cap)
        if killed:
            bidq = askq = None

        for t in reversed(trades):
            px = float(t["price"]); sz = float(t["count"]); side = t.get("taker_side")
            if side == "bid" and bidq and px <= bidq.px + 1e-9 and inv < a.inv_cap:
                eat = sz
                if bidq.ahead > 0:
                    dd = min(bidq.ahead, eat); bidq.ahead -= dd; eat -= dd
                if eat > 0:
                    fz = min(eat, bidq.size, a.inv_cap - inv)
                    if fz > 0:
                        inv += fz; cash -= bidq.px * fz; fills += 1; bidq.size -= fz
                        if bidq.size <= 1e-9:
                            bidq = None
            elif side == "ask" and askq and px >= askq.px - 1e-9 and inv > -a.inv_cap:
                eat = sz
                if askq.ahead > 0:
                    dd = min(askq.ahead, eat); askq.ahead -= dd; eat -= dd
                if eat > 0:
                    fz = min(eat, askq.size, a.inv_cap + inv)
                    if fz > 0:
                        inv -= fz; cash += askq.px * fz; fills += 1; askq.size -= fz
                        if askq.size <= 1e-9:
                            askq = None

        equity = cash + inv * m
        i += 1
        if i % 30 == 0:
            rec = dict(t=round(now, 1), inv=round(inv, 1), equity=round(equity, 4), fills=fills,
                       fv_book=round(fv_book, 5), mid=round(m, 5), mv3=round(mv3, 2),
                       per_fill_bp=round(equity / max(fills, 1) / m * 1e4, 3) if m else 0)
            f.write(json.dumps(rec) + "\n"); f.flush()
            print(f"  inv {inv:+.0f} fills {fills} eq ${equity:+.4f} "
                  f"({rec['per_fill_bp']:+.2f}bp/fill) FVdev {(fv_book-m)*1e4/m if m else 0:+.2f}bp"
                  f"{' KILL' if killed else ''}", flush=True)
        time.sleep(max(0, a.poll - (now - t0)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--coin", default="BTC", choices=["BTC", "ETH"])
    p.add_argument("--half_bp", type=float, default=0.3)
    p.add_argument("--skew", type=float, default=0.5)
    p.add_argument("--size", type=float, default=5)
    p.add_argument("--inv_cap", type=float, default=50)
    p.add_argument("--inv_kp", type=float, default=1.0)
    p.add_argument("--drift_bp", type=float, default=15)
    p.add_argument("--drift_s", type=float, default=20)
    p.add_argument("--poll", type=float, default=1.0)
    a = p.parse_args()
    run(a.coin, a)


if __name__ == "__main__":
    main()
