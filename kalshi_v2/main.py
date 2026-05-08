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
from .data import (BOT_STATE, WS_STATE, LOCK, SPOT, BOOKS, TRACKED,
                    spot_poller, ws_listener, event_tracker, _log,
                    fetch_historical_minutes, add_rv_features)
from .strategy import scan_signals
from .execution import (manage_open_positions, check_settlements,
                          place_smart_limit)
from .risk import (risk_preflight, size_for_mode, set_live_balance,
                    get_live_balance, RISK_BLOCKS)
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

        # Lipschitz-smoothed sizing
        raw_size  = size_for_mode(entry)
        contracts = SIZER.size("v2", raw_size, entry)

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
    print(f"  V2 BOT STATUS @ {datetime.now(timezone.utc).isoformat()}")
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
    print(f"    last msg:      {WS_STATE.get('last_msg_ts')}")
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
