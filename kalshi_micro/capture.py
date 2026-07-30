"""
Book + tape capture for Kalshi (perp via /margin, binary via elections API).

Logs JSONL at ~1-2 Hz: {ts, bids[topN], asks[topN], trades(new), spot} — the full book WITH
sizes, which is exactly what Kalshi provides and TopStep did not. This is the dataset the
order-flow fair-value model is calibrated on.

    # perp BTC
    python -m kalshi_micro.capture --venue perp --ticker KXBTCPERP
    # a liquid (near-the-money) binary strike — pick it from `--list`
    python -m kalshi_micro.capture --venue binary --list
    python -m kalshi_micro.capture --venue binary --ticker KXBTCD-26JUN2017-T71249.99
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi_k6"))
import config

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
PERP_BASE = "https://external-api.kalshi.com/trade-api/v2"
BIN_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _get(u, t=3):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "r/1.0"}), timeout=t).read())


def cb_spot(prod="BTC-USD"):
    try:
        return float(_get(f"https://api.exchange.coinbase.com/products/{prod}/ticker")["price"])
    except Exception:
        return None


def rich_sources(prod="BTC-USD"):
    """Genuinely INDEPENDENT data sources beyond the Kalshi book + Coinbase spot:
      * spot_book = Kraken Depth top-10 (LIGHT) -> spot-side order flow (leads the perp)
      * binance   = Binance best bid/ask        -> cross-exchange divergence
    Each best-effort so one failure can't stall the loop. (Coinbase L2 REST dumps the whole
    ~15k-level book per call — too heavy at 1Hz — so we use Kraken's count=10 depth instead.)"""
    out = {}
    try:
        r = list(_get("https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=10")["result"].values())[0]
        out["spot_book"] = {"bids": [[float(p), float(s)] for p, s, *_ in r["bids"]],
                            "asks": [[float(p), float(s)] for p, s, *_ in r["asks"]]}
    except Exception:
        pass
    try:
        d = _get("https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=BTCUSDT")
        out["binance"] = {"bid": float(d["bidPrice"]), "ask": float(d["askPrice"])}
    except Exception:
        pass
    return out


def list_binary_markets(c):
    c.base = BIN_BASE
    mk = c._get("/markets", {"series_ticker": "KXBTCD", "status": "open", "limit": 40}).get("markets", [])
    rows = []
    for m in mk:
        tk = m["ticker"]
        try:
            ob = c._get(f"/markets/{tk}/orderbook", {"depth": 5}).get("orderbook", {})
            yes = ob.get("yes") or []; no = ob.get("no") or []
            rows.append((tk, len(yes) + len(no), m.get("volume", 0)))
        except Exception:
            rows.append((tk, 0, 0))
    rows.sort(key=lambda r: -r[1])
    print("liquidity-ranked open KXBTCD strikes (levels, volume):")
    for tk, lv, vol in rows[:12]:
        print(f"  {tk}  levels={lv}  vol={vol}")
    print("\npick the one with the most levels (nearest the money) for --ticker")


def run(venue, ticker, hz, spot_prod, rich=False):
    c = config.KalshiClient()
    is_perp = venue == "perp"
    c.base = PERP_BASE if is_perp else BIN_BASE
    ob_path = f"/margin/markets/{ticker}/orderbook" if is_perp else f"/markets/{ticker}/orderbook"
    tr_path = "/margin/trades" if is_perp else "/markets/trades"
    fn = OUT / f"book_{venue}_{ticker.replace('/', '_')}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl"
    print(f"capturing {venue} {ticker} @ {hz}Hz -> {fn.name}", flush=True)
    f = open(fn, "a")
    last_id = None
    period = 1.0 / hz
    n = 0
    while True:
        t0 = time.time()
        rec = {"ts": time.time()}
        try:
            ob = c._get(ob_path, {"depth": 10} if not is_perp else None).get("orderbook", {})
            # arrays arrive sorted WORST-first; sort to keep the TOP 10 levels at the touch
            if is_perp:
                rec["bids"] = sorted(ob.get("bids") or [], key=lambda x: -float(x[0]))[:10]
                rec["asks"] = sorted(ob.get("asks") or [], key=lambda x: float(x[0]))[:10]
            else:
                # binary yes/no: highest-price (nearest touch) first
                rec["yes"] = sorted(ob.get("yes") or [], key=lambda x: -float(x[0]))[:10]
                rec["no"] = sorted(ob.get("no") or [], key=lambda x: -float(x[0]))[:10]
        except Exception as ex:
            rec["ob_err"] = str(ex)[:60]
        try:
            params = {"ticker": ticker, "limit": 100}
            tr = c._get(tr_path, params).get("trades", [])
            new = []
            for t in tr:
                if t.get("trade_id") == last_id:
                    break
                new.append(t)
            if tr:
                last_id = tr[0].get("trade_id", last_id)
            rec["trades"] = [{"price": t.get("price") or t.get("yes_price"),
                              "count": t.get("count"), "taker_side": t.get("taker_side")}
                             for t in new]
        except Exception as ex:
            rec["tr_err"] = str(ex)[:60]
        rec["spot"] = cb_spot(spot_prod)
        if rich:
            rec.update(rich_sources(spot_prod))
        f.write(json.dumps(rec) + "\n")
        n += 1
        if n % 30 == 0:
            f.flush()
            print(f"  {n} snaps captured", flush=True)
        time.sleep(max(0, period - (time.time() - t0)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--venue", choices=["perp", "binary"], default="perp")
    p.add_argument("--ticker", default="KXBTCPERP")
    p.add_argument("--hz", type=float, default=2.0)
    p.add_argument("--spot", default="BTC-USD")
    p.add_argument("--rich", action="store_true", help="also pull Coinbase L2 + Kraken + Binance")
    p.add_argument("--list", action="store_true", help="list liquid binary strikes and exit")
    a = p.parse_args()
    if a.list:
        list_binary_markets(config.KalshiClient())
        return
    run(a.venue, a.ticker, a.hz, a.spot, a.rich)


if __name__ == "__main__":
    main()
