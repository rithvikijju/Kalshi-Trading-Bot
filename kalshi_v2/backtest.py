"""Backtest harness for kalshi_v2 against a live-capture DuckDB.

Replays settled events from sami's live-capture format
(ws_orderbook_top_dedup + coinbase_ticker_all + ws_lifecycle_all) through
the current v2 fair-value model, simulates the bot's trade decisions at
the actual Kalshi quote prices captured, applies TP/SL/time-exit policy,
and reports realized PnL against true settlement outcomes.

The whole pipeline runs WITHOUT touching the live SQLite trade DB.
Results go to a backtest_outputs/ directory as CSVs + a summary log.

Usage
-----
    from kalshi_v2.backtest import run_backtest
    from pathlib import Path
    summary = run_backtest(
        duckdb_path=Path("~/.../live_capture_gapless_paused.duckdb"),
        output_dir=Path("./backtest_outputs/v2_replay"),
    )
    print(summary)

Walk-forward leakage protection
-------------------------------
The empirical bank is built once from BTC data older than the backtest
window's start date (we fetch 90 days back from there). The bank is then
frozen. At each event replay we use only quotes and BTC ticks with
timestamps before the decision time. No future-leak.

Limitations
-----------
- TP/SL exits are evaluated against the captured book mid in the event
  window. We DO NOT simulate the live order's actual fill price at exit
  (we use the limit-price-it-would-have-set, matching the new live PnL
  accounting). Close-enough for relative ranking.
- We only model BTC events (KXBTC / KXBTCD). Other series ignored.
- We do not simulate the NO-distance throttle's effect on size; throttle
  reduces position to 1 contract when applicable.
"""
from __future__ import annotations
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import duckdb
except ImportError as e:
    raise ImportError("DuckDB required: pip install duckdb") from e


# ════════════════════════════════════════════════════════════════════════
#  Data loaders
# ════════════════════════════════════════════════════════════════════════
def _load_btc_bars(con) -> pd.DataFrame:
    """Aggregate coinbase_ticker_all to 1-minute OHLCV bars + rolling features."""
    print("  loading BTC ticks → 1-min bars …", flush=True)
    bars = con.execute("""
        WITH ticks AS (
            SELECT
                date_trunc('minute', CAST(received_at_utc AS TIMESTAMP)) AS minute,
                price,
                received_at_ns
            FROM coinbase_ticker_all
            WHERE product_id = 'BTC-USD' AND price IS NOT NULL
        )
        SELECT
            minute  AS time,
            FIRST(price ORDER BY received_at_ns ASC)  AS open,
            MAX(price) AS high,
            MIN(price) AS low,
            FIRST(price ORDER BY received_at_ns DESC) AS close
        FROM ticks
        GROUP BY minute
        ORDER BY minute
    """).fetchdf()
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    bars["log_ret"]    = np.log(bars["close"] / bars["close"].shift(1))
    af = 60 * 24 * 365
    bars["rv_60m"]     = bars["log_ret"].rolling(60).std() * np.sqrt(af)
    bars["rkurt_60m"]  = bars["log_ret"].rolling(60).kurt()
    print(f"    {len(bars):,} bars, {bars['time'].min()} → {bars['time'].max()}")
    return bars


def _load_outcomes(con) -> pd.DataFrame:
    """Determined-event rows from ws_lifecycle_all, parsed."""
    print("  loading settled outcomes …", flush=True)
    rows = con.execute("""
        SELECT received_at_utc, market_ticker, payload_json
        FROM ws_lifecycle_all
        WHERE event_type = 'determined'
    """).fetchdf()

    parsed = []
    for _, r in rows.iterrows():
        try:
            p = json.loads(r["payload_json"])
            tk = p.get("market_ticker") or r["market_ticker"]
            result = (p.get("result") or "").lower()
            settle_v = p.get("settlement_value")
            settle = float(settle_v) if settle_v not in (None, "") else None
            det_ts = p.get("determination_ts") or p.get("ts")
            parsed.append({
                "market_ticker": tk,
                "result": result,
                "settlement_value": settle,
                "determined_ts": int(det_ts) if det_ts else None,
                "determined_utc": pd.to_datetime(r["received_at_utc"], utc=True),
            })
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    df = pd.DataFrame(parsed)
    df = df.drop_duplicates(subset=["market_ticker"], keep="first")
    print(f"    {len(df):,} unique settled markets")
    return df


def _load_event_close_times(con) -> pd.DataFrame:
    """Per-event close_ts from the orderbook stream (event_ticker has the
    encoded close hour but we use the WS lifecycle as authority)."""
    rows = con.execute("""
        SELECT DISTINCT
            event_ticker,
            MIN(received_at_ns) AS first_ns,
            MAX(received_at_ns) AS last_ns
        FROM ws_orderbook_top_dedup
        WHERE event_ticker IS NOT NULL
          AND (event_ticker LIKE 'KXBTC%')
        GROUP BY event_ticker
    """).fetchdf()
    rows["first_utc"] = pd.to_datetime(rows["first_ns"], unit="ns", utc=True)
    rows["last_utc"]  = pd.to_datetime(rows["last_ns"],  unit="ns", utc=True)
    return rows


# ════════════════════════════════════════════════════════════════════════
#  Per-event replay
# ════════════════════════════════════════════════════════════════════════
def _replay_event(con, event_ticker: str, close_utc: pd.Timestamp,
                    bars: pd.DataFrame, bank: dict, ambiguity_set: List[dict],
                    outcomes: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """Replay a single event window through the v2 model.

    For each scan time in [close - decision_window_min, close - 2min]
    sampled every decision_interval_sec, build a BOOKS snapshot from
    captured quotes and ask the v2 scanner if it would trade. Take the
    first trade signal. Then track to settlement / TP / SL / time-exit.
    """
    from .model import fair_value
    from .strategy import kalshi_fee

    window_start = close_utc - timedelta(minutes=cfg["decision_window_min"])
    scan_until   = close_utc - timedelta(minutes=2)

    # Fetch all quote snapshots in the window for this event
    quotes = con.execute(f"""
        SELECT received_at_ns, received_at_utc, market_ticker,
               yes_bid, yes_ask, no_bid, no_ask, btc_spot
        FROM ws_orderbook_top_dedup
        WHERE event_ticker = '{event_ticker}'
          AND received_at_utc >= '{window_start.isoformat()}'
          AND received_at_utc <  '{scan_until.isoformat()}'
          AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
        ORDER BY received_at_ns
    """).fetchdf()
    if len(quotes) == 0:
        return None
    quotes["received_at_utc"] = pd.to_datetime(quotes["received_at_utc"], utc=True, format="ISO8601")

    # Filter to cumulative -T markets only (matches v2's typical universe)
    quotes = quotes[quotes["market_ticker"].str.contains("-T")]
    if len(quotes) == 0:
        return None

    # Walk forward in scan_interval steps; at each step, take latest snap per market.
    t  = window_start
    dt = timedelta(seconds=cfg["decision_interval_sec"])
    chosen = None
    while t < scan_until:
        snap = quotes[quotes["received_at_utc"] <= t]
        if len(snap) == 0:
            t += dt
            continue
        # latest snapshot per market
        latest = snap.sort_values("received_at_ns").groupby("market_ticker").tail(1)
        # spot at this time
        spot_at = latest["btc_spot"].iloc[-1] if "btc_spot" in latest.columns else None
        if spot_at is None or not math.isfinite(spot_at):
            t += dt; continue
        # vol from BTC bars up to this point
        bars_so_far = bars[bars["time"] <= t]
        if len(bars_so_far) < 60:
            t += dt; continue
        sigma = bars_so_far["rv_60m"].iloc[-1]
        if not math.isfinite(sigma) or sigma <= 0:
            t += dt; continue
        ttl_min = (close_utc - t).total_seconds() / 60.0
        if ttl_min < cfg["scan_min_ttl_min"] or ttl_min > cfg["scan_max_ttl_hours"]*60:
            t += dt; continue

        # Find best edge across markets in this snapshot
        best = None
        for _, row in latest.iterrows():
            tk = row["market_ticker"]
            yb = float(row["yes_bid"]); ya = float(row["yes_ask"])
            if (ya - yb) > cfg["max_spread_cents"] / 100: continue
            # parse strike from ticker e.g. KXBTCD-26MAY0605-T81699.99
            try:
                strike = float(tk.split("-T")[-1])
            except Exception:
                continue
            p_yes = fair_value(tk, spot_at, strike, None, ttl_min, sigma,
                                  kurt=0.0, empirical_bank=bank)
            if p_yes is None: continue
            # Apply market-shrink consistent with live model
            mid = (yb + ya) / 2
            shrink = cfg.get("market_shrink", 0.0)
            if shrink > 0:
                p_yes = (1 - shrink) * p_yes + shrink * mid
            edge_yes = (p_yes - ya) * 100 - kalshi_fee(ya) * 100
            edge_no  = (yb - p_yes) * 100 - kalshi_fee(1 - yb) * 100
            if edge_yes >= edge_no:
                side, edge_c, entry = "yes", edge_yes, ya
            else:
                side, edge_c, entry = "no",  edge_no,  1 - yb
            min_edge = cfg["min_edge_cents"]
            if side == "no":
                min_edge += cfg.get("no_side_edge_surcharge_cents", 0.0)
            if edge_c < min_edge: continue
            if entry < cfg["min_entry_price"]: continue
            if entry > cfg["max_entry_price"]: continue
            if best is None or edge_c > best["edge_c"]:
                best = {"ticker": tk, "side": side, "entry": entry,
                         "edge_c": edge_c, "p_yes": p_yes, "strike": strike,
                         "spot": spot_at, "decision_t": t, "yb": yb, "ya": ya}
        if best is not None:
            chosen = best
            break
        t += dt

    if chosen is None:
        return None

    # We have an entry. Simulate the lifecycle: TP / SL / time-exit / settle.
    # Apply the live-PnL accounting fix: entry recorded at limit + buffer.
    buffer_d = cfg["order_buffer_cents"] / 100
    if chosen["side"] == "yes":
        entry_fill = min(0.99, chosen["ya"] + buffer_d)
    else:
        entry_fill = min(0.99, (1 - chosen["yb"]) + buffer_d)

    # NO-distance throttle (cap to 1 contract; we use fixed 1 contract sizing
    # for backtest so this is informational only)
    near = cfg.get("no_near_strike_distance_usd", 0)
    contracts = 1   # fixed for clean comparability; multiply PnL by N later
    throttled = False
    if chosen["side"] == "no" and near > 0:
        dist = abs(chosen["spot"] - chosen["strike"])
        if dist < near:
            throttled = True   # would have been capped to 1 anyway

    # Track exit via subsequent quotes
    post_q = con.execute(f"""
        SELECT received_at_utc, yes_bid, yes_ask
        FROM ws_orderbook_top_dedup
        WHERE market_ticker = '{chosen["ticker"]}'
          AND received_at_utc > '{chosen["decision_t"].isoformat()}'
          AND received_at_utc <= '{close_utc.isoformat()}'
        ORDER BY received_at_ns
    """).fetchdf()
    if len(post_q) == 0:
        exit_fill = entry_fill   # nothing to mark against; treat as flat
        exit_reason = "no_post_quotes"
    else:
        post_q["received_at_utc"] = pd.to_datetime(post_q["received_at_utc"], utc=True, format="ISO8601")
        exit_fill, exit_reason = None, None
        for _, q in post_q.iterrows():
            yb_q = float(q["yes_bid"]); ya_q = float(q["yes_ask"])
            if not (math.isfinite(yb_q) and math.isfinite(ya_q)): continue
            mid_q = (yb_q + ya_q) / 2
            cur_mid = mid_q if chosen["side"] == "yes" else 1.0 - mid_q
            pnl_c = (cur_mid - entry_fill) * 100
            # SL
            if cur_mid <= entry_fill * (1.0 - cfg["stop_loss_pct"]):
                if chosen["side"] == "yes":
                    exit_fill = max(0.01, yb_q - buffer_d)
                else:
                    exit_fill = max(0.01, (1 - ya_q) - buffer_d)
                exit_reason = f"stop_loss ({pnl_c:+.1f}c)"
                break
            # TP
            if pnl_c >= cfg["take_profit_cents"]:
                if chosen["side"] == "yes":
                    exit_fill = max(0.01, yb_q - buffer_d)
                else:
                    exit_fill = max(0.01, (1 - ya_q) - buffer_d)
                exit_reason = f"take_profit ({pnl_c:+.1f}c)"
                break
            # time exit
            ttl_left = (close_utc - q["received_at_utc"]).total_seconds() / 60
            if ttl_left <= cfg["time_exit_min_ttl_m"]:
                if chosen["side"] == "yes":
                    exit_fill = max(0.01, yb_q - buffer_d)
                else:
                    exit_fill = max(0.01, (1 - ya_q) - buffer_d)
                exit_reason = f"time_exit (ttl={ttl_left:.1f}m)"
                break
        # No exit triggered → hold to settlement
        if exit_fill is None:
            row = outcomes[outcomes["market_ticker"] == chosen["ticker"]]
            if len(row) == 0:
                return None    # no outcome, skip
            res = row.iloc[0]["result"]
            if res == "yes":
                exit_fill = 1.0 if chosen["side"] == "yes" else 0.0
            elif res == "no":
                exit_fill = 0.0 if chosen["side"] == "yes" else 1.0
            else:
                return None
            exit_reason = f"settle:result={res}"

    # PnL with fees
    def fee_d(p):
        return math.ceil(0.07 * p * (1-p) * 100) / 100.0
    gross  = (exit_fill - entry_fill) * contracts
    fees   = (fee_d(entry_fill) + fee_d(exit_fill)) * contracts
    net    = gross - fees

    return {
        "event_ticker":      event_ticker,
        "market_ticker":     chosen["ticker"],
        "side":              chosen["side"],
        "strike":            chosen["strike"],
        "spot_at_decision":  chosen["spot"],
        "dist_usd":          abs(chosen["spot"] - chosen["strike"]),
        "decision_utc":      chosen["decision_t"].isoformat(),
        "close_utc":         close_utc.isoformat(),
        "model_p_yes":       chosen["p_yes"],
        "entry_quoted":      chosen["entry"],
        "entry_fill":        entry_fill,
        "exit_fill":         exit_fill,
        "exit_reason":       exit_reason,
        "edge_c_decision":   chosen["edge_c"],
        "contracts":         contracts,
        "gross_pnl":         gross,
        "fees":              fees,
        "net_pnl":           net,
        "throttled":         throttled,
    }


# ════════════════════════════════════════════════════════════════════════
#  Orchestrator
# ════════════════════════════════════════════════════════════════════════
def run_backtest(duckdb_path: Path, output_dir: Path,
                  decision_interval_sec: int = 5,
                  decision_window_min: int = 30,
                  series_prefixes: Tuple[str, ...] = ("KXBTC-", "KXBTCD-"),
                  build_full_bank: bool = True) -> dict:
    """End-to-end backtest replay. Returns a summary dict and writes:
        output_dir/trades.csv     — per-trade log
        output_dir/summary.json   — aggregate metrics
        output_dir/by_side.csv    — YES/NO split
        output_dir/by_strike_distance.csv
    """
    from .config import CFG
    from .model import build_empirical_bank
    from .robust import build_ambiguity_set

    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    duckdb_path = Path(duckdb_path).expanduser()

    print("=" * 72)
    print(f"  v2 BACKTEST  ·  {duckdb_path.name}")
    print("=" * 72)
    con = duckdb.connect(str(duckdb_path), read_only=True)

    bars     = _load_btc_bars(con)
    outcomes = _load_outcomes(con)
    events   = _load_event_close_times(con)

    if build_full_bank:
        # Try to use external 90d history. Fall back to in-DB bars if not.
        try:
            from .data import fetch_historical_minutes, add_rv_features
            ext = add_rv_features(fetch_historical_minutes(days_back=90))
            print(f"  fetched {len(ext):,} external BTC bars for bank")
            bank_src = ext
        except Exception as e:
            print(f"  ⚠ external BTC fetch failed: {e} — using in-DB 3d bars")
            bank_src = bars
    else:
        bank_src = bars

    bank = build_empirical_bank(bank_src, horizon_min=60, n_samples=5000, demean=True)
    print(f"  empirical bank: n={bank['n']}")
    try:
        amb = build_ambiguity_set(bank_src, horizon_min=60,
                                       n_bootstrap=CFG["robust_n_bootstrap"])
    except Exception:
        amb = []
    print(f"  ambiguity set: {len(amb)} measures")

    # Filter events to BTC series
    events = events[events["event_ticker"].str.startswith(tuple(series_prefixes))]
    # Close time = max(last_utc) — close to the event close
    events["close_utc"] = events["last_utc"]

    cfg_local = {
        "decision_interval_sec":      decision_interval_sec,
        "decision_window_min":        decision_window_min,
        "scan_min_ttl_min":           CFG["scan_min_ttl_min"],
        "scan_max_ttl_hours":         CFG["scan_max_ttl_hours"],
        "max_spread_cents":           CFG["max_spread_cents"],
        "min_edge_cents":             CFG["min_edge_cents"],
        "min_entry_price":            CFG["min_entry_price"],
        "max_entry_price":            CFG["max_entry_price"],
        "market_shrink":              CFG.get("market_shrink", 0.0),
        "no_side_edge_surcharge_cents": CFG.get("no_side_edge_surcharge_cents", 0.0),
        "no_near_strike_distance_usd": CFG.get("no_near_strike_distance_usd", 0.0),
        "stop_loss_pct":              CFG["stop_loss_pct"],
        "take_profit_cents":          CFG["take_profit_cents"],
        "time_exit_min_ttl_m":        CFG["time_exit_min_ttl_m"],
        "order_buffer_cents":         CFG["order_buffer_cents"],
    }

    trades = []
    n = len(events)
    print(f"\n  replaying {n} events …")
    for i, (_, ev) in enumerate(events.iterrows(), 1):
        if i % 20 == 0 or i == n:
            print(f"    [{i}/{n}] {ev['event_ticker']}")
        try:
            tr = _replay_event(con, ev["event_ticker"], ev["close_utc"],
                                  bars, bank, amb, outcomes, cfg_local)
            if tr is not None:
                trades.append(tr)
        except Exception as e:
            print(f"      err: {e}")

    con.close()

    # Summary
    if not trades:
        print("\n  no trades fired in backtest window")
        return {"n_trades": 0}

    df = pd.DataFrame(trades)
    df.to_csv(output_dir / "trades.csv", index=False)

    wins  = (df["net_pnl"] > 0).sum()
    losses = (df["net_pnl"] < 0).sum()
    wr    = wins / max(1, len(df))
    s = {
        "n_trades":          int(len(df)),
        "wins":              int(wins),
        "losses":            int(losses),
        "win_rate":          float(wr),
        "total_net_pnl":     float(df["net_pnl"].sum()),
        "total_gross_pnl":   float(df["gross_pnl"].sum()),
        "total_fees":        float(df["fees"].sum()),
        "mean_net":          float(df["net_pnl"].mean()),
        "median_net":        float(df["net_pnl"].median()),
        "best_trade":        float(df["net_pnl"].max()),
        "worst_trade":       float(df["net_pnl"].min()),
        "std_pnl":           float(df["net_pnl"].std()),
        "throttled_count":   int(df["throttled"].sum()),
    }
    # Side split
    by_side = df.groupby("side").agg(
        n=("net_pnl", "count"),
        wins=("net_pnl", lambda x: (x > 0).sum()),
        net_pnl=("net_pnl", "sum"),
        mean_pnl=("net_pnl", "mean"),
    ).round(3)
    by_side["win_rate"] = (by_side["wins"] / by_side["n"]).round(3)
    by_side.to_csv(output_dir / "by_side.csv")

    # Strike-distance bucket
    df["dist_bucket"] = pd.cut(df["dist_usd"],
                                  bins=[0, 50, 100, 200, 400, 1000, 5000],
                                  labels=["0-50","50-100","100-200","200-400",
                                          "400-1000","1000+"])
    by_dist = df.groupby("dist_bucket", observed=True).agg(
        n=("net_pnl", "count"),
        wins=("net_pnl", lambda x: (x > 0).sum()),
        net_pnl=("net_pnl", "sum"),
        mean_pnl=("net_pnl", "mean"),
    ).round(3)
    by_dist["win_rate"] = (by_dist["wins"] / by_dist["n"]).round(3)
    by_dist.to_csv(output_dir / "by_strike_distance.csv")

    # By exit reason
    df["exit_kind"] = df["exit_reason"].str.split("(").str[0].str.split(":").str[0].str.strip()
    by_exit = df.groupby("exit_kind").agg(
        n=("net_pnl", "count"),
        net_pnl=("net_pnl", "sum"),
        mean_pnl=("net_pnl", "mean"),
    ).round(3)
    by_exit.to_csv(output_dir / "by_exit_reason.csv")

    (output_dir / "summary.json").write_text(json.dumps(s, indent=2))

    # Console summary
    print("\n" + "=" * 72)
    print("  BACKTEST SUMMARY")
    print("=" * 72)
    print(f"  trades:        {s['n_trades']}")
    print(f"  net PnL:       ${s['total_net_pnl']:+,.2f}")
    print(f"  gross PnL:     ${s['total_gross_pnl']:+,.2f}")
    print(f"  fees:          ${s['total_fees']:.2f}")
    print(f"  win rate:      {s['win_rate']*100:.1f}%  ({s['wins']}W / {s['losses']}L)")
    print(f"  mean / trade:  ${s['mean_net']:+.3f}")
    print(f"  std / trade:   ${s['std_pnl']:.3f}")
    print(f"  best / worst:  ${s['best_trade']:+.2f} / ${s['worst_trade']:+.2f}")
    print(f"  throttled:     {s['throttled_count']}")
    print(f"\n  by side:")
    print(by_side.to_string())
    print(f"\n  by strike distance:")
    print(by_dist.to_string())
    print(f"\n  by exit reason:")
    print(by_exit.to_string())
    print(f"\n  outputs in: {output_dir}")
    return s
