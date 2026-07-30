"""Market universe discovery.

Polls each venue's REST API to find the set of currently-active BTC
short-horizon markets. The capture script subscribes to whatever this
returns and re-subscribes when the set changes.

Kalshi: KXBTC15M is the only sub-hour BTC binary series (no 5m on Kalshi
as of 2026-06). We also include KXBTCD (hourly above/below) so the user
has the hourly side-by-side.

Polymarket: slug pattern `btc-updown-5m-<unix-ts>` covers the rolling 5-min
BTC up/down events. We resolve each event to its child markets and pull
both YES/NO token_ids.
"""
from __future__ import annotations
import json, time
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

import requests


KALSHI_REST = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_REST  = "https://gamma-api.polymarket.com"

# Edit these to add/remove Kalshi series:
KALSHI_SERIES_5M = ("KXBTC15M",)  # closest to 5-min; Kalshi has no 5-min BTC
KALSHI_SERIES_EXTRA = ("KXBTCD",)  # hourly above/below, also tracked


def _get_json(url: str, params: dict = None, timeout: float = 10.0) -> dict:
    r = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def discover_kalshi_btc_markets(series: Tuple[str, ...] = None,
                                kxbtcd_nearest_n_events: int = 1) -> List[Dict]:
    """Return open Kalshi market dicts for the given BTC series.

    Each item: {ticker, event_ticker, series, close_time}.

    For the hourly KXBTCD series we keep only the next `kxbtcd_nearest_n_events`
    upcoming events — otherwise the discovery would return the full strike
    grid for every open hour (~300+ tickers) which would saturate the WS sub.
    KXBTC15M is small enough that we keep every event.
    """
    from datetime import datetime, timezone
    series = series or (KALSHI_SERIES_5M + KALSHI_SERIES_EXTRA)
    out: List[Dict] = []
    now = datetime.now(timezone.utc)
    for ser in series:
        try:
            events = _get_json(f"{KALSHI_REST}/events",
                                {"series_ticker": ser, "status": "open",
                                 "limit": 200, "with_nested_markets": True})
        except Exception as e:
            print(f"[discover] kalshi events err {ser}: {e}", flush=True)
            continue
        ev_list = events.get("events", []) or []
        # Sort by close_time ascending.
        def _ct(ev):
            mks = ev.get("markets") or []
            if not mks: return ""
            return mks[0].get("close_time") or ""
        ev_list.sort(key=_ct)
        if ser == "KXBTCD":
            # Only the soonest-closing N hourly events; otherwise we sub ~300 tickers.
            ev_list = [ev for ev in ev_list if _ct(ev)][:kxbtcd_nearest_n_events]
        for ev in ev_list:
            et = ev.get("event_ticker", "")
            for m in (ev.get("markets") or []):
                tk = m.get("ticker")
                if not tk:
                    continue
                status = (m.get("status") or "").lower()
                if status not in ("active", "open", ""):
                    continue
                out.append({
                    "ticker":       tk,
                    "event_ticker": et,
                    "series":       ser,
                    "close_time":   m.get("close_time"),
                })
    return out


def discover_polymarket_btc_5m(window_minutes: int = 30) -> List[Dict]:
    """Return open Polymarket BTC 5-min up/down markets whose end is in
    the next `window_minutes`. We subscribe ahead so the book is captured
    from the moment the market goes live.

    Each item: {condition_id, slug, yes_token_id, no_token_id, end_date}.
    """
    out: List[Dict] = []
    seen_cond: Set[str] = set()
    offset, page_size = 0, 100
    now = datetime.now(timezone.utc)
    cutoff_lo = int(now.timestamp())
    cutoff_hi = cutoff_lo + window_minutes * 60

    # Easiest path: query events sorted by endDate ascending and stop once
    # we pass the cutoff. We try a few sort orders because Gamma's filter
    # behavior shifts.
    try:
        events = _get_json(f"{GAMMA_REST}/events",
                            {"limit": 200, "active": "true", "closed": "false",
                             "order": "endDate", "ascending": "true",
                             "tag_slug": "crypto"})
    except Exception as e:
        print(f"[discover] polymarket events err: {e}", flush=True)
        events = []
    if isinstance(events, dict) and "data" in events:
        events = events["data"]
    events = events or []

    for ev in events:
        slug = ev.get("slug", "")
        if not slug.startswith("btc-updown-5m-"):
            continue
        end_str = ev.get("endDate", "")
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        except Exception:
            continue
        end_ts = int(end_dt.timestamp())
        if end_ts < cutoff_lo or end_ts > cutoff_hi + 7200:
            # outside our forward window (we allow a bit more headroom for early subs)
            continue
        for m in ev.get("markets", []) or []:
            cond = m.get("conditionId") or m.get("condition_id") or ""
            if not cond or cond in seen_cond:
                continue
            seen_cond.add(cond)
            tok_field = m.get("clobTokenIds")
            tokens = []
            if isinstance(tok_field, str):
                try:
                    tokens = json.loads(tok_field)
                except Exception:
                    tokens = []
            elif isinstance(tok_field, list):
                tokens = tok_field
            yes_tok = tokens[0] if len(tokens) > 0 else None
            no_tok  = tokens[1] if len(tokens) > 1 else None
            out.append({
                "condition_id": cond,
                "slug":         slug,
                "yes_token_id": yes_tok,
                "no_token_id":  no_tok,
                "end_date":     end_str,
                "question":     m.get("question") or ev.get("title", ""),
            })
    return out
