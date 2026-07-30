# Live in-game mean-reversion strategy

**Buy the dip / fade the spike** on high-volatility Kalshi markets. When a price
overshoots on a fast momentum burst it tends to snap back toward its short-run
baseline. Flagship use case: **NBA game-winner markets** (a team goes on a run,
its win-probability price spikes past fair value, then reverts) — but the
price-based core is sport-agnostic and works on any volatile binary.

Built on the edge-bot `prediction-arb` infra: reuses `KalshiClient`, the fee
model, and the logger. Because it's **single-leg directional** (not a two-leg
arb), it ships its own SQLite portfolio + risk gate instead of reusing the
arb-pair portfolio.

## How it works

```
discover ──> quote ──> signal ──> manage exits ──> enter
```

1. **Discovery** (`market_discovery.py`) — pulls ESPN's live scoreboard, keeps
   in-progress games, and matches open `KXNBAGAME` markets to them *by team name*
   (ESPN uses `SA`/`NY`, Kalshi uses `SAS`/`NYK`, so abbr-matching fails). Each
   watched market carries its ESPN event id + home/away tag.
2. **Price feed** (`price_feed.py`) — top-of-book via `get_price`, falling back
   to **last-trade price** when the book is null (the public mirror returns empty
   books for some live sports markets even while they trade).
3. **Signal engine** (`signal_engine.py`) — the statistical core:
   - `baseline = EMA(yes_mid)`, `z = (mid − baseline) / rolling_std`
   - fire only when **stretched** (`|z| ≥ entry_z`) **and** the stretch came from
     a **fast burst** (`|Δ| ≥ spike_min_move` over `spike_window`). A slow drift
     is real repricing — fading it is how mean-reversion bleeds out.
   - everything reduces to *buy the underpriced side, expect its price to rise*:
     dip → buy YES; spike → buy NO.
4. **Fair-value overlay** (`fair_value.py`) — a live NBA win-probability model
   (margin + spread + time, normal approx). In `confirm` mode it **vetoes fades
   that agree with the model** (i.e. the move was justified); in `require` mode it
   only trades genuine overshoots. This is what stops the bot from fading real
   news (a star fouling out, an actual 12-0 run).
5. **Risk + exits** — per-market / total-exposure / concurrent caps, a daily-loss
   kill switch, and per-position take-profit / stop-loss / time-stop / near-close.

## The fee floor is the enemy

Per `memory/kalshi_fee_floor.md`: a directional binary pays Kalshi's fee on
**both** legs of the round trip. The entry gate (`SignalEngine`) requires the
expected reversion to clear `round_trip_fee + 2×slippage + min_edge_cents` before
it will fire — otherwise the trade is fee-negative on arrival. Fee model is
configurable (`kalshi_pct` = `ceil(0.07·P·(1−P))¢`, or the repo's `flat3`).

## Usage

```bash
cd prediction-arb

# 1. Backtest the mechanics on synthetic OU+spike paths
python -m strategies.mean_reversion.backtest --synthetic

# 2. Record a real price tape during live games (build backtest data)
python -m strategies.mean_reversion.recorder --minutes 180

# 3. Backtest on the captured tape
python -m strategies.mean_reversion.backtest --tape data/mr_tape.db

# 4. Paper trade live (default; no orders placed)
python -m strategies.mean_reversion.run --reset

# 5. Go live (real orders — requires KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH)
python -m strategies.mean_reversion.run --mode live
```

All knobs live in `config.yaml`.

## Status / honesty

This is **validated for mechanics, not yet for edge.** The synthetic backtest
confirms signals fire in both directions, all exit branches work, and fees are
charged on both legs. It does **not** prove the strategy makes money — that
requires a real captured tape (`recorder.py` → `backtest.py`). Consistent with
every other edge in this repo, assume the live edge is much smaller than the
idealized backtest until proven otherwise on real data. **Paper trade first.**

Known limitations:
- Live order routing is simplified marketable-limit (no partial-fill requeue yet).
- Win-prob model is a first-order margin/time/spread approximation (no lineup,
  fouls, possession, or pace state).
- Generic (non-NBA) markets run price-only with no fair-value veto.
```
