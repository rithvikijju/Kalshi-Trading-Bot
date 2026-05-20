# BTC15M F2 TTL 10-12 Forward Holdout Freeze

Created UTC: 2026-05-16 03:10

## Frozen Candidate

Name: `btc15m_f2_ttl10_12_entry55_q50`

This candidate is frozen for forward validation. Do not tune these thresholds
against future websocket data.

Rules:

- Series: `KXBTC15M`.
- Event identity: trade only the subscribed 15-minute event, no adjacent-event
  bleed.
- Decision state: causal websocket orderbook and causal BTC spot only.
- Fair value: existing H02/lognormal fair-value model.
- Side fair probability: `>= 0.60`.
- Net fair edge after one-contract Kalshi taker fee: `>= 12c`.
- TTL: `10.0 <= ttl_min <= 12.0`.
- Spread: `<= 2c`.
- Entry: `0.02 <= entry_price <= 0.55`.
- Visible top ask quantity on traded side: `>= 50`.
- Sizing for validation: one contract.
- Execution model: FOK at current top-of-book, reject if reprice worsens by
  more than `2c`.
- Position rule: first qualifying decision per event.
- Settlement: official Kalshi result when available; proxy settlement must be
  reported separately.

## Why It Exists

Broad F2 did not transfer from April Predexon to May websocket replay:

- Apr 1-14 Predexon, exact F2 +2c stress: 96 trades, `+$9.48`.
- Apr 15-30 Predexon, exact F2 +2c stress: 183 trades, `+$2.54`.
- May websocket refresh, exact F2 +2c stress: 101 trades, `-$9.46`.

The TTL/execution-quality subset was the only fixed coarse filter that survived
all three checked samples:

| Sample | Trades | PnL | Win Rate | Max DD |
|---|---:|---:|---:|---:|
| Apr 1-14 Predexon | 16 | `+$6.02` | 87.5% | `-$0.53` |
| Apr 15-30 Predexon | 25 | `+$3.24` | 64.0% | `-$2.10` |
| May websocket refresh proxy | 16 | `+$3.65` | 75.0% | `-$0.59` |
| May websocket refresh official subset | 3 | `+$1.38` | 100.0% | `$0.00` |

These numbers are not enough for deployment. The candidate is now frozen so
future data can test it without more parameter search.

## Forward Shadow

Dry-run script:

```powershell
python -u scripts\btc15m_f2_ttl10_12_shadow.py
```

Current run started as PID `4196`.

Primary logs:

- `logs\btc15m_f2_ttl10_12_shadow_20260515_210157.out.log`
- `logs\btc15m_lowdd_live_20260515_210157.log`

Data:

- Capture DB:
  `C:\Users\ahmed\.btc_kalshi_bot\btc15m_f2_ttl10_12_shadow_capture.duckdb`
- Trade DB:
  `C:\Users\ahmed\.btc_kalshi_bot\btc15m_f2_ttl10_12_shadow_trades.db`

It is `mode=dry-run`, so it cannot submit orders.

## Promotion Gate

Promote only if all are true:

- Future websocket shadow has at least 30 closed official-result trades, or a
  larger proxy set whose official subset agrees directionally.
- Official-result PnL is positive after fees and a `+2c` adverse-fill stress.
- Max drawdown is small enough that one-contract live risk would not materially
  damage the account.
- No single day or single volatility regime explains all positive PnL.
- Live decision rows match the intended rule exactly: TTL `10-12`, entry
  `<=55c`, visible qty `>=50`, side probability `>=60%`, net edge `>=12c`.
- No further threshold tuning was done on the forward holdout.

## Anti-Overfit Rule

Future websocket rows from this shadow are now a promotion gate, not a tuning
set. If the rule fails, kill it or move research back to a separate training
dataset. Do not modify the TTL, entry, quantity, or edge thresholds by looking
at this shadow's PnL.
