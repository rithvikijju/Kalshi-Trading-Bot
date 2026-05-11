"""Bot orchestration. Spins up all background threads and runs the
decision loop. Idempotent: start_bot() / stop_bot() are safe to call
from any state.
"""
from __future__ import annotations
import threading, time
from datetime import datetime, timezone
from typing import Optional

from .config import CFG, LIVE_OR_SHADOW
from .client import KalshiClient
from .state import BOT_STATE, WS_STATE, LOCK, SPOT, BOOKS, TRACKED
from .data import (spot_poller, ws_listener, event_tracker, _log,
                    fetch_historical_minutes, add_rv_features,
                    fmt_local, fmt_local_full)
from .strategy import scan_signals
from .execution import (manage_open_positions, check_settlements,
                          place_smart_limit)
from .risk import (risk_preflight, size_for_mode, set_live_balance,
                    get_live_balance, effective_risk_limits, RISK_BLOCKS)
from .robust import build_ambiguity_set, SIZER
from .paper_db import record_trade
from .portfolio import paper_portfolio_metrics, live_portfolio_metrics


# Global singletons (created at start_bot time)
_KALSHI_MD: Optional[KalshiClient] = None
_KALSHI_LIVE: Optional[KalshiClient] = None
_BTC_1M = None
_EMPIRICAL_BANK = None
_AMBIGUITY_SET = []


def _refresh_balance():
    if _KALSHI_LIVE is None: return
    try:
        b = _KALSHI_LIVE.get_balance()
        bal = float(b.get("balance", 0)) / 100.0
        set_live_balance(bal)
    except Exception:
        pass


def _decision_worker():
    """Main trading-decision loop. Runs every CFG['decision_interval_sec']."""
    while BOT_STATE["running"]:
        try:
            BOT_STATE["iter"] += 1

            # Settle resolved markets before doing anything else
            try:
                check_settlements(_KALSHI_MD)
            except Exception as e:
                _log(f"settlement err: {e}")

            # Manage open positions (stop / TP / time / max-age)
            try:
                manage_open_positions(_KALSHI_MD, _KALSHI_LIVE)
            except Exception as e:
                _log(f"manage err: {e}")

            # Refresh balance periodically (every 10 cycles)
            if BOT_STATE["iter"] % 10 == 1:
                _refresh_balance()

            # Scan for new signals
            signals = scan_signals(empirical_bank=_EMPIRICAL_BANK,
                                      ambiguity_set=_AMBIGUITY_SET)

            if len(signals) == 0:
                pass
            else:
                _execute_signals(signals)

        except Exception as e:
            _log(f"decision err: {e}")
        # Sleep responsively
        end = time.time() + CFG["decision_interval_sec"]
        while time.time() < end and BOT_STATE["running"]:
            time.sleep(0.2)


def _execute_signals(signals):
    """Send each signal through risk preflight, then execute (paper / live)."""
    mode = CFG.get("mode", "paper")
    spot = SPOT.get("price")

    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        side   = sig["side"]
        entry  = float(sig["entry_price"])

        # Win probability for Kelly sizing:
        #   YES side → win_prob = model_p_yes
        #   NO  side → win_prob = 1 - model_p_yes  (since NO wins when YES loses)
        model_p_yes = float(sig["model_p_yes"])
        win_prob    = model_p_yes if side == "yes" else (1.0 - model_p_yes)

        # Half-Kelly sizing, then Lipschitz-clamp against recent same-mode
        # entries so adjacent-price signals don't whipsaw size up and down.
        raw_size  = size_for_mode(entry, win_prob=win_prob)
        contracts = SIZER.size(f"v2_{mode}", raw_size, entry)

        # The Lipschitz clamp can round size UPWARD past the dollar cap
        # (e.g. previous trade at similar price was 40 contracts, so the
        # clamp pulls a 39-contract decision to 40 → $0.34 over the
        # max_per_trade cap → risk_preflight rejects). Re-clamp here so
        # the smoothness guarantee never breaks the dollar limit.
        if mode in ("live", "live_shadow"):
            cap_dollars   = effective_risk_limits()["max_per_trade"]
            max_contracts = int(cap_dollars / max(0.01, entry))
            if contracts > max_contracts:
                contracts = max(1, max_contracts)

        allow, reasons = risk_preflight(ticker, side, contracts, entry, "v2")
        if not allow:
            print(f"  RISK BLOCK {ticker} {side}: {'; '.join(reasons)}")
            continue

        # Tag depends on mode
        tag_paper       = "v2"
        tag_shadow      = "v2_shadow"
        tag_live        = "v2_live"
        tag = (tag_live if mode == "live"
                else tag_shadow if mode == "live_shadow" else tag_paper)

        # Live: actually place order
        if mode == "live":
            try:
                resp = place_smart_limit(_KALSHI_LIVE, _KALSHI_MD,
                                            ticker, side, "buy", contracts)
                order_id = resp.get("order", {}).get("order_id")
            except Exception as e:
                print(f"  v2 LIVE FAILED {ticker}: {e}")
                continue
        else:
            order_id = None

        # Record locally
        ttl_h = float(sig.get("ttl_min", 0)) / 60
        tid = record_trade({
            "timestamp_utc":     datetime.now(timezone.utc).isoformat(),
            "event_ticker":      TRACKED.get("event") or "?",
            "market_ticker":     ticker,
            "side":              side,
            "entry_price":       entry,
            "contracts":         contracts,
            "entry_edge_cents":  float(sig["edge_c"]),
            "model_p_yes":       float(sig["model_p_yes"]),
            "market_yes_mid":    float(sig["market_yes_mid"]),
            "btc_spot_entry":    float(spot or 0),
            "ttl_hours_entry":   ttl_h,
            "confidence":        1.0,
            "trade_type":        tag,
            "robust_pass_rate":  float(sig.get("robust_pass_rate", 0) or 0),
            "robust_mean_edge_c": float(sig.get("robust_mean_edge_c", 0) or 0),
            "kalshi_order_id":   order_id,
        })
        prefix = "LIVE" if mode == "live" else "SHADOW" if mode == "live_shadow" else "PAPER"
        cost = entry * contracts
        print(f"  {prefix}-RECORD #{tid} {ticker:30s} {side:>3s}  "
              f"entry=${entry:.3f}  x{contracts}  cost=${cost:.2f}")
        BOT_STATE["trades"] += 1


# ════════════════════════════════════════════════════════════════════════
#  Lifecycle
# ════════════════════════════════════════════════════════════════════════
def start_bot(kalshi_md: KalshiClient,
                kalshi_live: Optional[KalshiClient] = None,
                btc_1m=None,
                refresh_btc_1m: bool = True):
    """Start all background threads. Idempotent — restart cleanly."""
    global _KALSHI_MD, _KALSHI_LIVE, _BTC_1M, _EMPIRICAL_BANK, _AMBIGUITY_SET

    if BOT_STATE["running"]:
        print("bot already running"); return

    _KALSHI_MD   = kalshi_md
    _KALSHI_LIVE = kalshi_live

    if btc_1m is None and refresh_btc_1m:
        print("fetching 90d BTC minute data (Coinbase)...")
        btc_1m = add_rv_features(fetch_historical_minutes(days_back=90))
        print(f"  ✓ {len(btc_1m):,} bars")
    _BTC_1M = btc_1m

    # Build the empirical bank (drift-removed, vol+kurt matched)
    if _BTC_1M is not None and len(_BTC_1M) > 60*24*7:
        from .model import build_empirical_bank
        _EMPIRICAL_BANK = build_empirical_bank(
            _BTC_1M, horizon_min=60, n_samples=5000, demean=True)
        print(f"  ✓ empirical bank: {_EMPIRICAL_BANK['n']} samples")
    else:
        print("  ⚠ btc_1m too short to build empirical bank; using lognormal only")

    # Build the ambiguity set for the robust filter (HRDNN-inspired)
    if CFG["robust_enabled"] and _BTC_1M is not None and len(_BTC_1M) > 60*24*7:
        _AMBIGUITY_SET = build_ambiguity_set(
            _BTC_1M, horizon_min=60,
            n_bootstrap=CFG["robust_n_bootstrap"])
        print(f"  ✓ ambiguity set: {len(_AMBIGUITY_SET)} measures")
    else:
        _AMBIGUITY_SET = []

    # Read live balance
    if _KALSHI_LIVE is not None:
        try:
            b = _KALSHI_LIVE.get_balance()
            bal = float(b.get("balance", 0)) / 100.0
            set_live_balance(bal)
            print(f"  ✓ live balance: ${bal:.2f}")
        except Exception as e:
            print(f"  ⚠ live balance read failed: {e}")

    # Spin up threads
    BOT_STATE.update({"running": True, "log": [], "threads": [],
                       "iter": 0, "trades": 0})
    workers = [
        ("spot_poller",   spot_poller),
        ("ws_listener",   lambda: ws_listener(_KALSHI_MD, _KALSHI_LIVE)),
        ("event_tracker", lambda: event_tracker(_KALSHI_MD)),
        ("decision",      _decision_worker),
    ]
    for name, fn in workers:
        th = threading.Thread(target=fn, daemon=True, name=f"v2_{name}")
        th.start()
        BOT_STATE["threads"].append(th)
    print(f"\n✓ bot running ({len(workers)} threads). mode={CFG.get('mode')}")


def stop_bot():
    BOT_STATE["running"] = False
    print("stopping bot... (threads daemon-exit at next sleep wake)")


def status(last_n_log_lines: int = 12):
    print("=" * 72)
    print(f"  V2 BOT STATUS @ {fmt_local_full()}")
    print("=" * 72)
    print(f"  mode:          {CFG.get('mode')}")
    print(f"  live_enabled:  {CFG.get('live_enabled')}")
    print(f"  running:       {BOT_STATE.get('running')}")
    print(f"  iter:          {BOT_STATE.get('iter')}")
    print(f"  trades:        {BOT_STATE.get('trades')}")
    bal = get_live_balance()
    print(f"  live balance:  {'$'+format(bal, ',.2f') if bal else 'unavailable'}")
    print(f"\n  Threads: {len(BOT_STATE.get('threads', []))}")
    for t in BOT_STATE.get("threads", []):
        print(f"    {t.name:25s}  alive={t.is_alive()}")
    print(f"\n  WebSocket:")
    print(f"    mode:          {WS_STATE.get('mode')}")
    print(f"    connected:     {WS_STATE.get('connected')}")
    print(f"    subscribed:    {WS_STATE.get('subscribed_event')}")
    print(f"    msgs received: {WS_STATE.get('msg_count')}")
    last_msg = WS_STATE.get('last_msg_ts')
    print(f"    last msg:      {fmt_local(last_msg) if last_msg else None}")
    print(f"\n  Tracked event:  {TRACKED.get('event')}")
    print(f"  Books in mem:   {len(BOOKS)}")
    print(f"\n  Empirical bank: {_EMPIRICAL_BANK['n'] if _EMPIRICAL_BANK else 'not built'}")
    print(f"  Ambiguity set:  {len(_AMBIGUITY_SET)} measures")
    print(f"\n  Log tail:")
    for line in BOT_STATE.get("log", [])[-last_n_log_lines:]:
        print(f"    {line}")


def kill_switch():
    """Halt everything, switch to paper, refresh sessions."""
    print("KILL SWITCH activated...")
    stop_bot()
    CFG["mode"] = "paper"
    CFG["live_enabled"] = False
    BOT_STATE["running"] = False
    import requests
    if _KALSHI_MD is not None:
        _KALSHI_MD.session = requests.Session()
    if _KALSHI_LIVE is not None:
        _KALSHI_LIVE.session = requests.Session()
    print("  ✓ mode=paper, live_enabled=False, sessions refreshed")


def cancel_all_live_orders():
    if _KALSHI_LIVE is None:
        print("kalshi_live not available"); return
    try:
        orders = _KALSHI_LIVE._get(
            "/portfolio/orders", {"status": "resting"}).get("orders", []) or []
    except Exception as e:
        print(f"fetch err: {e}"); return
    n_ok = n_fail = 0
    for o in orders:
        try:
            _KALSHI_LIVE.cancel_order(o["order_id"])
            n_ok += 1
        except Exception as e:
            n_fail += 1
            print(f"  ✗ {o['order_id']}: {e}")
    print(f"canceled: {n_ok} ok / {n_fail} fail")


def enable_live():
    if _KALSHI_LIVE is None:
        print("✗ kalshi_live not initialized"); return
    bal = get_live_balance()
    if bal is None:
        try:
            b = _KALSHI_LIVE.get_balance()
            bal = float(b.get("balance", 0)) / 100.0
            set_live_balance(bal)
        except Exception as e:
            print(f"✗ balance read failed: {e}"); return
    if bal is None or bal <= 0:
        print("✗ live balance is $0 — refusing"); return
    CFG["mode"] = "live"
    CFG["live_enabled"] = True
    print(f"✓ LIVE TRADING ENABLED. balance=${bal:.2f}")


def disable_live():
    CFG["mode"] = "paper"
    CFG["live_enabled"] = False
    print("LIVE DISABLED. mode=paper.")


# ════════════════════════════════════════════════════════════════════════
#  Live diagnostic — what the bot is actually thinking
# ════════════════════════════════════════════════════════════════════════
def dashboard():
    """Single-snapshot view of the full pipeline. Re-run any time.

    Sections:
      A. Connectivity     — threads + WS + REST status
      B. What we're tracking — event, TTL, book count, sample quotes
      C. What scan_signals sees — full per-market edge breakdown,
         including markets that fail each filter and the reason
      D. Robust filter outcomes — last 10 decisions with pass rate
      E. Risk preflight blocks — last 10 rejections with reasons
      F. Recent paper trades + open positions
    """
    from .data import causal_sigma_from_spot
    from .strategy import scan_signals, kalshi_fee
    from .model import fair_value
    from .robust import robust_filter
    from .paper_db import open_trades, settled_trades, _conn
    import pandas as pd

    print("=" * 78)
    print(f"  V2 DASHBOARD @ {fmt_local_full()}")
    print("=" * 78)

    # ─────── A. Connectivity ───────
    print("\nA. CONNECTIVITY")
    threads = BOT_STATE.get("threads", [])
    alive = sum(1 for t in threads if t.is_alive())
    print(f"   threads alive:  {alive}/{len(threads)}")
    for t in threads:
        flag = "✓" if t.is_alive() else "✗"
        print(f"     {flag} {t.name}")
    print(f"   ws connected:   {WS_STATE.get('connected')}  "
          f"(mode={WS_STATE.get('mode')}, msgs={WS_STATE.get('msg_count')})")
    print(f"   ws subscribed:  {WS_STATE.get('subscribed_event')}")

    # ─────── B. What we're tracking ───────
    print("\nB. TRACKING")
    spot  = SPOT.get("price")
    sigma = causal_sigma_from_spot()
    event = TRACKED.get("event")
    ct    = TRACKED.get("close_time")
    ttl_min = ((ct - datetime.now(timezone.utc)).total_seconds() / 60) if ct else None
    print(f"   spot:           ${spot:,.2f}" if spot else "   spot:           (no data)")
    print(f"   causal sigma:   {sigma:.4f}" if sigma else "   causal sigma:   (need 15+ ticks)")
    print(f"   event:          {event}")
    if ct:
        print(f"   closes:         {fmt_local(ct)}  (ttl {ttl_min:+.1f} min)")
    print(f"   books in mem:   {len(BOOKS)}")
    if BOOKS:
        sample = sorted(BOOKS.items())[:3]
        print(f"   sample books:")
        for tk, b in sample:
            yb, ya, fl = b.get("yes_bid"), b.get("yes_ask"), b.get("floor")
            print(f"     {tk:35s} yes_bid={yb}  yes_ask={ya}  floor={fl}")

    # ─────── C. What scan_signals sees ───────
    print("\nC. SCANNER (this snapshot, not the running worker)")
    if not (spot and sigma and event):
        print("   skipped — missing spot / sigma / event")
    else:
        # Build per-market edge breakdown manually so we can show rejections
        rows = []
        for tk, b in list(BOOKS.items()):
            if not tk.startswith(event):
                continue
            yb, ya = b.get("yes_bid"), b.get("yes_ask")
            floor = b.get("floor"); cap = b.get("cap")
            reason = None
            edge_c = None
            side = None; entry = None
            if yb is None or ya is None:
                reason = "no quote"
            elif (ya - yb) > CFG["max_spread_cents"] / 100:
                reason = f"spread {(ya-yb)*100:.1f}c > {CFG['max_spread_cents']}c"
            elif floor is None:
                reason = "no floor"
            else:
                try:
                    floor_f = float(floor)
                except (ValueError, TypeError):
                    reason = "bad floor"
                else:
                    p_yes = fair_value(tk, spot, floor_f, cap, ttl_min, sigma,
                                          kurt=0.0, empirical_bank=_EMPIRICAL_BANK)
                    if p_yes is None:
                        reason = "fair_value None"
                    else:
                        edge_yes = (p_yes - ya) * 100 - kalshi_fee(ya) * 100
                        edge_no  = (yb - p_yes) * 100 - kalshi_fee(1 - yb) * 100
                        if edge_yes >= edge_no:
                            side, edge_c, entry = "yes", edge_yes, ya
                        else:
                            side, edge_c, entry = "no",  edge_no,  1 - yb
                        if edge_c < CFG["min_edge_cents"]:
                            reason = f"edge {edge_c:+.1f}c < {CFG['min_edge_cents']}c"
                        elif entry < CFG["min_entry_price"]:
                            reason = f"entry {entry:.3f} < {CFG['min_entry_price']}"
                        elif entry > CFG["max_entry_price"]:
                            reason = f"entry {entry:.3f} > {CFG['max_entry_price']}"
                        else:
                            reason = "PASSES edge filters"
            rows.append({"ticker": tk, "side": side, "entry": entry,
                          "edge_c": edge_c, "verdict": reason})
        if rows:
            df = pd.DataFrame(rows).sort_values(
                "edge_c", ascending=False, na_position="last")
            passes = df[df["verdict"] == "PASSES edge filters"]
            print(f"   {len(rows)} markets in event scope, {len(passes)} pass edge filters")
            top = df.head(8)
            for _, r in top.iterrows():
                e = f"{r['edge_c']:+.1f}c" if pd.notna(r["edge_c"]) else "  —  "
                en = f"{r['entry']:.3f}" if pd.notna(r["entry"]) else "  —  "
                sd = r["side"] or "—"
                print(f"     {r['ticker']:32s} {sd:>3s} entry={en} edge={e}  · {r['verdict']}")
        else:
            print("   no markets in event scope")

        # Also call the real scan_signals to show what robust filter does
        try:
            sigs = scan_signals(empirical_bank=_EMPIRICAL_BANK,
                                  ambiguity_set=_AMBIGUITY_SET)
            print(f"\n   scan_signals → {len(sigs)} signals after robust filter")
            if len(sigs):
                cols = ["ticker", "side", "entry_price", "edge_c",
                        "robust_pass_rate", "robust_mean_edge_c"]
                cols = [c for c in cols if c in sigs.columns]
                print("   " + sigs[cols].to_string(index=False).replace("\n", "\n   "))
        except Exception as e:
            print(f"   scan_signals error: {e}")

    # ─────── D. Robust filter outcomes ───────
    print("\nD. ROBUST FILTER (last 10 decisions)")
    try:
        conn = _conn()
        rd = pd.read_sql_query(
            "SELECT ts, ticker, side, entry_price, pass_rate, mean_edge_c, "
            "min_edge_c, max_edge_c, passed FROM robust_decisions "
            "ORDER BY ts DESC LIMIT 10", conn)
        conn.close()
        if len(rd) == 0:
            print("   no robust decisions logged yet")
        else:
            for _, r in rd.iterrows():
                pf = "✓" if r["passed"] else "✗"
                print(f"   {pf} {fmt_local(r['ts'])} {r['ticker']:32s} {r['side']:>3s}  "
                      f"entry=${r['entry_price']:.3f}  pass_rate={r['pass_rate']:.2f}  "
                      f"edge=[{r['min_edge_c']:+.1f}, {r['max_edge_c']:+.1f}]")
    except Exception as e:
        print(f"   error: {e}")

    # ─────── E. Risk-block log ───────
    print("\nE. RISK BLOCKS (last 10)")
    if not RISK_BLOCKS:
        print("   none")
    else:
        for rb in RISK_BLOCKS[-10:]:
            print(f"   {fmt_local(rb['ts'])} {rb['ticker']:32s} {rb['side']:>3s}  "
                  f"→ {'; '.join(rb['reasons'])}")

    # ─────── F. Trades ───────
    print("\nF. TRADES")
    op = open_trades()
    st = settled_trades()
    print(f"   open:     {len(op)}")
    print(f"   settled:  {len(st)}")
    if len(st):
        wins = (st['pnl_dollars'] > 0).sum()
        print(f"   realized: ${st['pnl_dollars'].sum():+.2f}  "
              f"(win rate {wins}/{len(st)} = {wins/len(st)*100:.1f}%)")
    if len(op):
        print(f"   open positions:")
        cols = ["id", "market_ticker", "side", "contracts", "entry_price",
                "entry_edge_cents", "trade_type"]
        cols = [c for c in cols if c in op.columns]
        for _, r in op.iterrows():
            print(f"     #{r['id']} {r['market_ticker']:32s} {r['side']:>3s} "
                  f"x{r['contracts']} @ ${r['entry_price']:.3f}  "
                  f"({r.get('trade_type', '?')})")

    # ─────── log tail ───────
    print("\nG. RECENT LOG")
    for line in BOT_STATE.get("log", [])[-15:]:
        print(f"   {line}")
    print()


def tail_log(n: int = 30, follow_secs: float = 0):
    """Print the last n log lines. With follow_secs > 0, blocks and
    streams new lines for that duration."""
    seen = 0
    if follow_secs <= 0:
        for line in BOT_STATE.get("log", [])[-n:]:
            print(line)
        return
    end = time.time() + follow_secs
    while time.time() < end:
        log = BOT_STATE.get("log", [])
        # Print from `seen` onward (capped at last n on first pass)
        if seen == 0:
            start = max(0, len(log) - n)
        else:
            start = seen
        for line in log[start:]:
            print(line, flush=True)
        seen = len(log)
        time.sleep(1.0)
