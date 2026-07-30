"""Generates the four Jupyter notebooks from inline source.

Run from `prediction-arb/` directory:
    python3 build_notebooks.py
"""
from __future__ import annotations
import json
from pathlib import Path


def make_nb(cells) -> dict:
    nb_cells = []
    for kind, src in cells:
        if kind == "md":
            nb_cells.append({"cell_type": "markdown", "metadata": {},
                             "source": src.splitlines(keepends=True)})
        else:
            nb_cells.append({"cell_type": "code", "metadata": {},
                             "execution_count": None, "outputs": [],
                             "source": src.splitlines(keepends=True)})
    return {
        "cells": nb_cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }


# ═══════════════════════════════════════════════════════════════════
NB_01 = [
    ("md", """# 01 — Market Exploration

Connect to both APIs and explore what's available on each platform.

**Run this first** to verify your setup and to get a feel for the universe.
"""),

    ("code", """import asyncio, sys, os
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient
from collections import Counter
import pandas as pd

# Common Kalshi series — covers the categories that overlap with Polymarket
KALSHI_SERIES = [
    "KXPRES", "KXSEN", "KXHOUSE", "KXIMPEACH",
    "KXBTCD", "KXETHD", "KXBTC", "KXETH", "KXSOL",
    "KXFEDDECISION", "KXCPI", "KXJOBS", "KXGDP", "KXRATE",
    "KXSPX", "KXNASDAQ",
    "KXWORLDCUP", "KXNBA", "KXNFL", "KXMLB",
    "KXWAR", "KXPUTIN",
]

k = KalshiClient(environment="production")
p = PolymarketClient()
print(f"Kalshi authed={k._authed}  Polymarket authed={p._authed}  (read-only is fine for exploration)")
"""),

    ("md", "## Pull markets from both platforms\n"),

    ("code", """k_markets, p_markets = await asyncio.gather(
    k.get_markets_by_series(KALSHI_SERIES, limit_per_series=50),
    p.get_markets(limit=500),
)
print(f"Kalshi: {len(k_markets)} markets")
print(f"Poly:   {len(p_markets)} markets")
"""),

    ("md", "## Category breakdown\n"),

    ("code", """k_cats = Counter(m.category for m in k_markets)
p_cats = Counter(m.category for m in p_markets)
df_cats = pd.DataFrame({"kalshi": k_cats, "polymarket": p_cats}).fillna(0).astype(int)
df_cats["overlap_potential"] = df_cats[["kalshi","polymarket"]].min(axis=1)
df_cats.sort_values("overlap_potential", ascending=False)
"""),

    ("md", "## Sample markets side by side per category\n"),

    ("code", """for cat in ["politics", "crypto", "economics", "sports", "geopolitics", "finance"]:
    ks = [m for m in k_markets if m.category == cat][:3]
    ps = [m for m in p_markets if m.category == cat][:3]
    if not (ks or ps): continue
    print(f"\\n══ {cat.upper()} ══")
    print("  KALSHI:")
    for m in ks: print(f"    [{m.market_id[:35]:35s}] {m.title[:75]}")
    print("  POLYMARKET:")
    for m in ps: print(f"    [{m.market_id[:35]:35s}] {m.title[:75]}")
"""),

    ("md", "## Volume distribution (Polymarket)\n"),

    ("code", """import plotly.express as px
df_p = pd.DataFrame([{"title": m.title[:60], "category": m.category,
                       "volume_usd": m.volume_usd} for m in p_markets])
df_p = df_p.sort_values("volume_usd", ascending=False).head(30)
fig = px.bar(df_p, y="title", x="volume_usd", color="category",
             title="Top 30 Polymarket markets by 24h volume",
             orientation="h", height=700)
fig.update_yaxes(autorange="reversed")
fig.show()
"""),

    ("code", """await k.close(); await p.close()
print("Done.")
"""),
]


# ═══════════════════════════════════════════════════════════════════
NB_02 = [
    ("md", """# 02 — Matching Analysis

Run the cross-platform matcher and inspect the candidate pairs.
"""),

    ("code", """import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient
from core.event_matcher import EventMatcher, flatten_market_pairs
from core.market_matcher import MatchCache
from core.fee_calculator import polymarket_fee_per_contract, kalshi_fee_per_contract
from data.models import Platform
import pandas as pd
"""),

    ("code", """KALSHI_SERIES = [
    "KXPRES", "KXSEN", "KXBTCD", "KXETHD", "KXBTC", "KXETH",
    "KXFEDDECISION", "KXCPI", "KXJOBS", "KXSPX", "KXNASDAQ",
    "KXWORLDCUP", "KXNBA", "KXWAR",
]
k = KalshiClient(environment="production")
p = PolymarketClient()
k_events, p_events = await asyncio.gather(
    k.get_events_with_markets(KALSHI_SERIES, limit_per_series=30),
    p.get_events_with_markets(limit=300),
)
print(f"Events: K={len(k_events)}  P={len(p_events)}")
print(f"Total nested markets: K={sum(len(e.markets) for e in k_events)}  "
      f"P={sum(len(e.markets) for e in p_events)}")
"""),

    ("code", """matcher = EventMatcher(min_event_score=0.45, strike_tolerance_pct=0.02)
event_pairs = matcher.match(k_events, p_events)
pairs = flatten_market_pairs(event_pairs)
print(f"Event matches: {len(event_pairs)}")
print(f"Market pairs:  {len(pairs)}")
"""),

    ("code", """# Event-level pairings
ep_rows = []
for ep in event_pairs:
    ep_rows.append({
        "conf": round(ep.confidence, 3),
        "method": ep.method.value,
        "kalshi_event": ep.kalshi_event.event_id[:35],
        "kalshi_title": ep.kalshi_event.title[:55],
        "poly_event": ep.polymarket_event.event_id[:35],
        "poly_title": ep.polymarket_event.title[:55],
        "n_market_pairs": len(ep.market_pairs),
        "end_delta_hrs": round(ep.end_date_delta_hours, 1),
    })
pd.DataFrame(ep_rows).sort_values("conf", ascending=False)
"""),

    ("code", """# Market-level pairings within events
rows = []
for pp in pairs:
    rows.append({
        "conf": round(pp.confidence, 3),
        "method": pp.method.value,
        "k_outcome": (pp.kalshi_market.raw.get('yes_sub_title','') or '')[:30],
        "p_outcome": (pp.polymarket_market.raw.get('groupItemTitle','') or '')[:30],
        "k_strike":  pp.kalshi_market.raw.get('floor_strike'),
        "p_threshold": pp.polymarket_market.raw.get('groupItemThreshold'),
        "kalshi_id": pp.kalshi_market.market_id[:32],
    })
pd.DataFrame(rows).sort_values("conf", ascending=False).head(50)
"""),

    ("md", "## Mark verified / rejected matches\n\nReview the table above. For pairs that ARE the same event, mark verified. For pairs that AREN'T, mark rejected. Both go into the cache for future runs.\n"),

    ("code", """# Example: mark first match as verified
cache = MatchCache(path="../data/cache/matches_cache.db")
# cache.mark_verified("KXBTCD-26MAY1812-T76000", "0xabc...")
# cache.mark_rejected("KXSEN-26-AZ-D", "wrong-poly-condition-id")
print(f"Verified cache size: {len(cache.get_verified())}")
print(f"Rejected cache size: {len(cache.get_rejected())}")
"""),

    ("md", "## Pull live prices for top-confidence pairs and check for ARB right now\n"),

    ("code", """top = pairs[:15]
prices = {}
for pp in top:
    try:
        kp, pp_pr = await asyncio.gather(
            k.get_price(pp.kalshi_market.market_id),
            p.get_price(pp.polymarket_market.market_id),
            return_exceptions=True,
        )
        if not isinstance(kp, Exception):
            prices[(Platform.KALSHI, pp.kalshi_market.market_id)] = kp
        if not isinstance(pp_pr, Exception):
            prices[(Platform.POLYMARKET, pp.polymarket_market.market_id)] = pp_pr
    except Exception as e:
        print(f"  err: {e}")
print(f"got prices for {len(prices)//2} pairs")
"""),

    ("code", """from core.arb_detector import detect_all
ops = detect_all(top, prices, min_net_edge_cents=-10, min_liquidity_usd=0, max_capital_usd=5000)
print(f"{len(ops)} arb candidates (including negative-edge for visibility)")
arb_rows = []
for op in ops:
    arb_rows.append({
        "direction": op.direction.value,
        "k_title": op.pair.kalshi_market.title[:50],
        "k_price": round(op.kalshi_price, 4),
        "p_price": round(op.polymarket_price, 4),
        "gross_c": round(op.gross_edge_cents, 2),
        "net_c": round(op.net_edge_cents, 2),
        "size": round(op.max_size_contracts, 0),
        "capital": round(op.capital_required_usd, 0),
        "ann_ret%": round(op.annualized_return_pct or 0, 1),
    })
pd.DataFrame(arb_rows)
"""),

    ("code", """await k.close(); await p.close()
"""),
]


# ═══════════════════════════════════════════════════════════════════
NB_03 = [
    ("md", """# 03 — Paper Trading Dashboard (MAIN)

Live cross-venue arb scanner + paper executor + portfolio dashboard.

Open this notebook and run all cells top-to-bottom. The scanner will:
1. Pull markets from both platforms (refreshed periodically)
2. Run the matcher
3. Poll prices for matched pairs every N seconds
4. Detect arbs
5. Auto-execute in paper mode (no real money)
6. Display a live, color-coded table

**Stop with Jupyter's interrupt-kernel (■) button.**
"""),

    ("md", "## Setup\n"),

    ("code", """import asyncio, sys, time, os
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
import pandas as pd
import yaml
from IPython.display import clear_output, display
from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient
from core.event_matcher import EventMatcher, flatten_market_pairs
from core.arb_detector import detect_all
from core.portfolio import Portfolio
from core.risk_manager import RiskManager
from core.execution_engine import ExecutionEngine
from data.models import Platform

CFG = yaml.safe_load(open("../config.yaml"))

KALSHI_SERIES = [
    "KXPRES", "KXSEN", "KXHOUSE", "KXIMPEACH",
    "KXBTCD", "KXETHD", "KXBTC", "KXETH", "KXSOL",
    "KXFEDDECISION", "KXCPI", "KXJOBS",
    "KXSPX", "KXNASDAQ",
    "KXWORLDCUP", "KXNBA", "KXWAR",
]
print("Loaded config. Mode:", CFG["mode"])
"""),

    ("code", """k = KalshiClient(environment=CFG["kalshi"]["environment"])
p = PolymarketClient()
print(f"Kalshi authed={k._authed}  Polymarket authed={p._authed}")
"""),

    ("md", "## Pull markets + build match cache\n"),

    ("code", """matcher = EventMatcher(
    min_event_score=0.45,
    strike_tolerance_pct=0.02,
    max_end_date_delta_days=14,
)
k_events, p_events = await asyncio.gather(
    k.get_events_with_markets(KALSHI_SERIES, limit_per_series=30),
    p.get_events_with_markets(limit=300),
)
event_pairs = matcher.match(k_events, p_events)
pairs = flatten_market_pairs(event_pairs)
print(f"K events={len(k_events)}  P events={len(p_events)}")
print(f"Event-level matches: {len(event_pairs)}")
print(f"Market-level pairs:  {len(pairs)}")

# Show top event matches
for ep in event_pairs[:5]:
    n = len(ep.market_pairs)
    print(f"  [{ep.method.value:>6}] conf={ep.confidence:.2f}  {n} mkts  "
          f"K:'{ep.kalshi_event.title[:40]}' ↔ P:'{ep.polymarket_event.title[:40]}'")

# Filter to high-confidence pairs for scanning
PAIRS_TO_SCAN = [pp for pp in pairs if pp.confidence >= 0.65][:100]
print(f"\\nScanning {len(PAIRS_TO_SCAN)} pairs each cycle")
"""),

    ("md", "## Portfolio, risk, execution engine\n"),

    ("code", """portfolio = Portfolio(
    db_path="../" + CFG["paper"]["db_path"],
    starting_capital_usd=CFG["paper"]["starting_capital_usd"],
)
risk = RiskManager(
    max_position_per_market_usd=CFG["risk"]["max_position_per_market_usd"],
    max_total_exposure_usd=CFG["risk"]["max_total_exposure_usd"],
    max_daily_loss_usd=CFG["risk"]["max_daily_loss_usd"],
    min_net_edge_cents=CFG["arbitrage"]["min_net_edge_cents"],
    min_match_confidence=CFG["risk"]["min_match_confidence"],
    min_liquidity_usd=CFG["arbitrage"]["min_liquidity_usd"],
)
engine = ExecutionEngine(k, p, portfolio, risk, mode="paper")
print("Engine ready in PAPER mode.")
print(f"Starting capital: ${portfolio.starting_capital:,.2f}")
print(f"Current cash:     ${portfolio.cash_usd():,.2f}")
"""),

    ("md", """## Live scanner + executor

Runs continuously. Pulls prices every `POLL_INTERVAL` seconds, detects arbs,
auto-executes in paper mode. Updates a colored table in place.
"""),

    ("code", """async def pull_prices(pairs):
    \"\"\"Pull current top-of-book for both legs of each pair.

    CRITICAL: pass the explicit yes_token_id when querying Polymarket so we
    hit the right book directly. Without it, the get_price fallback path
    can return the same (wrong) market data for every pair.
    \"\"\"
    tasks = []
    for pp in pairs:
        tasks.append(k.get_price(pp.kalshi_market.market_id))
        tasks.append(p.get_price(pp.polymarket_market.market_id,
                                  token_id=pp.polymarket_market.yes_token_id))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    prices = {}
    for i, pp in enumerate(pairs):
        kr = results[2*i]; pr = results[2*i+1]
        if not isinstance(kr, Exception):
            prices[(Platform.KALSHI, pp.kalshi_market.market_id)] = kr
        if not isinstance(pr, Exception):
            prices[(Platform.POLYMARKET, pp.polymarket_market.market_id)] = pr
    return prices

def style_row(row):
    net = row.get("net_c")
    if net is None:
        return [""] * len(row)
    if net >= CFG["arbitrage"]["min_net_edge_cents"]:
        return ["background-color: #d4edda"] * len(row)   # green
    if net >= 0:
        return ["background-color: #fff3cd"] * len(row)   # yellow
    return [""] * len(row)
"""),

    ("code", """POLL_INTERVAL = CFG["scanning"]["poll_interval_seconds"]
RUN_CYCLES = 60   # ~30 min at 30s interval

for cycle in range(RUN_CYCLES):
    t0 = time.time()
    prices = await pull_prices(PAIRS_TO_SCAN)
    ops = detect_all(
        PAIRS_TO_SCAN, prices,
        min_net_edge_cents=-5.0,  # show even negative for the live table
        min_liquidity_usd=CFG["arbitrage"]["min_liquidity_usd"],
        max_capital_usd=CFG["risk"]["max_position_per_market_usd"],
        max_slippage_pct=CFG["arbitrage"]["max_slippage_pct"],
    )
    # Auto-execute positive-net opportunities
    for op in ops:
        if op.net_edge_cents >= CFG["arbitrage"]["min_net_edge_cents"]:
            await engine.execute(op)
    # Build table
    rows = []
    for pp in PAIRS_TO_SCAN:
        kp = prices.get((Platform.KALSHI, pp.kalshi_market.market_id))
        pr = prices.get((Platform.POLYMARKET, pp.polymarket_market.market_id))
        if not (kp and pr): continue
        # Find matching arb op for this pair
        op = next((o for o in ops if o.pair is pp), None)
        rows.append({
            "kalshi_id": pp.kalshi_market.market_id[:30],
            "kalshi_title": pp.kalshi_market.title[:45],
            "K_yes_ask": round(kp.yes_ask or 0, 4),
            "P_yes_ask": round(pr.yes_ask or 0, 4),
            "K_no_ask": round(kp.no_ask or 0, 4),
            "P_no_ask": round(pr.no_ask or 0, 4),
            "gross_c": round(op.gross_edge_cents, 2) if op else None,
            "net_c":   round(op.net_edge_cents, 2) if op else None,
            "size":    round(op.max_size_contracts, 0) if op else None,
            "signal":  "ARB" if (op and op.net_edge_cents >= CFG["arbitrage"]["min_net_edge_cents"])
                       else ("WATCH" if op and op.net_edge_cents >= 0 else ""),
        })
    df = pd.DataFrame(rows).sort_values("net_c", ascending=False, na_position="last")

    # Portfolio snapshot
    snap = portfolio.snapshot()

    clear_output(wait=True)
    print(f"══ Cycle {cycle+1}/{RUN_CYCLES}  ({time.strftime('%H:%M:%S')}) ══")
    print(f"  Pairs scanned: {len(rows)}  Arb signals: {sum(1 for r in rows if r['signal']=='ARB')}")
    print(f"  Cash: ${snap.cash_usd:,.2f}  Locked: ${snap.locked_in_positions_usd:,.2f}  "
          f"Open arbs: {snap.open_arb_pairs}  Realized: ${snap.realized_pnl_usd:+,.2f}")
    print(f"  Kill switch: {'YES' if risk.kill_switch else 'no'}")
    display(df.head(20).style.apply(style_row, axis=1))

    # Sleep
    elapsed = time.time() - t0
    await asyncio.sleep(max(0, POLL_INTERVAL - elapsed))

print("Scanner stopped.")
"""),

    ("md", "## Portfolio dashboard\n"),

    ("code", """snap = portfolio.snapshot()
print(f"══ PORTFOLIO ══")
print(f"  Starting:       ${snap.starting_capital_usd:,.2f}")
print(f"  Cash:           ${snap.cash_usd:,.2f}")
print(f"  Locked:         ${snap.locked_in_positions_usd:,.2f}")
print(f"  Realized PnL:   ${snap.realized_pnl_usd:+,.2f}")
print(f"  Unrealized:     ${snap.unrealized_pnl_usd:+,.2f}")
print(f"  Equity:         ${snap.equity_usd:,.2f}")
print(f"  Total return:   {snap.total_return_pct:+.2f}%")
print(f"  Open arbs:      {snap.open_arb_pairs}")
print(f"  Settled arbs:   {snap.settled_arb_pairs}  ({snap.wins}W/{snap.losses}L)")
"""),

    ("code", """# Open arbs in detail
df_open = portfolio.open_arb_pairs_df()
df_open
"""),

    ("code", """# Recent events
portfolio.all_events_df().head(20)
"""),

    ("md", "## Analytics\n"),

    ("code", """import plotly.graph_objects as go
events = portfolio.all_events_df()
import json as _json
opens = events[events["event_type"] == "open_arb"].copy()
if len(opens):
    opens["edge_usd"] = opens["details"].apply(lambda s: _json.loads(s).get("expected_edge_usd", 0))
    fig = go.Figure(go.Histogram(x=opens["edge_usd"], nbinsx=30))
    fig.update_layout(title="Distribution of expected edges captured (USD)",
                       xaxis_title="Expected net edge ($)", yaxis_title="Trades",
                       height=400)
    fig.show()
else:
    print("No trades yet.")
"""),

    ("md", "## Cleanup\n"),

    ("code", """await k.close(); await p.close()
print("Closed. Run the scanner cell again any time to resume.")
"""),
]


# ═══════════════════════════════════════════════════════════════════
NB_04 = [
    ("md", """# 04 — Go-Live Checklist

Run this notebook BEFORE switching `mode: live` in config.yaml.
"""),

    ("code", """import asyncio, sys, os
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient

k = KalshiClient(environment="production")
p = PolymarketClient()
print(f"Kalshi authed: {k._authed}")
print(f"Polymarket authed: {p._authed}")
assert k._authed, "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH first"
# Polymarket auth not yet required for paper, but check before going live
"""),

    ("code", """# Verify Kalshi balance call works
balance = await k.get_balance_usd()
print(f"Kalshi USD balance: ${balance:.2f}")
positions = await k.get_positions()
print(f"Kalshi open positions: {len(positions)}")
"""),

    ("code", """# Pre-flight checklist
checklist = {
    "Kalshi authed":           k._authed,
    "Kalshi balance>0":        balance > 0,
    "Polymarket authed":       p._authed,
    "Match cache populated":   Path("../data/cache/matches_cache.db").exists(),
    "Paper trades reviewed":   Path("../data/paper_trades.db").exists(),
}
for name, ok in checklist.items():
    print(f"  [{'✓' if ok else '✗'}] {name}")

all_ok = all(checklist.values())
print(f"\\n{'✅ READY FOR LIVE TRADING' if all_ok else '❌ STILL NEED WORK BEFORE GOING LIVE'}")
"""),

    ("code", """await k.close(); await p.close()
"""),
]


# ═══════════════════════════════════════════════════════════════════
def build():
    nb_dir = Path("notebooks")
    nb_dir.mkdir(exist_ok=True)
    for name, cells in [
        ("01_market_exploration", NB_01),
        ("02_matching_analysis", NB_02),
        ("03_paper_trading", NB_03),
        ("04_go_live_checklist", NB_04),
    ]:
        out = nb_dir / f"{name}.ipynb"
        out.write_text(json.dumps(make_nb(cells), indent=1))
        print(f"wrote {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
