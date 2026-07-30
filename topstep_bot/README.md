# TopStep ICT Bot

An ICT / market-microstructure trading bot for **TopStep futures** (index: MES/MNQ/ES/NQ,
crypto: MBT/MET), wired to the official **ProjectX / TopstepX API**, controlled from
**Discord**, with **AI position sizing** (a fast confidence model that scales a deterministic
risk cap). Built to scalp short-term swings — liquidity-sweep reversals and BOS continuations
— never overnight holds.

> **Honest framing (read this first).** ICT concepts (FVGs, order blocks, liquidity sweeps,
> killzones) are popular but **not independently proven** edges, and prior research in this
> repo (`../topstep_strategy/STRATEGY.md`) found no directional edge in the crypto data and
> identified order-flow microstructure on liquid index futures as the real path. So this is
> built as a **framework that proves itself**: every setup is logged with its features, the
> risk engine caps downside to the eval fee, and the confidence model starts as a transparent
> **prior** (not discovered alpha) until enough real outcomes are captured to train and
> validate it. Treat all results as **paper** until the sample is large and out-of-sample.
> Don't trade real size on the cold-start prior.

## Why this design

You chose: ProjectX execution · build live **and** backtest in parallel · **no purchased
history** (capture the live tape going forward) · AI sizing = fast ML confidence × hard risk
cap. Consequences:

- The first deployable artifact is a **capture + paper-trade loop**. It runs on the live
  feed in `sim` mode, generates its own training/backtest data, and risks nothing.
- The **backtester replays that captured tape** through the *same* signal + risk code.
- Promotion to `live` is a deliberate, separate restart — never a hidden toggle.

## Layout

```
config.py              instruments, account/risk params, env-driven secrets
broker/  base.py        Broker interface + Quote/Bar/Order/Position/Fill contracts
         projectx.py    LIVE: ProjectX REST (auth/orders/history) + SignalR hubs, OCO brackets
         sim.py         PAPER: simulates fills against the live feed (same interface)
data/    bars.py        tick/quote → 1-min bar aggregation (+ retrieveBars warmup)
         capture.py     DuckDB: bars + setups + trades; builds the ML training set
signals/ structure.py   swings, BOS/CHoCH, displacement
         fvg.py / order_blocks.py / liquidity.py / sessions.py   ICT primitives
         engine.py      fuses primitives → Setup (reversal | continuation) with features
risk/    engine.py      wraps ../topstep_strategy/risk_engine.py for live intraday use
         sizing.py      deterministic cap × confidence scale (cap can only shrink)
ml/      features.py    canonical feature vector
         confidence.py  fast model: cold-start prior → trained sklearn logistic
bot/     trader.py      the live loop: feed→signal→risk→size→execute→manage
         discord_bot.py control + alerts
run.py                 entrypoint        backtest.py   replay the captured tape
```

## The decision flow (per closed 1-min bar)

1. **Signal engine** detects a setup:
   - *Reversal* — a liquidity sweep (stop run) + rejection, confirmed by CHoCH/displacement;
     stop beyond the swept wick, target the next opposing liquidity pool. ("reverts to a point")
   - *Continuation* — established trend + BOS, retrace into an unmitigated FVG/order block;
     stop beyond the zone, target the next draw on liquidity. ("momentum / bull-bear switch")
2. **Gates:** minimum R:R, killzone window, flat in that instrument, risk-engine `can_enter`.
3. **Confidence** (ML) → 0–1 setup quality.
4. **Sizing:** risk engine sets the HARD contract cap (full stop ≈ per-trade risk, bounded by
   distance to the trailing-DD floor and account max); confidence scales it **down** only.
5. **Execute** a bracketed order (server-side OCO stop+target in live, simulated in sim).
6. **Manage:** mark every quote against the trailing-DD floor (flatten-all on breach), and
   flatten before the cash close.
7. **Learn:** every closed trade is labelled win/loss into DuckDB; the model retrains every
   25 trades once ≥200 labelled samples exist.

## Risk first (this is the actual edge)

Most funded accounts fail on the **trailing drawdown / daily loss**, not on signal. The risk
engine (`../topstep_strategy/risk_engine.py`, reused not rewritten) models TopStep's EOD
trailing DD, daily-loss lock, max contracts, loss-streak halt and give-back lock. The bot adds
**real-time intraday equity** checks so it flattens the instant equity touches the floor. The
funded-account math (capped downside = eval fee, large upside) is itself the positive-EV
mechanism — *only if you never blow the account*.

## Setup

```bash
pip install -r topstep_bot/requirements.txt

export TOPSTEP_USERNAME=...        # TopstepX username
export TOPSTEP_API_KEY=...         # from the TopstepX dashboard (enable API access)
export TOPSTEP_ACCOUNT_NAME=...    # optional; else first active account
export DISCORD_BOT_TOKEN=...       # optional (omit → console alerts)
export DISCORD_CHANNEL_ID=...      # channel for alerts
```

## Run

```bash
# paper-trade the live feed (default, zero account risk) — start here, leave it running
python -m topstep_bot.run --mode sim

# console-only (no Discord)
python -m topstep_bot.run --mode sim --no-discord

# require manual Discord approval before every entry
python -m topstep_bot.run --mode sim --manual-approve

# backtest the captured tape (run after the bot has collected bars)
python -m topstep_bot.backtest --instrument MNQ

# LIVE — real orders. Only after sim + backtest validate an edge.
python -m topstep_bot.run --mode live --account 50K --per-trade-risk 200
```

### Discord commands
`!status` `!risk` `!positions` `!pause` `!resume` `!flatten` `!approve [INST]` `!mode`

## Validation path (do this before live size)

1. Run `--mode sim` across several full sessions to capture a tape + paper trades.
2. `backtest.py` for honest small-sample stats (win rate, net/trade after fees). It refuses
   to over-claim below 30 trades.
3. Once ≥200 labelled trades exist, the prior is replaced by a trained, calibrated model —
   re-check OOS that confidence actually ranks winners.
4. Only then consider `--mode live`, starting at 1 micro and the smallest account.

## Known caveats / TODO (do not skip before live)

- **Live SignalR payload shapes & OCO bracket behaviour are coded to the documented ProjectX
  schema but unverified without a live account.** Confirm `GatewayQuote/Trade/UserTrade/
  UserPosition` field names and that the stop/target siblings cancel correctly before real size.
- Sim assumes limit/bracket fills at the touch (optimistic) and 1-tick market slippage — real
  fills will be worse; keep R:R and confidence floors conservative.
- The cold-start confidence weights are a **prior**, not validated alpha.
- `MET` tick value is a fallback guess; the live `Contract/search` metadata overrides it.
- No earnings/news/halt awareness yet; killzone gating is the only time filter.
```
