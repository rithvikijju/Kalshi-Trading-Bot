# 2026-05-14 Predexon Research Ledger

Goal: use Predexon Kalshi historical orderbook snapshots to expand BTC15M and
BTC 1h research data, then test whether our current models and new structural
signals survive beyond the small live websocket capture.

## Ground Rules

- Do not install untrusted provider SDKs. Use plain REST with `requests`.
- Do not persist the Predexon API key in tracked files.
- Store raw research data under `data/predexon_kalshi_orderbooks/`; this path is
  ignored by git.
- Treat Predexon as `historical_snapshot_provider_time`, not live-replay
  truth. It is stronger than Kalshi candle history but weaker than our own
  receive-time websocket capture.
- Use only causal snapshot timestamps for decisions.
- Do not use candle high/low or any later quote inside the event.
- Include Kalshi taker fees in all PnL.
- Use official Kalshi settlement where available.
- Keep final promotion gated by our own live websocket capture.

## API Notes

- Endpoint: `GET /v2/kalshi/orderbooks`
- Required: `ticker`, `start_time`, `end_time`
- Time unit: Unix milliseconds
- Price unit: cents
- Page size: max `limit=200`
- Pagination: `pagination_key`
- Free historical endpoints do not count toward quota, but free plan has
  `1 req/sec`.
- Known Predexon Kalshi orderbook gap from docs:
  `2026-03-12 08:00 UTC` to `2026-03-14 18:10 UTC`.

## Research Plan

1. Smoke test API on one BTC15M market to learn snapshot density and storage.
2. Pull highest-value BTC15M late-window data first:
   - `KXBTC15M`
   - late 20 minutes around each event close
   - one market per event
   - recent-to-old priority
3. Pull KXBTCD hourly late-window data second:
   - `KXBTCD`
   - late 20 minutes
   - cap to top 8-12 markets per event unless storage/time remains cheap
4. Build compact top-of-book research tables from the Parquet parts.
5. Backtest current running BTC15M and BTC 1h models on the Predexon snapshot
   data using conservative fill assumptions.
6. Hypothesis loop:
   - generate candidate structural features,
   - train on older Predexon/historical data,
   - validate on later Predexon data,
   - only then compare with live websocket capture.

## Running Log

- Initialized ledger and safe downloader.
- Smoke test:
  - `KXBTCD-26APR0100-T58099.99`, late 20m, returned `169`
    snapshots in one request from `2026-04-01 03:40:00.090 UTC` to
    `2026-04-01 03:59:30.904 UTC`.
  - First BTC15M explicit ticker `KXBTC15M-26MAY122145-45` returned no
    snapshots for its late window, even though exact market lookup works.
  - Batch BTC15M probe over 20 local May 12/13 windows returned 3 nonempty
    markets and 17 empty markets. Nonempty windows had `29`, `94`, and `212`
    snapshots, concentrated in the final minutes before close.
- Implication: Predexon is useful for KXBTCD immediately and may have sparse
  BTC15M coverage. Allocate the first large pull mostly to KXBTCD center/near
  center strikes, with a smaller BTC15M probe.
- Downloader patch: when local KXBTCD metadata lacks volume/OI, rank markets
  near the event's median strike first instead of taking arbitrary ladder
  strikes.
- Correction: event median strike is not the same as near live BTC spot. The
  first corrected KXBTCD run still downloaded many 99c/1c books because the
  strike grid center was far from BTC. I stopped that run and generated
  `backtest_outputs/predexon_selection/kxbtd_near_spot_late20_20260301_20260506.csv`,
  ranking each event by distance between `floor_strike` and Coinbase BTC at
  `close_time - 10m`, then selecting the 12 nearest strikes.
- Restarted KXBTCD backfill from that near-spot selection in reverse
  chronological order so recent dense May/April windows arrive first. This is
  the right research pull for the current 1h model because it preserves adjacent
  strike structure around the actual live decision region.
- Replay bug caught: Predexon can encode a missing YES ask as
  `best_ask_cents = 0`. That is not a zero-cent executable ask. The replay now
  treats it as a synthetic 100c YES ask with zero visible YES-ask quantity while
  preserving the executable NO ask inferred from the YES bid.
- 2026-05-14 05:02 MDT: patched the downloader to persist scalar Predexon
  market metadata (`result`, open/close times, subtitles, volume/OI, etc.) in
  `data/predexon_kalshi_orderbooks/market_metadata/`. This is required for
  BTC15M, because quote snapshots alone are not enough to score settled
  up/down contracts.
- Restarted the BTC15M Predexon backfill with a 12-minute late window,
  reverse-chronological priority, 1 req/sec, and 1,200 recent market windows.
  The live trading/weather Python processes were left alone.


## Backtest Run
- Output: `backtest_outputs\predexon_research_smoke_20260514`
- Predexon snapshots tested: 21,543 rows, 12 events, 12 markets, 2026-03-11 15:40:00.633000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_partial_20260514_1`
- Predexon snapshots tested: 21,543 rows, 12 events, 12 markets, 2026-03-11 15:40:00.633000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_partial_20260514_2`
- Predexon snapshots tested: 96,898 rows, 88 events, 396 markets, 2026-03-11 15:40:00.633000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_partial_nearspot_20260514_2`
- Predexon snapshots tested: 127,224 rows, 48 events, 461 markets, 2026-05-04 00:40:00.257000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_partial_nearspot_20260514_3`
- Predexon snapshots tested: 128,712 rows, 24 events, 338 markets, 2026-05-05 00:40:00.308000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_partial_nearspot_20260514_4`
- Predexon snapshots tested: 166,490 rows, 24 events, 389 markets, 2026-05-05 00:40:00.308000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_partial_nearspot_20260514_5`
- Predexon snapshots tested: 281,188 rows, 48 events, 681 markets, 2026-05-04 00:40:00.257000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_1h_current_20260514_1`
- Predexon snapshots tested: 598,160 rows, 106 events, 1,142 markets, 2026-03-01 05:40:04.809000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_research_15m_partial_20260514_1`
- Predexon snapshots tested: 9,329 rows, 58 events, 58 markets, 2026-05-11 13:56:30.604000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_1`
- Candidate rows: 758; events: 43; source: `backtest_outputs\predexon_research_15m_partial_20260514_1\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## Backtest Run
- Output: `backtest_outputs\predexon_research_15m_partial_20260514_2`
- Predexon snapshots tested: 61,640 rows, 202 events, 202 markets, 2026-05-09 13:58:06.703000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_2`
- Candidate rows: 7,515; events: 164; source: `backtest_outputs\predexon_research_15m_partial_20260514_2\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.

## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_3`
- Candidate rows: wider May 7-12 Predexon slice, 274 all-event candidates
  after one-per-event selection for broad hypotheses.
- Strongest repeated rule: `cheap_no_rr` = buy NO when `NO ask <= 60c`,
  `RR >= 0.5`, `ttl 2-8m`, spread `0-4c`, visible top quantity >= 1,
  choose max RR once per event.
- Predexon result for `cheap_no_rr`: 187 trades, `+24.47` one-contract PnL
  all splits, 30.48% win rate, max drawdown `-1.31`; train `+13.78`,
  validation `+5.79`, test `+4.90`.
- Warning: event-split ML gates lost money (`hgb_structural_15m` test
  `-0.68`, `mlp_structural_15m` test `-2.55`). The edge is not coming from
  generic neural classification yet; it is a simple payout-skew / cheap-option
  structure.

## BTC15M Live Websocket Holdout Check
- Output: `backtest_outputs\predexon_structural_15m_live_capture_check_20260514`.
- Data: snapshot of `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb`;
  decisions use local `received_at_ns`, 5-second sample buckets, `ttl 2-8m`,
  spread `0-4c`, visible top quantity >= 1, official result fetched from Kalshi.
- `cheap_no_rr`: 134 trades, `+27.434` one-contract PnL, premium `21.566`,
  return on premium `127.21%`, win rate `36.57%`, max drawdown `-0.954`,
  trade Sharpe `6.27`, from `2026-05-12 10:52:59 UTC` to
  `2026-05-14 11:10:09 UTC`.
- `cheap_yes_rr`: 131 trades, `+15.827`, premium `24.173`, win rate `30.53%`,
  max drawdown `-1.571`, Sharpe `3.93`.
- `no_only_earliest`: 172 trades, `-1.119`, win rate `50.00%`, max drawdown
  `-5.745`; this confirms the profitable structure is not "always NO", it is
  specifically cheap high-risk/reward NO.
- Promotion note: this is the first genuinely interesting BTC15M structural
  edge from the Predexon loop, and it also survived our live websocket capture
  holdout. It still needs a forward shadow/live implementation with exact FOK
  reprice checks before any real deployment.

## Wider BTC15M Predexon Re-Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_4b`.
- Data at run time: Predexon BTC15M May 4-12 partial, 443 one-per-event
  candidate events in the candidate file.
- `cheap_both_sides_rr` (independent cheap YES and cheap NO tails, one per
  side per event): 587 trades all splits, `+65.20` one-contract PnL, premium
  `95.80`, 27.43% win, max drawdown not yet promoted as a live rule because it
  can take both sides in the same event.
- Split details for the same combined rule: train `+32.28`, validation
  `+16.14`, test `+16.78`; test max drawdown `-0.89`.
- `cheap_no_rr` alone: 297 trades, `+31.30`, train `+14.02`, validation
  `+7.14`, test `+10.14`, max drawdown `-1.37`.
- `cheap_yes_rr` alone: 290 trades, `+33.90`, train `+18.26`, validation
  `+9.00`, test `+6.64`, max drawdown `-1.47`.
- Interpretation: the strongest structure is not directional BTC momentum. It
  is late cheap-tail underpricing in BTC15M. Market often prices one side very
  cheap, but realized reversal frequency is high enough that both cheap YES and
  cheap NO tails are profitable after Kalshi taker fees in this snapshot data.
- Live websocket combined check from
  `backtest_outputs\predexon_structural_15m_live_capture_check_20260514`:
  cheap YES + cheap NO independent rows = 265 trades, `+43.261`, premium
  `45.739`, 33.58% win, using local receive timestamps from
  `2026-05-12 10:42 UTC` to `2026-05-14 11:25 UTC`.

## Latest Wider BTC15M Predexon Re-Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_5`.
- Data at run time: Predexon BTC15M May 3-12 partial; 213,300 snapshots
  across 646 events in the backtest report; 541 one-per-event candidate events
  in the structural candidate file.
- `cheap_both_sides_rr`: 723 trades, `+81.60` one-contract PnL, premium
  `108.90`, return on premium `74.93%`, win rate `27.66%`, max drawdown
  `-1.50`, trade Sharpe `8.68`.
- Split stability: train 429 trades `+43.74`; validation 147 trades `+16.90`;
  test 147 trades `+20.96`.
- `cheap_no_rr`: 361 trades, `+38.76`, max drawdown `-1.50`; train
  `+18.70`, validation `+9.37`, test `+10.69`.
- `cheap_yes_rr`: 362 trades, `+42.84`, max drawdown `-1.47`; train
  `+25.04`, validation `+7.53`, test `+10.27`.
- Baseline `earliest_any` remains negative (`-7.14`), and ML gates remain
  negative on test (`hgb -4.46`, `mlp -4.45`). This supports the specific
  cheap-tail hypothesis rather than a broad "trade anything late" or neural
  classifier result.


## Backtest Run
- Output: `backtest_outputs\predexon_research_15m_partial_20260514_3`
- Predexon snapshots tested: 104,967 rows, 334 events, 334 markets, 2026-05-07 16:58:39.255000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_3`
- Candidate rows: 12,848; events: 274; source: `backtest_outputs\predexon_research_15m_partial_20260514_3\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## Backtest Run
- Output: `backtest_outputs\predexon_research_15m_partial_20260514_4`
- Predexon snapshots tested: 168,756 rows, 534 events, 534 markets, 2026-05-04 20:08:55.523000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_4`
- Candidate rows: 18,854; events: 443; source: `backtest_outputs\predexon_research_15m_partial_20260514_4\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_4b`
- Candidate rows: 18,854; events: 443; source: `backtest_outputs\predexon_research_15m_partial_20260514_4\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## Backtest Run
- Output: `backtest_outputs\predexon_research_15m_partial_20260514_5`
- Predexon snapshots tested: 213,300 rows, 646 events, 646 markets, 2026-05-03 13:04:19.084000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_partial_20260514_5`
- Candidate rows: 23,432; events: 541; source: `backtest_outputs\predexon_research_15m_partial_20260514_5\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## BTC15M Live Websocket Holdout, Last 8 Hours
- Output: `backtest_outputs\btc15m_live_holdout_8h_20260514`.
- Script: `scripts/backtest_btc15m_live_holdout.py`.
- Data: local live websocket capture snapshot from
  `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb`.
- Window is anchored to the latest captured `ws_orderbook_top.received_at_utc`,
  not wall-clock: `2026-05-14 11:05:57 UTC` -> `2026-05-14 19:05:57 UTC`.
- Coverage: 687,833 top-of-book rows, 14,089 BTC websocket ticks, 33 BTC15M
  events in-window, 32 finalized result rows. Open/unsettled rows were excluded
  from PnL.
- The rules use local `received_at_ns` ordering and official Kalshi settlement
  fetched after the fact. They do not use candle high/low or future quotes for
  decisions.

```
strategy                trades  events  pnl      return/$100  premium  win%    maxDD   sharpe
cheap_yes_rr            18      18      +6.908   +6.91%       3.092    55.6%  -0.654  3.45
cheap_no_rr             26      26      +3.713   +3.71%       2.287    23.1%  -1.019  1.75
cheap_both_sides_rr     44      32      +10.621  +10.62%      5.379    36.4%  -0.552  3.55
current_lowdd_no_rv     11      11      -2.260   -2.26%       7.260    45.5%  -2.260 -1.20
```

Interpretation: the cheap-tail structure transferred cleanly from Predexon
snapshots to the most recent live websocket holdout. The currently deployed
lowdd momentum-style BTC15M rule was negative in the same window. The combined
cheap YES + cheap NO rule can take independent opposite-tail trades in the same
event; do not deploy that behavior without explicitly deciding how to handle
same-event exposure and live FOK fill constraints.


## Corrected Predexon Current-Strategy Comparison
- Output: `backtest_outputs\predexon_current_btc15m_20260514_2`.
- Structural comparison output:
  `backtest_outputs\predexon_structural_15m_current_compare_20260514_1`.
- Bug fixed before this run: `scripts/backtest_predexon_orderbooks.py`
  previously sorted Predexon quote rows without resetting index and also
  treated parquet `datetime64[us]` values as nanoseconds. That made 2m/3m
  market-mid lookbacks unavailable and falsely produced zero current-model
  trades.
- Corrected data: 317,378 usable Predexon top-of-book snapshots, 850 BTC15M
  events/markets, `2026-04-30 10:49:21 UTC` ->
  `2026-05-12 22:29:02 UTC`. This is provider-timestamped historical snapshot
  data, not local websocket receive-time replay and not a fully gapless calendar.

```
strategy                 trades  pnl_1c    premium  win%    maxDD   sharpe  first_entry              last_entry
current_btc15m_lowdd     61      -4.19     33.19    47.5%  -5.55   -1.22   2026-04-30 13:10 UTC  2026-05-11 22:10 UTC
current target-win size  61      -14.12    240.12   47.5%  -33.19  n/a     same                   same
cheap_yes_rr             486     +56.77    79.23    28.0%  -1.47   7.40    2026-04-30 10:56 UTC  2026-05-12 21:42 UTC
cheap_no_rr              501     +58.75    85.25    28.7%  -2.27   7.35    2026-04-30 10:52 UTC  2026-05-12 22:11 UTC
cheap_both_sides_rr      987     +115.52   164.48   28.4%  -2.27   10.43   2026-04-30 10:52 UTC  2026-05-12 22:11 UTC
```

Chronological split for `cheap_both_sides_rr` on the corrected Predexon
candidate file: train 592 trades `+66.56`, validation 196 trades `+20.71`,
test 199 trades `+28.25`. This does not eliminate overfit risk, but the
direction of evidence is now: Predexon broad snapshot data positive, later
live websocket holdout positive, current deployed lowdd negative on both the
recent live holdout and corrected Predexon comparison.


## Backtest Run
- Output: `backtest_outputs\predexon_current_btc15m_20260514_1`
- Predexon snapshots tested: 317,378 rows, 850 events, 850 markets, 2026-04-30 10:49:21.519000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_current_btc15m_20260514_2`
- Predexon snapshots tested: 317,378 rows, 850 events, 850 markets, 2026-04-30 10:49:21.519000+00:00 -> 2026-05-12 22:29:02.423000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## BTC15M Position-Aware Cheap-Tail Correction
- Outputs:
  - Predexon: `backtest_outputs\predexon_structural_15m_position_aware_20260514_1`.
  - Full live websocket: `backtest_outputs\btc15m_live_holdout_full_position_aware_20260514`.
  - May 13 MDT live websocket:
    `backtest_outputs\btc15m_live_holdout_may13_mdt_position_aware_20260514`.
  - May 13 UTC live websocket:
    `backtest_outputs\btc15m_live_holdout_may13_utc_position_aware_20260514`.
- Fix: removed the old misleading `cheap_both_sides_rr` interpretation. YES
  and NO on the same Kalshi market are complementary/offsetting, not two
  independent directional positions.
- Additional correction: deployable cheap-tail rows now use the first passing
  signal in event time, not the highest future RR quote within the event. The
  previous best-RR selection was optimistic for live trading.
- New strategy rows:
  - `cheap_yes_rr_first` / `cheap_no_rr_first`: first passing side-specific
    signal.
  - `cheap_tail_best_side_first`: first passing cheap-tail side, one side only.
  - `cheap_pair_lock_rr`: completed pair subset only; profitable by
    construction when YES+NO all-in cost is below $1, but not standalone
    deployable because it ignores first-leg events that never later lock.
  - `cheap_tail_position_aware`: deployable accounting for first cheap side,
    then later buys the opposite side only if it locks a profitable pair;
    otherwise it holds the first side to settlement.

Full live websocket replay, `2026-05-12 10:42:45 UTC` ->
`2026-05-14 19:45:00 UTC`, 4,240,274 top rows, 64,756 BTC ticks, 211 finalized
events:

```
strategy                    trades  pnl_1c   premium  ROP      win%    maxDD    sharpe
cheap_yes_rr_first          153     -5.420   58.420   -9.28%   34.6%   -11.092  -0.98
cheap_no_rr_first           163     -3.568   60.568   -5.89%   35.0%   -7.705   -0.63
cheap_tail_best_side_first  211     -2.188   56.188   -3.89%   25.6%   -12.719  -0.36
cheap_pair_lock_rr          98      +7.227   90.773   +7.96%   100.0%  0.000    8.27
cheap_tail_position_aware   211     -8.088   113.088  -7.15%   49.8%   -11.095  -2.48
current_lowdd_no_rv         85      -0.090   56.090   -0.16%   65.9%   -7.820   -0.02
```

Interpretation: the previously exciting cheap-tail result was mostly an
artifact of future-best quote selection and independent YES/NO accounting. The
completed lock subset is real in arithmetic terms, but the deployable
position-aware policy is negative on the full live capture. Do not deploy
cheap-tail as a first-leg strategy without a separate model that predicts which
first legs will later become profitable locks or otherwise controls unpaired
first-leg losses.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_current_compare_20260514_1`
- Candidate rows: 35,605; events: 731; source: `backtest_outputs\predexon_current_btc15m_20260514_2\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## BTC15M Structural Candidate Run
- Output: `backtest_outputs\predexon_structural_15m_position_aware_20260514_1`
- Candidate rows: 35,605; events: 731; source: `backtest_outputs\predexon_current_btc15m_20260514_2\btc15m_ml_candidates.parquet`.
- Best test rows are in `structural_hypotheses_summary.csv`; treat as exploratory until repeated on more Predexon and live websocket data.


## BTC15M Predexon vs Local WS Exact-Overlap Check
- Output: `backtest_outputs\btc15m_predexon_ws_overlap_20260514_181535`.
- Window requested: `2026-05-12 10:42:45 UTC` ->
  `2026-05-12 22:29:02.423 UTC`.
- Exact common market overlap was small: 11 BTC15M events/markets. Predexon had
  779 usable common snapshots; local WS had 31,287 dense rows in the same
  per-market intervals. Local WS sampled at Predexon timestamps matched 755 of
  those snapshots within a 10s as-of tolerance.
- Execution-equal comparison means Predexon snapshots and local WS as-of rows
  were scored at the same decision timestamps with the same one-contract,
  top-of-book, taker-fee model. No candle high/low, no future-best quote, and
  no future-best RR selection were used. The current lowdd model also required
  non-stale 2m/3m market and 3m BTC lookbacks so sparse snapshots could not
  fabricate momentum from old quotes.
- Quote similarity was generally close: median local quote age 0.055s; 88.9%
  matched within 1s and 96.6% within 5s. Median absolute bid/ask differences
  were 0.3c/0.4c; p95 differences were 5.35c/5.70c.

Execution-equal strategy results:

```
source                  strategy                    trades  pnl_1c  win%   maxDD
predexon                current_live_lowdd_rr033    0       +0.000  0.0%   0.000
local_asof_pred_times   current_live_lowdd_rr033    0       +0.000  0.0%   0.000
predexon                cheap_tail_best_side_first  6       +0.080  16.7% -0.380
local_asof_pred_times   cheap_tail_best_side_first  6       +0.068  16.7% -0.380
predexon                cheap_tail_position_aware   6       +0.080  16.7% -0.380
local_asof_pred_times   cheap_tail_position_aware   6       +0.068  16.7% -0.380
predexon                liquid_low_spread_rr        6       +0.100  16.7% -0.390
local_asof_pred_times   liquid_low_spread_rr        5       +0.276  20.0% -0.220
```

Trade matching was strong on the execution-equal grid: `cheap_tail_best_side`
and `cheap_tail_position_aware` took the same 6 event/market trades on both
sources with the same side; PnL differed by only `-0.012` one-contract dollars
because local quotes were a few tenths of a cent different. `liquid_low_spread`
had one Predexon-only trade because the local as-of quote failed that stricter
spread/liquidity gate.

Interpretation: Predexon top-of-book prices are close enough to our local WS
for broad research when an overlap exists, but its snapshot sparsity is real.
It should still be treated as a research/backfill source, with our live WS
capture remaining the promotion gate.


## BTC15M April Predexon Execution-Style Backtest
- Output: `backtest_outputs\predexon_btc15m_april_execution_20260514_182852`.
- Script: `scripts\backtest_btc15m_predexon_april_execution.py`.
- Scope: all available Predexon BTC15M April snapshots from
  `2026-04-01 00:00 UTC` to `2026-05-01 00:00 UTC`.
- Prepared data: 2,169,130 top snapshots with joined level-0 visible size.
  After executable-book sanity filtering (`spread <= 20c`) and official
  settlement join: 838,311 feature snapshots across 2,567 events; 832,434 rows
  passed the sparse-snapshot quality gate.
- Execution assumptions: first qualifying trade per event, one taker contract
  at visible top-of-book for normalized comparison, Kalshi fees included, no
  candle high/low, no future-best RR/quote selection. The target-win sizing
  column uses the BTC15M live-style target `$3` win / max `$15` premium sizing,
  and the bankroll column simulates starting with `$100` and reducing size as
  bankroll falls.

Main result: the liquid cheap-tail idea did not survive broad April validation.

```
strategy                    trades  pnl_1c   win%    maxDD_1c  sized pnl  bankroll100 end
current_live_lowdd_rr033    177     -17.21   43.5%   -18.68    -144.83    $1.77
new_liquid_rr_first         1503    -35.25   20.8%   -38.90    -146.52    $0.13
new_liquid_rr_first_qty250  1463    -40.19   20.2%   -43.46    -173.70    $0.12
```

Interpretation: on April Predexon, the current lowdd strategy is bad and the
new liquid RR strategy is also bad. The new strategy loses less per trade than
current, but it fires far more often, so the aggregate and bankroll-limited
result is unacceptable. Do not promote the liquid cheap-tail strategy from the
small May overlap result without a stronger directional filter.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_apr1_14_stride30_20260515`
- Predexon snapshots tested: 852,418 rows, 307 events, 2,410 markets, 2026-04-01 00:35:00.045000+00:00 -> 2026-04-14 23:59:53.810000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_apr15_30_stride30_20260515`
- Predexon snapshots tested: 999,896 rows, 315 events, 2,177 markets, 2026-04-15 02:36:25.923000+00:00 -> 2026-04-30 23:59:57.280000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_feb10_mar31_stride30_highconf_20260515`
- Predexon snapshots tested: 10,939,656 rows, 826 events, 3,732 markets, 2026-02-10 11:35:00.150000+00:00 -> 2026-03-31 23:59:59.947000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_may1_06_stride30_highconf_20260515_222544`
- Predexon snapshots tested: 566,226 rows, 65 events, 1,100 markets, 2026-05-03 07:40:01.302000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_may1_06_stride30_highconf_visible_20260515_233413`
- Predexon snapshots tested: 566,226 rows, 65 events, 1,100 markets, 2026-05-03 07:40:01.302000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_apr1_14_stride30_highconf_visible_20260515_234410`
- Predexon snapshots tested: 852,418 rows, 307 events, 2,410 markets, 2026-04-01 00:35:00.045000+00:00 -> 2026-04-14 23:59:53.810000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_apr15_30_stride30_highconf_visible_20260515_234410`
- Predexon snapshots tested: 999,896 rows, 315 events, 2,177 markets, 2026-04-15 02:36:25.923000+00:00 -> 2026-04-30 23:59:57.280000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260210_20260217_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 4,926,470 rows, 130 events, 723 markets, 2026-02-10 00:35:00.001000+00:00 -> 2026-02-16 23:59:58.889000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260217_20260224_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 3,565,014 rows, 132 events, 681 markets, 2026-02-17 01:35:00.134000+00:00 -> 2026-02-23 23:59:51.452000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260224_20260303_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 1,044,582 rows, 124 events, 648 markets, 2026-02-24 00:35:03.635000+00:00 -> 2026-03-02 23:59:48.749000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260303_20260310_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 572,176 rows, 110 events, 221 markets, 2026-03-03 00:35:07.069000+00:00 -> 2026-03-09 23:45:34.600000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260310_20260317_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 414,676 rows, 61 events, 124 markets, 2026-03-10 00:35:02.756000+00:00 -> 2026-03-16 23:59:58.544000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260317_20260324_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 182,611 rows, 102 events, 333 markets, 2026-03-17 01:35:08.309000+00:00 -> 2026-03-23 23:59:58.855000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_20260324_20260401_stride30_highconf_visible_20260516_003528`
- Predexon snapshots tested: 588,247 rows, 178 events, 1,055 markets, 2026-03-24 00:35:11.071000+00:00 -> 2026-03-31 23:59:59.947000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_entry59_70_smoke_20260516_022554`
- Predexon snapshots tested: 588,247 rows, 178 events, 1,055 markets, 2026-03-24 00:35:11.071000+00:00 -> 2026-03-31 23:59:59.947000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_entry59_70_direct_apr08_apr15_20260516_024401`
- Predexon snapshots tested: 374,967 rows, 152 events, 1,182 markets, 2026-04-08 00:35:01.721000+00:00 -> 2026-04-14 23:59:53.810000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_entry59_70_direct_apr01_apr08_20260516_024401`
- Predexon snapshots tested: 477,451 rows, 155 events, 1,228 markets, 2026-04-01 00:35:00.045000+00:00 -> 2026-04-07 23:59:52.775000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_entry59_70_direct_may03_may06_20260516_024401`
- Predexon snapshots tested: 566,226 rows, 65 events, 1,100 markets, 2026-05-03 07:40:01.302000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_entry59_70_direct_mar24_apr01_20260516_024401`
- Predexon snapshots tested: 588,247 rows, 178 events, 1,055 markets, 2026-03-24 00:35:11.071000+00:00 -> 2026-03-31 23:59:59.947000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_apr08_apr15_20260516_0320`
- Predexon snapshots tested: 374,967 rows, 152 events, 1,182 markets, 2026-04-08 00:35:01.721000+00:00 -> 2026-04-14 23:59:53.810000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_apr01_apr08_20260516_0320`
- Predexon snapshots tested: 477,451 rows, 155 events, 1,228 markets, 2026-04-01 00:35:00.045000+00:00 -> 2026-04-07 23:59:52.775000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_may03_may06_20260516_0320`
- Predexon snapshots tested: 566,226 rows, 65 events, 1,100 markets, 2026-05-03 07:40:01.302000+00:00 -> 2026-05-05 23:59:59.912000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_mar24_apr01_20260516_0320`
- Predexon snapshots tested: 588,247 rows, 178 events, 1,055 markets, 2026-03-24 00:35:11.071000+00:00 -> 2026-03-31 23:59:59.947000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_apr15_apr23_20260516_0340`
- Predexon snapshots tested: 462,166 rows, 172 events, 1,249 markets, 2026-04-15 02:36:25.923000+00:00 -> 2026-04-22 23:59:10.103000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_apr23_may01_20260516_0340`
- Predexon snapshots tested: 537,730 rows, 143 events, 928 markets, 2026-04-23 00:35:06.824000+00:00 -> 2026-04-30 23:59:57.280000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_mar16_mar24_20260516_0355`
- Predexon snapshots tested: 194,447 rows, 116 events, 359 markets, 2026-03-16 06:35:05.001000+00:00 -> 2026-03-23 23:59:58.855000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_mar05_mar16_20260516_0355`
- Predexon snapshots tested: 912,761 rows, 121 events, 244 markets, 2026-03-05 00:53:39.105000+00:00 -> 2026-03-15 23:59:58.833000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_feb21_mar05_20260516_0355`
- Predexon snapshots tested: 1,595,852 rows, 214 events, 976 markets, 2026-02-21 00:35:00.404000+00:00 -> 2026-03-04 23:59:54.457000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_feb13_feb17_20260516_0420`
- Predexon snapshots tested: 2,463,528 rows, 79 events, 462 markets, 2026-02-13 00:35:00+00:00 -> 2026-02-16 23:59:58.889000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_feb09_feb13_20260516_0420`
- Predexon snapshots tested: 2,977,361 rows, 59 events, 296 markets, 2026-02-09 15:35:00.328000+00:00 -> 2026-02-12 23:59:59.541000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_feb17_feb19_20260516_0425`
- Predexon snapshots tested: 1,136,383 rows, 37 events, 189 markets, 2026-02-17 01:35:00.134000+00:00 -> 2026-02-18 23:59:59.054000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.


## Backtest Run
- Output: `backtest_outputs\predexon_btc1h_highconf_direct_feb19_feb21_20260516_0425`
- Predexon snapshots tested: 1,939,616 rows, 41 events, 239 markets, 2026-02-19 00:35:01.306000+00:00 -> 2026-02-20 23:59:58.617000+00:00.
- Script: `scripts/backtest_predexon_orderbooks.py`.
