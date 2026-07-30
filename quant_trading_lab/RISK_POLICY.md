# RISK_POLICY

The rules the framework enforces in both backtest and live mode.
Encoded in `src/backtest/engine.py:RiskLimits` and `src/live/risk_monitor.py:RiskState`.

## 1. Hard live-trading locks
Live execution requires **BOTH** of these env vars to be exactly the string `true`:
```
LIVE_TRADING_ENABLED=true
I_UNDERSTAND_REAL_MONEY_RISK=true
```
Either missing or set to anything else → live brokers refuse to instantiate or place orders. See `src/config.py:SAFETY` and `src/live/alpaca_broker.py:__init__`.

## 2. Portfolio-level drawdown ladder
| Trigger | Action |
|---|---|
| -3% from peak | warn (logged + alert) |
| -5% from peak | leverage halved for all subsequent orders until new HWM |
| -8% from peak | new trades blocked entirely (`halted = True`) |
| daily PnL ≤ -5% | new trades blocked, alert at critical severity |

All values configurable via `RiskLimits`. The engine writes a `risk_events` row for every transition.

## 3. Position-level caps
| Limit | Default |
|---|---|
| max single-asset weight | 50% of equity |
| max gross leverage | 2.0× |
| max strategy allocation (combined portfolio) | 60% |
| max venue exposure (crypto sleeve) | 30% per exchange |
| max funding-arb notional | 25% per pair |

Caps applied at order-submission time (live) and at weight-construction time (backtest).

## 4. Data-quality gates
The risk monitor blocks orders when:
- **Stale data:** last bar is older than `stale_data_max_age_sec` (default 15 min).
- **Missing prices:** any symbol in the target weights has no current price.
- **Suspicious prices:** zero or negative price (catches API bugs).
- **Broker API failure:** any exception during `account()`, `positions()`, or `last_price()` causes a halt for that tick.

## 5. Funding-arb specific
- **Unwind on persistent negative funding:** 6 consecutive negative funding prints (≈48 hours on 8h-cadence venues) triggers unwind.
- **Unwind on basis blow-out:** |(perp - spot) / spot| × 10_000 > 50 bps (configurable) triggers unwind.
- **Counterparty kill switch:** manual flag in `src/live/alerts.py` lets you globally halt a venue when an exchange flag goes up (FTX-style risk).

## 6. Event-time risk
- **Do not initiate new positions in the 30 minutes before a scheduled FOMC, CPI, or NFP release** (data from `src/data/fred_client.py`). Existing positions held; rebalance suspended.
- **Crypto: pause new funding-arb entries during BTC daily moves > 5%.** Existing carries held to natural unwind.

## 7. Capital concentration
| Tier | % of total trading capital |
|---|---|
| Paper / research | unbounded |
| New live sleeve, first 30 days | 5% max |
| Validated sleeve, 30–90 days | 25% max |
| Validated sleeve, 90+ days | up to per-strategy cap from `RiskLimits` |

A "validated sleeve" means paper-trading PnL has tracked the backtest projection within ±1 σ for 30+ continuous days. Recorded in `strategy_runs.notes`.

## 8. Manual kill switches
- Set `state.halted = True` in any notebook to immediately halt that run.
- Delete `data/lab.sqlite` to wipe local state (does NOT cancel live broker orders — use the broker's panel for that).
- Revoke `LIVE_TRADING_ENABLED` to refuse all new live orders (existing positions unaffected).

## 9. What is not covered
- **Black-swan event risk** beyond the drawdown ladder.
- **Counterparty insolvency** beyond a manual kill switch.
- **Tax accounting** — out of scope.
- **Regulatory compliance** for prediction-market trading — out of scope; consult a lawyer before adding it.

## 10. Review cadence
Re-read this document and the engine's `RiskLimits` constants:
- Before sizing up.
- After any drawdown event.
- After any change to strategy logic.
- Monthly, regardless.
