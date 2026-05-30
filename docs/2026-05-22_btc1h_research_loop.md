# BTC1H Research Loop

Started: `2026-05-22T03:10Z`

This pivots active research to BTC1H while BTC15M continues as passive shadow
evidence collection. Nothing here authorizes live deployment.

## Current Verdict

- BTC15M remains weak on official-settled forward evidence, so stop spending
  active research cycles on new 15m threshold variants unless the running
  shadows accumulate surprising official rows.
- BTC1H is more promising historically, but it is still not deployable.
- The active BTC1H shadow is
  `btc1h_high_conf80_entry70_no_chase_shadow`.
- Latest remote official-settled BTC1H sample:
  - `11` official rows;
  - official PnL `+$0.50`;
  - proxy PnL `+$1.50`;
  - official-minus-proxy PnL `-$1.00`;
  - win rate `72.73%`;
  - trade Sharpe `0.3155`;
  - max drawdown `-$1.76`;
  - `1` proxy/official result mismatch.
- Remote worker verification at `2026-05-22T03:28:02Z`: `ALL_RUNNING`.
  BTC1H `btc1h_high_conf80_entry70_no_chase_shadow` PID `5132` was alive;
  BTC15M capture and all three BTC15M shadows were also alive.
- Remote worker verification at `2026-05-22T04:11:06Z`: still
  `ALL_RUNNING`; BTC1H PID `5132` remained alive.

## Best Current BTC1H Candidate

`high_conf_80_entry70_no_chase`

- Fixed multi-holdout artifact:
  `backtest_outputs\btc1h_multi_holdout_research_latest_codex`.
- Fixed holdout result:
  - `14 / 14` positive historical/proxy/live-WS holdout buckets under `+2c`
    adverse historical stress;
  - `6 / 6` May live-websocket cadences positive;
  - `11` forward REST-official rows with official PnL `+$0.50`, win
    `72.73%`, max DD `-$1.76`, and official/proxy mismatch rate `9.09%`.
- Historical/Predexon +2c stress:
  - `30` trades;
  - PnL `+$4.26`;
  - win `80.00%`;
  - max DD `-$1.41`;
  - breakeven-null p `0.0428`.
- Direct aggregate +2c stress:
  - `32` trades;
  - PnL `+$4.78`;
  - win `81.25%`;
  - max DD `-$1.41`;
  - breakeven-null p `0.0479`.
- May websocket cadence +2c stress:
  - all six tested cadences positive;
  - worst cadence PnL `+$0.75`.
- Blockers:
  - only `11` official-settled remote rows;
  - official/proxy mismatch rate is `9.09%`, above the `2%` gate;
  - settlement basis has already reduced PnL by `-$1.00`;
  - actual-shadow snapshot parity now passes for the `11` filled rows, but the
    full all-window counterfactual replay path is not yet promotion-usable;
  - current active DuckDBs still require a fresh pause/snapshot for any rows
    after the May 21 snapshot.

## Fixed Multi-Holdout Ranking

This ranking compares already-frozen variants. It is not a threshold search and
does not authorize deployment.

| Variant | Status | Holdout Result | Forward Official Evidence | Verdict |
| --- | --- | --- | --- | --- |
| `high_conf_80_entry70_no_chase` | active forward candidate | `14 / 14` positive; `6 / 6` WS cadences positive | `11` rows, `+$0.50`, mismatch `9.09%` | keep collecting; promising but blocked by official/fidelity gates |
| `high_conf_80_entry59_70_no_chase` | historical-only promising | `12 / 13` positive; `6 / 6` WS cadences positive | none | do not run/promote without a causal replay and new shadow decision |
| `high_conf_80` | historical-only promising | `8 / 9` positive; `6 / 6` WS cadences positive | none | fallback research idea only |
| `high_conf_80_no_chase` | watch/reject | `13 / 14` positive; failed `stride1s` WS bucket | none | do not promote; expensive entries are unstable |

## Forward Snapshot Parity

Artifacts:

- `backtest_outputs\btc1h_snapshot_decision_log_parity_latest_codex`;
- `backtest_outputs\btc1h_snapshot_replay_coverage_latest_codex`;
- `backtest_outputs\btc1h_forward_snapshot_signal_audit_latest_codex`.

Using the paused remote snapshot
`runtime\remote_snapshots\snapshot_20260521_145951\btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb`:

- capture rows: `2,273,693` `ws_orderbook_top`, `480,791`
  `signal_scan`, `13` `order_decision`;
- replay coverage gate: `11 / 11` official BTC1H rows replayable from readable
  top-of-book and signal-scan capture;
- decision-log parity: `11 / 11` paper-fill decisions matched official ledger
  rows, with zero missing or extra fill rows;
- signal chain audit: `13` selected signals matched `13` decisions; `11` were
  `paper_fill` and `2` were skipped by websocket reprice;
- forward daily official holdouts:
  - `2026-05-19`: `4` rows, official PnL `+$0.22`;
  - `2026-05-20`: `4` rows, official PnL `-$0.63`, the only
    proxy/official mismatch day;
  - `2026-05-21`: `3` rows, official PnL `+$0.91`.

This proves the gathered snapshot data and paper ledger agree for actual
shadow decisions. It does not prove an all-window counterfactual replay for
new BTC1H variants. A full signal-scan-clock replay over the snapshot was
attempted and stopped after it remained running for more than ten minutes; the
replay path needs optimization before it can serve as broad promotion evidence.

## Selected Signal Model Parity

Artifact:

- `backtest_outputs\btc1h_selected_signal_model_parity_latest_codex`.

This stricter audit recomputes the active live strategy at the `13` captured
`selected` signal rows using the selected market's as-of websocket top book and
an as-of BTC candle cache. The top-book side/entry prices matched the selected
signal rows. Exact model parity using scan-time TTL did not pass:

- selected rows: `13`;
- parity pass rows: `0`;
- max model probability absolute drift: `0.032348`;
- max net-edge absolute drift: `3.234823c`;
- four selected rows no longer recomputed as signals.

The first attempt used the local BTC cache and failed more severely because the
local cache ended at `2026-05-19T00:21:00Z`. Pulling the remote BTC cache reduced
but did not remove the drift. A follow-up implied-TTL reconciliation then showed
the logged probabilities are consistent with the live loop's cached event TTL:

- implied TTL pass rows: `13 / 13`;
- max implied-TTL probability residual: `0.000239`;
- implied TTL offset range: `-178` to `+162` seconds versus scan-time TTL.
- official impact split:
  - `9` official fills still reproduce as scan-time-TTL signals, official PnL
    `+$0.93`;
  - `2` official fills are cached-TTL-only under this diagnostic, official PnL
    `-$0.43`.

The likely blocker is not bad top-of-book capture; it is that the paused capture
snapshot did not include the exact cached `ttl_min` value from the live event
object. Therefore:

- actual decision/ledger parity still passes for filled rows;
- independent model replay from captured books is diagnosable but not yet
  promotion-usable without inferring TTL after the fact;
- the current `+$0.50` official forward PnL is not clean evidence for the exact
  scan-time-TTL policy, even though the drift-sensitive rows were net negative;
- future BTC1H capture needs exact scan-time model inputs.

Code has been updated for future runs so `signal_scan` can include the selected
signal's threshold, spread, visible quantity, quote timestamp/age, BTC candle
time, BTC candle age, RV60, 10-minute BTC return, selected TTL, and close time.
The live BTC1H signal path has also been patched to compute model TTL from
`close_time - scan_start_time` instead of the event object's cached refresh-time
TTL. The running remote shadow has not been restarted, so this only affects
future explicitly authorized starts/restarts. Rows collected before such a
restart remain cached-TTL-policy rows; rows collected after a restart should
start a separate scan-time-TTL evidence clock.

Latest local refresh note:

- A full read-only evidence-stack refresh at `2026-05-22T12:28:51Z` completed
  all `50` then-wired steps, with deployment readiness returning the expected
  no-deploy exit `1`.
- That full refresh did not regenerate
  `btc1h_clean_evidence_clock_gate_latest_codex`, so the refresh controller was
  patched to run the clean-clock gate before the BTC1H next-forward packet,
  deployment readiness, and promotion-gap matrix.
- Bounded refresh at `2026-05-22T12:46:12Z` now runs `51` planned steps and
  includes `btc1h_clean_evidence_clock_gate` as step `32`.
- The refreshed clean-clock gate remains blocked:
  - `clean_evidence_clock_ready = False`;
  - `blocker_count = 9`;
  - `status_source = status_json`;
  - latest pulled remote BTC1H status was
    `2026-05-22T08:01:20.103367Z`, about `284.9` minutes old at audit time;
  - `official_rows = 11`, `official_pnl = +$0.50`,
    `official_proxy_mismatches = 1`;
  - `blank_policy_official_rows = 11`;
  - old sidecar schema is still missing the scan-time model-input and policy
    identity fields.
- The forward evidence report now writes
  `backtest_outputs\btc_forward_evidence_report_latest_codex\btc1h_remote_provenance.csv`
  to keep local status and pulled remote official evidence separate:
  - `provenance_verdict = REMOTE_STATUS_STALE_OR_UNAVAILABLE`;
  - local BTC1H shadow status: `running = False`;
  - remote official rows: `11`, official PnL `+$0.50`;
  - remote proxy/official mismatches: `1`;
  - remote status age at report time: about `289.4` minutes.
- The forward consistency audit now carries that provenance into
  `backtest_outputs\btc_forward_consistency_audit_latest_codex\forward_consistency_summary.csv`:
  - BTC1H `agreement_status =
    btc1h_remote_status_stale_or_unavailable`;
  - `btc1h_remote_official_rows = 11`;
  - `btc1h_remote_official_pnl = +$0.50`;
  - `btc1h_clean_clock_status = BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - BTC1H blockers include stale remote status, clean-clock-not-ready,
    remote proxy/official mismatch, and too few remote official rows.
- The GPT Pro action-status artifact now repeats this as an explicit
  `btc1h_remote_provenance` checklist gate and sets BTC1H candidate
  `current_status = BTC1H_REMOTE_STATUS_STALE_OR_UNAVAILABLE`. This keeps the
  final decision surface aligned with the official-settlement/clean-clock
  boundary instead of treating the 11 remote rows as fresh promotion evidence.
- The multi-holdout research artifact now uses a stricter near-deployable
  label:
  - active BTC1H `research_promising = True`;
  - active BTC1H `near_deployable_candidate = False`;
  - `promotion_readiness_status =
    promising_but_blocked_by_official_or_fidelity_gates`;
  - disqualifying blockers are official/proxy mismatch, row-unfaithful
    counterfactual replay, missing exact cached TTL/model inputs, and
    cached-TTL-only forward fills.
- The next-forward candidate packet now carries the same readiness state in
  `btc1h_current_evidence_snapshot.csv`, so it no longer hardcodes the active
  BTC1H path as `near_deployable_research_only`.

## Snapshot Candidate-Scan Replay

Artifact:

- `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_candidate_scans_latest_codex`.

The broad all-scan counterfactual replay was too slow because it recomputed the
active policy on hundreds of thousands of captured `candidate_count = 0` scans.
The replay script now has two practical fixes:

- the BTC cache loader falls back to DuckDB when PyArrow cannot read the remote
  parquet;
- `--candidate-scan-only` evaluates only captured active-policy scans with
  `candidate_count > 0`.

This mode is an active-policy parity diagnostic, not a new-variant discovery
backtest. It completed on the May 21 paused BTC1H snapshot:

- streamed top-book rows: `2,000,000+`;
- captured signal scans consumed: `397,059+`;
- settled replay trades: `10`;
- replay PnL using captured lifecycle settlement: `+$1.11`;
- win rate: `80.0%`;
- max DD: `-$1.38`;
- trade Sharpe: `0.83`.

It still does not match the actual paper ledger well enough for promotion:

- actual BTC1H official paper ledger: `11` rows, `+$0.50`;
- replay matched `9` actual market/side keys;
- actual row missing from replay:
  `KXBTCD-26MAY1911-T76299.99|no` (`-$0.71`);
- replay replaced actual `KXBTCD-26MAY2010-T77199.99|no` with
  `KXBTCD-26MAY2010-T77299.99|no`.

So the optimized replay is useful and much faster, but the `+$1.11` result is
not deployable evidence. It is still overstating the actual ledger by about
`$0.61`, consistent with the cached-TTL/model-input parity blocker above.

Replay-vs-ledger reconciliation artifact:

- `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_latest_codex`.

Candidate-scan replay reconciliation:

- actual official ledger rows: `11`;
- replay rows: `10`;
- exact market/side matches: `9 / 11`;
- actual official PnL: `+$0.50`;
- replay PnL: `+$1.11`;
- replay-minus-actual PnL: `+$0.61`;
- `promotion_usable_replay = false`;
- blockers:
  `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.

A stricter selected-scan-only replay was also run:

- artifact:
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_selected_scans_latest_codex`;
- reconciliation:
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_selected_scans_latest_codex`;
- replay rows: `6`;
- exact market/side matches: `6 / 11`;
- replay PnL: `-$0.14`;
- `promotion_usable_replay = false`.

The stricter replay prevents later blocked/dedupe scans from creating adjacent
replacement trades, but it misses even more actual rows under scan-time model
recomputation. This reinforces the conclusion that the old cached-TTL shadow
rows are not clean evidence for the new exact scan-time-TTL policy.

## Decision-Time Distance Guard Audit

Artifact:

- `backtest_outputs\btc1h_decision_distance_guard_audit_latest_codex`.
- `backtest_outputs\btc1h_side_entry_profile_audit_latest_codex`.

This audit asks whether the current official/proxy flip is solved by requiring
more decision-time side margin from strike. It uses only decision-time distance:
YES margin is `spot - strike`, NO margin is `strike - spot`.

Result:

- no guard / `$25` guard:
  - `127` historical/proxy rows under `+2c` adverse entry stress;
  - `15 / 15` positive historical holdouts;
  - `6 / 6` positive websocket cadences;
  - current diagnostic official rows: `11`, PnL `+$0.50`, mismatch `1`.
- `$50` guard:
  - excludes the observed current official/proxy flip;
  - current diagnostic official rows fall to `9`, PnL rises to `+$1.88`,
    mismatch `0`;
  - historical holdout robustness worsens to `13 / 15`;
  - websocket cadence robustness worsens to `4 / 6`.
- `$75+` guards reduce sample size and degrade WS stability further.

Interpretation:

- A simple `$50` side-distance guard is not good enough to freeze as the next
  BTC1H policy despite fixing the current mismatch row.
- This is exactly why the current rows cannot be used to fit a deployable basis
  guard: the apparent fix is partly a one-row repair and breaks broader
  live-websocket cadence evidence.
- Keep the active forward candidate frozen without a distance guard unless a
  separate preregistered guard survives future clean-clock official rows.

Side/entry profile follow-up:

- `all_active`: `159` historical/proxy rows, `+$21.39`, `15 / 15` positive
  holdouts, `6 / 6` positive WS cadences, but current diagnostic official rows
  still have `1` mismatch/flip.
- `entry_60_70`: keeps `15 / 15` holdouts and `6 / 6` WS cadences, but current
  diagnostic official PnL drops to `+$0.09` on `10` rows and the mismatch/flip
  remains.
- `yes_only`: avoids the current official mismatch but has only `1` current
  official row, `14 / 15` positive holdouts, and `5 / 6` WS cadences.
- `no_only`: keeps `6 / 6` WS cadences but has only `+$0.18` current official
  PnL on `10` rows and retains the mismatch/flip.
- `no_entry_60_70`: current diagnostic official sample is negative
  (`-$0.23`) and still has the mismatch/flip.

Interpretation: no side-only or entry-band slice improves the deployability
case. The active frozen candidate remains the best next forward candidate, and
side/entry retunes should not be preregistered from the current stale rows.

## Scan-Time-TTL Evidence Epoch Prep

The local and remote BTC1H code has been prepared for a future clean
scan-time-TTL evidence clock, but the remote worker has not been restarted.

Changes made for future rows:

- `scripts\btc_1hr_research_live.py` now writes these policy identity fields
  into both the DuckDB capture stream and SQLite paper ledger:
  - `signal_strategy`;
  - `model_ttl_policy`;
  - `model_policy_version`.
- Current default policy identity:
  - `model_ttl_policy = scan_time_close_minus_now_v1`;
  - `model_policy_version = btc1h_live_model_20260522_scan_ttl_v1`.
- `scripts\check_btc_shadow_official_settlement.py` now preserves those fields
  in `shadow_official_trades.csv` and writes
  `shadow_official_policy_summary.csv`.
- Remote source files were uploaded to
  `ClawService@100.92.9.80:C:\Users\ClawService\Kalshi-Trading-Bot` after
  timestamped backups:
  - `runtime\script_backups\pre_scan_ttl_deploy_20260522_051842`;
  - `runtime\script_backups\pre_policy_settlement_deploy_20260522_052004`.
- Remote compile passed for the uploaded BTC1H live/replay/audit/settlement
  scripts.

The refreshed remote official settlement artifact now includes policy summary:

- artifact:
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex\shadow_official_policy_summary.csv`;
- all current BTC1H rows have blank policy fields because they were written by
  the old running process before these columns existed;
- BTC1H remains `11` official rows, official PnL `+$0.50`, win `72.73%`,
  proxy PnL `+$1.50`, and `1` proxy/official mismatch.

Operational boundary:

- Remote workers remained `ALL_RUNNING` after source upload.
- Existing BTC1H PID `5132` started at `2026-05-22T02:42:06Z`; it is still the
  old in-memory process.
- Future scan-time-TTL evidence requires an explicitly authorized controlled
  restart/start; rows before that restart must not be pooled with rows after it.

## Candidate Ranking

1. `high_conf_80_entry70_no_chase`: primary forward candidate. Strongest
   balance of historical PnL, drawdown, cadence robustness, and current active
   shadow coverage. Keep collecting.
2. `high_conf_80`: cadence-stable fallback, but weaker Predexon/null evidence.
   Worth keeping in research, not necessarily as a running shadow yet.
3. `high_conf_80_no_chase`: strong Predexon evidence but failed the 1s websocket
   cadence check because expensive `>70c` entries were unstable.
4. `high_conf_80_entry59_70_no_chase`: interesting but derived from filtered
   rows, not a full causal replay; do not promote without a real replay and
   forward shadow.

## Official Basis Watch

Artifact:

- `backtest_outputs\btc1h_official_basis_mismatch_audit_latest_codex`.
- `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex`.

Current official/proxy risk is not theoretical. The active BTC1H shadow has
`11` official-settled rows with official PnL `+$0.50` versus proxy PnL
`+$1.50`; the entire `-$1.00` gap comes from one proxy-win/official-loss flip.

Mismatch row:

- `KXBTCD-26MAY2008-T77299.99`, side `no`;
- proxy result `no`, official result `yes`;
- proxy margin in favor of NO was only about `$8.37`;
- official margin ended about `$33.78` against NO;
- official-minus-proxy basis was `$42.15`;
- proxy PnL `+$0.30`, official PnL `-$0.70`.

Audit summary:

- official/proxy mismatches: `1 / 11 = 9.09%`;
- proxy-win/official-loss flips: `1`;
- max absolute official/proxy basis: `$87.42`;
- p95 absolute official/proxy basis: `$78.915`;
- rows within `$50` of proxy boundary: `4`;
- rows within `$50` of official boundary: `4`.

This is diagnostic only. Do not fit a distance/basis guard on these rows. After
a clean scan-time-TTL evidence restart, monitor the same fields prospectively
and only consider a preregistered guard once enough official rows exist.

## Frozen Next Forward Packet

The next BTC1H forward evidence packet freezes
`high_conf_80_entry70_no_chase` for future clean-clock evidence:

- paper-only, flat one-contract sizing;
- TTL `5` to `20` minutes;
- entry `25c` to `70c`;
- YES probability `>= 0.80`, NO-side `p_yes <= 0.20`;
- min edge `12c`, max spread `2c`;
- no-chase 10-minute move threshold `$150`;
- expected policy `btc1h_live_model_20260522_scan_ttl_v1` /
  `scan_time_close_minus_now_v1`.

Promotion gates for future post-restart rows:

- at least `50` clean official-settled rows;
- official PnL positive after fees;
- proxy/official mismatch rate `<= 2%`;
- proxy-win/official-loss flips `0`;
- row-for-row replay/ledger match;
- complete execution-realism and model-input fields;
- no blank policy rows.

The packet status is `READY_FOR_EXPLICIT_RESTART_AUTHORIZATION`, not
deployable. Current stale rows do not count.

The packet now also writes
`backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex\btc1h_candidate_decision_packet.csv`
so the current strategy choices are explicit:

- `high_conf_80_entry70_no_chase`:
  `keep_as_only_frozen_clean_clock_control`;
- `high_conf_80_entry59_70_no_chase`:
  `defer_shadow_until_independent_causal_rows`;
- `high_conf_80_no_chase`: `basis_watchlist_only_no_restart`;
- `high_conf_80`: `deprioritize`.

## Promotion Gap Matrix

Artifact:

- `backtest_outputs\btc1h_promotion_gap_matrix_latest_codex`.

Current matrix verdict for `high_conf_80_entry70_no_chase`:

- deployable now: `False`;
- near-deployable research candidate: `False`;
- recommended next clean-clock policy: `high_conf_80_entry70_no_chase`;
- recommended policy change: `none`;
- recommended next action:
  `explicitly_authorized_controlled_restart_then_collect_50_clean_official_rows`.

Gate counts:

- blocked gates: `8`;
- rejected diagnostic retunes: `2`;
- diagnostic-only gates: `5`;
- research-only passes: `1`;
- operational passes: `1`.

This is the compact operational read:

- The historical/live-WS holdout story is strong enough to keep researching:
  `14 / 14` holdouts, `6 / 6` WS cadences, `+$21.39` historical/proxy PnL.
- Frozen policy parity passes.
- Official PnL is positive only on stale pre-clean-clock rows, so it is
  diagnostic rather than promotional.
- Basis-stressed proxy labels are diagnostic only: the active candidate
  survives `$50` side-adverse basis on stressable historical rows, but p95/max
  observed official basis erases or reverses the de-duplicated edge.
- Cross-variant basis stress is diagnostic only: runner-ups are more robust to
  observed basis shocks, but they lack active forward validation or have other
  rejected robustness failures.
- Holdout independence is diagnostic only: the pooled historical result has
  `90` duplicate market/side rows, though the de-duplicated panel remains
  positive.
- Statistical confidence is also diagnostic only: pooled historical rows look
  strong, but the unique market/side panel has a negative bootstrap lower bound
  and the stale official panel has no useful statistical power.
- The deployability blockers are still clean evidence clock, clean sample size,
  execution-realism fields, proxy/official agreement, policy identity fields,
  source freshness, row-for-row replay, and overall readiness.
- The obvious retunes are rejected:
  - distance guard fixes one flip but breaks WS cadence robustness;
  - side/entry slices do not improve deployability.

## Variant Basis-Stress Ranking

Artifact:

- `backtest_outputs\btc1h_variant_basis_stress_ranking_latest_codex`.

This compares already-frozen BTC1H variants under the same side-adverse basis
stress. It is not a threshold search and does not change the active forward
policy.

Ranking by max adverse basis shock with positive unique market/side PnL:

| Variant | Max Positive Unique Shock | P95 Basis Result | Current Interpretation |
| --- | ---: | --- | --- |
| `high_conf_80_no_chase` | `$87.42` | `+$2.70` pooled, `+$1.60` unique, `8 / 12` holdouts positive | basis-robust but previously failed WS cadence/expensive-entry checks |
| `high_conf_80_entry59_70_no_chase` | `$87.42` | `+$3.00` pooled, `+$1.41` unique, `8 / 9` holdouts positive | basis-robust historical-only runner-up; needs causal replay and forward shadow |
| `high_conf_80_entry70_no_chase` | `$75.00` | `+$0.04` pooled, `-$0.22` unique, `4 / 9` holdouts positive | active near-forward candidate, but basis-fragile |
| `high_conf_80` | `$50.00` | `-$2.11` pooled and unique, `2 / 5` holdouts positive | fallback research only |

Interpretation:

- the current active BTC1H candidate remains the best next clean-clock forward
  candidate because it has the active shadow, official rows, and frozen packet;
- it is not the best basis-stress historical variant;
- a future research branch should revisit `entry59_70_no_chase` and
  `high_conf_80_no_chase` only through causal replay plus fresh forward shadow
  evidence, not by switching the current policy from this diagnostic.

## No-Chase Extra-Row Audit

Artifact:

- `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_fullscan_no_chase_latest_codex`;
- `backtest_outputs\btc1h_replay_variant_overlap_no_chase_latest_codex`;
- `backtest_outputs\btc1h_no_chase_extra_row_audit_latest_codex`.

This fixed diagnostic compares broad `high_conf_80_no_chase` against active
`high_conf_80_entry70_no_chase` only on artifacts where both variants are
present. It recomputes row PnL with the standard `+2c` adverse entry stress and
taker fee, then asks whether the rows admitted by removing the 70c entry cap
help or hurt.

The paused May 21 snapshot was also replayed for broad no-chase with captured
`signal_scan` timestamps, live scan semantics, and `--no-public-fallback`.

Result:

- broad no-chase full-scan replay: `17` signals, `16` settled rows, PnL
  `+$1.68`, win `81.25%`, max DD `-$1.41`;
- full-scan overlap versus active entry70: active rows `14`, no-chase rows
  `17`, shared rows `12`, active-only rows `2`, no-chase-only rows `5`, PnL
  diff `-$0.31`;
- full-scan `+2c` extra-row stress: `5` extra no-chase rows, `3` independent
  market/side rows, extra-row PnL `-$0.84`;
- comparable evidence labels: `19`;
- no-chase rows: `208`;
- entry70 rows: `173`;
- exact extra broad no-chase rows: `55`;
- all extra rows were `>70c`;
- total extra no-chase PnL: `-$2.24`;
- live-WS extra no-chase PnL: `-$3.32`;
- live-WS labels with negative extra-row PnL: `4 / 6`;
- 1s live-WS extra `>70c` rows: `11`, PnL `-$1.41`;
- status: `NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING`.

Interpretation:

- broad no-chase can remain a basis-stress research watchlist policy, but the
  extra-row audit makes the cadence blocker concrete;
- the 70c cap is currently justified as a damage-removal diagnostic, not a new
  deployable threshold;
- do not restart or promote broad no-chase without fresh causal replay and
  official forward rows.

## Research Priority Matrix

Artifact:

- `backtest_outputs\btc1h_research_priority_matrix_latest_codex`.

This matrix merges the fixed multi-holdout result, variant basis-stress
ranking, and promotion-gap state. It is diagnostic only: it ranks the next
research work, not deployment eligibility.

Current ranking:

| Variant | Classification | Forward Rank | Research Replay Rank | Key Evidence |
| --- | --- | ---: | ---: | --- |
| `high_conf_80_entry70_no_chase` | active clean forward control | `1` |  | only policy with official forward rows: `11`, official PnL `+$0.50`, but stale/blocked |
| `high_conf_80_entry59_70_no_chase` | top replay runner-up but not independent on snapshot |  | `1` | `12 / 13` fixed holdouts, `6 / 6` WS cadences, p95 basis unique PnL `+$1.41`, but `0` independent live-WS replay rows versus active |
| `high_conf_80_no_chase` | basis-robust watchlist |  | `2` | basis-stress winner, but exact extra rows total `-$2.24`, full-scan extra rows are `-$0.84`, and 1s live-WS extra `>70c` rows are `-$1.41` |
| `high_conf_80` | low priority or reject |  | `3` | weaker holdout and basis-stress picture |

Current conclusion:

- recommended forward policy change: `none`;
- keep `high_conf_80_entry70_no_chase` only as the frozen clean-clock control
  if the user explicitly authorizes a controlled restart;
- do not start a separate `high_conf_80_entry59_70_no_chase` shadow yet,
  because the paused May 21 live-WS replay row set is identical to active
  `entry70_no_chase`;
- broaden causal replay/model-input parity for `entry59_70_no_chase` before
  any paper-shadow discussion;
- do not treat `high_conf_80_no_chase` as better just because it wins the
  basis-stress table; its extra rows damage the live-WS cadence checks.

## Entry59 Replay Overlap Audit

Artifacts:

- `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_fullscan_entry59_latest_codex`;
- `backtest_outputs\btc1h_replay_variant_overlap_latest_codex`.

The paused May 21 remote BTC1H snapshot was replayed for
`high_conf_80_entry59_70_no_chase` using captured `signal_scan` timestamps and
live scan semantics. This is a causal live-WS replay diagnostic, not a new
forward shadow.

Result:

- `entry59_70_no_chase`: `14` signals, `13` settled, PnL `+$1.99`, win
  `84.6154%`, max DD `-$1.40`, Sharpe `1.455433`;
- overlap versus the active `entry70_no_chase` full-scan replay:
  - active rows: `14`;
  - entry59 rows: `14`;
  - shared rows: `14`;
  - active-only rows: `0`;
  - entry59-only rows: `0`;
  - independent challenger rows: `0`;
  - status: `EXACT_ROW_SET_MATCH`.

Interpretation:

- the entry59 band did not change the live-WS replay on this snapshot because
  the active entry70 rows already landed inside the 59c-70c band;
- this is positive but not independent evidence;
- `entry59_70_no_chase` should not get a separate paper shadow until broader
  causal replay or future clean rows show it actually differs from the active
  policy.

## Entry59 Floor-Filter Audit

Artifact:

- `backtest_outputs\btc1h_entry59_floor_filter_audit_latest_codex`.

This fixed diagnostic compares `high_conf_80_entry59_70_no_chase` against the
active `high_conf_80_entry70_no_chase` across paired direct Predexon aggregate
rows and the paused live-WS full-scan replay. It skips the latest robustness log
because that artifact does not contain the entry59 variant, so it cannot be used
to infer deleted entry59 rows.

Result:

- comparable evidence labels: `6`;
- live-WS full-scan snapshot: exact row-set match, `14` active rows and `14`
  entry59 rows, with `0` entry59-only market/side rows;
- direct Predexon aggregate: `32` active rows versus `27` entry59 rows;
- total exact active-only rows across comparable labels: `6`, PnL `+$1.79`;
- total entry59-only exact rows: `1`, PnL `+$0.32`;
- total entry59-only independent market/side rows: `1`;
- deleted low-entry active rows: `6`, PnL `+$1.79`;
- status: `ENTRY59_HAS_INDEPENDENT_MARKET_SIDE_ROWS`, but only from one
  historical direct Predexon row.

Interpretation:

- the paused live-WS evidence still adds `0` independent entry59 rows versus
  active entry70;
- the one direct historical entry59-only row is useful for broader replay
  targeting, but it is not forward evidence and cannot justify a separate
  shadow;
- priority-matrix next action remains: do not change forward policy; broaden
  causal replay or wait for future clean rows that actually differ.

## Official-Basis Stress Audit

Artifact:

- `backtest_outputs\btc1h_basis_stress_audit_latest_codex`.

This audit keeps the active `high_conf_80_entry70_no_chase` policy fixed and
moves each stressable proxy settlement against the trade side. YES rows are
shifted lower; NO rows are shifted higher. It is a stress test, not a guard.

Stressable coverage:

- `62` active-candidate historical/proxy rows with usable settlement spot;
- `32` unique market/side rows;
- `9` historical holdout buckets;
- live-WS rows without usable proxy settlement spot are not included in this
  stress table.

Results under `+2c` adverse entry stress:

- `$0`, `$25`, `$50` side-adverse basis: `0` flips, PnL `+$9.04`, unique
  market/side PnL `+$4.78`, `9 / 9` positive stressable holdouts;
- `$75` side-adverse basis: `7` flips, PnL `+$2.04`, unique PnL `+$0.78`,
  `6 / 9` positive holdouts;
- observed p95 absolute official/proxy basis `$78.915`: `9` flips, PnL
  `+$0.04`, unique PnL `-$0.22`, only `4 / 9` positive holdouts;
- observed max basis `$87.42`: `11` flips, PnL `-$1.96`, unique PnL
  `-$1.22`, only `2 / 9` positive holdouts.

Interpretation:

- moderate `$50` basis stress does not kill the stressable historical panel;
- p95/max observed basis from the stale official sample is enough to erase or
  reverse the de-duplicated edge;
- this strengthens the no-deploy conclusion and argues for clean official
  evidence before any basis guard or promotion.

## Holdout Independence Audit

Artifact:

- `backtest_outputs\btc1h_holdout_independence_audit_latest_codex`.

This audit checks whether the active `high_conf_80_entry70_no_chase`
multi-holdout result is over-counting duplicate market/side rows or depending
on one event/holdout cluster.

Results:

- pooled historical rows: `159` rows, PnL `+$21.39`, `90` duplicate
  market/side rows, `63` event clusters, `15` holdout keys, leave-one-event
  minimum PnL `+$19.41`;
- de-duplicated market/side rows: `69` rows, PnL `+$5.82`, `63` event
  clusters, `10` holdout keys, leave-one-event minimum PnL `+$5.26`;
- stale forward official rows: `11` rows, official PnL `+$0.50`,
  leave-one-event minimum PnL `+$0.09`, diagnostic only;
- weak buckets:
  - `direct_predexon_trade_logs|H2b_direct_apr23_may01_holdout`: `+$0.06`,
    flips negative under one-event removal;
  - `robustness_trade_logs|D1_predexon_mar24_apr01_dev`: `+$0.22`, flips
    negative under one-event removal.

Interpretation:

- the candidate is not just one winning event cluster;
- the de-duplicated panel remains positive, which supports continued research;
- the headline pooled PnL is overstated by duplicate/cadence-overlapping rows;
- this adds a diagnostic caveat, not a policy change.

## Statistical Confidence Audit

Artifact:

- `backtest_outputs\btc1h_statistical_confidence_audit_latest_codex`.

This audit does not retune thresholds. It asks whether the active
`high_conf_80_entry70_no_chase` evidence still looks strong under row
bootstrap, event-cluster bootstrap, de-duplicated market/side rows, and a
simple breakeven-null simulation.

Results:

- naive pooled historical rows: `159` rows, PnL `+$21.39`, win `81.76%`,
  max DD `-$1.82`, Sharpe `4.45`, event-cluster p2.5 `+$5.7395`,
  breakeven-null p `0.00025`;
- unique market/side historical rows: `69` rows, PnL `+$5.82`, win `76.81%`,
  max DD `-$1.95`, Sharpe `1.67`, event-cluster p2.5 `-$1.28`,
  breakeven-null p `0.043898`;
- robustness rows: `127` rows, PnL `+$16.61`, event-cluster p2.5 `+$2.77`,
  breakeven-null p `0.00065`;
- direct rows: `32` rows, PnL `+$4.78`, event-cluster p2.5 `+$0.24975`,
  breakeven-null p `0.049448`;
- stale forward official rows: `11` rows, official PnL `+$0.50`, win
  `72.73%`, max DD `-$1.76`, Sharpe `0.315`, event-cluster p2.5 `-$2.61`,
  breakeven-null p `0.514474`.

Interpretation:

- the pooled historical result is strong enough to justify continued BTC1H
  research;
- the de-duplicated market/side panel is fragile enough that we should not talk
  about the candidate as statistically secure;
- the current official rows are too stale and too small to support any
  promotion claim;
- no policy change follows from this audit.

## Replay Mismatch Diagnosis

Artifact:

- `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_latest_codex`.
- `btc1h_replay_vs_ledger_mismatch_diagnosis.csv`.

The replay-vs-ledger audit now requires exact market/side, entry price, and
PnL parity. Current result remains blocked:

- actual official rows: `11`;
- replay rows: `10`;
- exact market/side matches: `9 / 11`;
- entry-price drift rows: `2`;
- PnL drift rows: `2`;
- actual official PnL: `+$0.50`;
- replay PnL: `+$1.11`;
- replay-minus-actual PnL: `+$0.61`;
- blockers:
  `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;entry_price_not_row_for_row_equal;pnl_not_row_for_row_equal`.

Diagnosis rows:

- `KXBTCD-26MAY1911-T76299.99|no`:
  `captured_live_fill_missing_after_skip_then_fill`. The live chain selected
  the same market twice: first a `failed_ws_reprice_filter` skip, then a
  `paper_fill`. Replay missed the later filled row.
- `KXBTCD-26MAY2010-T77199.99|no`:
  `captured_live_fill_replaced_by_replay_market`. Actual paper filled
  `T77199.99` NO at `0.57`; replay chose `T77299.99` NO in the same event.
- `KXBTCD-26MAY2010-T77299.99|no`:
  `replay_extra_market_replacing_captured_live_fill`.
- `KXBTCD-26MAY2107-T77299.99|no` and
  `KXBTCD-26MAY2110-T76699.99|yes`:
  `matched_market_with_entry_or_pnl_drift`; both are one-cent entry/PnL drifts.

Interpretation:

- the replay blocker is now specific enough to guide the next fix;
- future replay work needs to reproduce skip-then-fill sequencing, same-event
  market choice, and exact entry-price/PnL behavior;
- no deployment or process action follows from this diagnostic.

## Cached-TTL Replay Diagnostic

Artifact:

- `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_candidate_scans_ttl15_latest_codex`.
- `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_ttl15_latest_codex`.

A diagnostic replay mode was added with `--model-ttl-override-min 15` to test
whether the old live rows were mostly explained by using a fixed cached model
horizon rather than scan-time TTL. This was a negative result:

- replay rows: `7`;
- exact market/side matches: `7 / 11`;
- entry-price drift rows: `3`;
- PnL drift rows: `3`;
- replay PnL: `+$1.21`;
- replay-minus-actual PnL: `+$0.71`;
- `promotion_usable_replay = false`.

Interpretation:

- fixed `15` minute model TTL is not sufficient for faithful old-row replay;
- it removes the same-event market replacement but misses more actual fills;
- the next replay fix needs exact captured model inputs and reprice/fill
  sequencing, not a global TTL override.

## Replay Root-Cause Audit

Artifact:

- `backtest_outputs\btc1h_replay_root_cause_audit_latest_codex`.

The audit joins replay mismatch rows to selected-signal parity and captured
order-decision timing. It gives the current row-fidelity blockers as:

- `same_event_market_selection_not_row_faithful = 2`;
- `reprice_skip_then_fill_sequence_missing = 1`;
- `order_decision_reprice_fill_price_missing = 1`;
- `blocked_dedupe_or_post_selected_scan_used_as_replay_clock = 1`.

Replay-mode comparison:

- candidate-scan and candidate-selected-market: `9 / 11` exact market/side
  matches, but still event replacements plus entry/PnL drift;
- selected-scan and selected-market: only `6 / 11` exact matches;
- fixed `15` minute cached-TTL replay: only `7 / 11` exact matches;
- fullscan prefilter: `14` replay rows, but still only `9 / 11` exact matches
  and extra-row/event-replacement blockers.

Interpretation:

- no replay mode currently available is promotion usable;
- the next faithful replay work must model order-decision reprice/fill
  sequencing and must not trade from blocked/dedupe scans as if they were
  selected scans;
- this reinforces the current status:
  `high_conf_80_entry70_no_chase` is research-promising but not near-deployable.

Decision artifact update:

- `backtest_outputs\btc1h_promotion_gap_matrix_latest_codex` now carries the
  replay root-cause counts directly in the `row_for_row_replay` gate.
- `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex` now
  carries the same root-cause counts and records `0 / 6` promotion-usable replay
  modes.
- The next-forward evidence snapshot explicitly says replay still requires
  order-decision reprice/fill modeling and blocked-dedupe filtering.

## Replay Repair Feasibility

Artifact:

- `backtest_outputs\btc1h_replay_repair_feasibility_latest_codex`.

The feasibility checklist keeps two ideas separate:

- replay repairs we can test on the existing paused BTC1H snapshot;
- promotion gates that require future clean-clock official rows.

Current summary:

- `deployable_now = false`;
- `near_deployable_after_current_replay_repairs = false`;
- `replay_repair_can_make_deployable_now = false`;
- promotion-usable replay modes: `0 / 6`;
- current official rows: `11`;
- clean promotion floor: `50`;
- official/proxy mismatch rate: `9.09%`;
- clean clock: `BLOCKED_CONTROLLED_RESTART_REQUIRED`.

Snapshot-testable replay repairs:

- `order_decision_reprice_fill_model`: fix replay to model captured
  `order_decision` skip/fill sequence and filled entry price, then rerun exact
  market/entry/PnL reconciliation.
- `blocked_dedupe_filter`: prevent candidate-scan replay from trading
  blocked/dedupe or post-selected scan rows as if they were selected scans.
- `same_event_selected_market_dedupe`: force same-event replay to honor the
  captured selected-market/order-decision semantics.
- `row_for_row_reconciliation_after_repairs`: after repairs, at least one
  replay mode must become promotion usable with exact row parity.

Future-row gates:

- `exact_model_input_capture`: old rows have zero captured-TTL rows and fail
  selected-signal model parity for promotion.
- `official_proxy_basis_gate`: current stale official sample has a `9.09%`
  mismatch rate and one proxy-win/official-loss flip.
- `sample_size_gate`: old/current official rows are short by `39` versus the
  `50` row floor.
- `clean_policy_identity`: old rows have blank policy identity and missing
  sidecar fields, so they cannot be pooled into the next evidence clock.

Interpretation:

- the next engineering target is replay fidelity on the paused snapshot, not a
  live process change;
- even perfect replay repair would still leave BTC1H blocked by future
  clean-clock official evidence requirements.

## Captured Order-Decision Replay Baseline

Artifacts:

- `backtest_outputs\btc1h_order_decision_replay_baseline_latest_codex`;
- `backtest_outputs\btc1h_order_decision_replay_baseline_reconciliation_latest_codex`.

A new diagnostic baseline converts the paused snapshot's captured
`order_decision` paper fills into replay-shaped trade rows, then runs the same
replay-vs-ledger reconciler against them.

Result:

- baseline rows: `11`;
- settled rows: `11`;
- official PnL: `+$0.50`;
- official premium: `$7.50`;
- official win rate: `72.7273%`;
- max drawdown: `-$1.76`;
- actual rows vs replay rows: `11 / 11`;
- exact market/side matches: `11 / 11`;
- entry-price drift rows: `0`;
- PnL drift rows: `0`;
- row fidelity exact: `true`;
- promotion usable replay: `false`;
- blocker: `diagnostic_replay_not_independent_counterfactual`.

Interpretation:

- captured decisions, official settlement, and reconciliation can line up
  exactly;
- this does not count as independent counterfactual replay, because it is
  anchored to the live decisions already made;
- the remaining replay blocker is specifically independent replay scan/model
  semantics, not corrupted raw decision or settlement rows.

## Independent Replay Repair Targets

Artifact:

- `backtest_outputs\btc1h_replay_repair_target_matrix_latest_codex`.

The repair target matrix compares the current independent replay to the exact
captured order-decision baseline and turns the root-cause audit into measurable
next targets.

Current comparison:

- independent replay row fidelity exact: `false`;
- independent replay promotion usable: `false`;
- captured order-decision baseline row fidelity exact: `true`;
- captured order-decision baseline promotion usable: `false`;
- independent replay gap is not a data/settlement pipeline issue: `true`;
- independent replay exact market/side matches: `9 / 11`;
- independent replay rows: `10`;
- actual official rows: `11`;
- replay-minus-actual PnL: `+$0.61`.

Independent replay repair targets:

- `skip_then_fill_sequence`: one `KXBTCD-26MAY1911` row where the live chain
  skipped after failed reprice and later filled the same market.
- `same_event_market_selection`: two `KXBTCD-26MAY2010` rows where independent
  replay chose a different market in the same event.
- `order_decision_reprice_fill_price`: one `KXBTCD-26MAY2107` row with
  one-cent entry/PnL drift.
- `blocked_dedupe_scan_clock`: one `KXBTCD-26MAY2110` row where replay appears
  to trade from the wrong scan clock.

Future clean-clock blockers remain:

- `exact_model_input_capture`;
- `official_proxy_basis_gate`;
- `sample_size_gate`;
- `clean_policy_identity`.

Interpretation:

- the next replay edit has a clear measurable target: remove the four
  independent replay row-fidelity classes and rerun reconciliation;
- success here still would not deploy BTC1H, because future clean-clock
  official-settled evidence is still required.

## Replay Repair Prerequisites

Artifact:

- `backtest_outputs\btc1h_replay_repair_prerequisite_audit_latest_codex`.

This audit tightens the target matrix so we do not overstate what the paused
snapshot can fix.

Current prerequisite split:

- existing snapshot can test independent replay repair for `2` targets:
  `order_decision_reprice_fill_price` and `blocked_dedupe_scan_clock`;
- existing snapshot is only partial for `same_event_market_selection`;
- existing snapshot cannot independently repair `skip_then_fill_sequence`;
- future exact-input blocked targets:
  `same_event_market_selection;skip_then_fill_sequence`;
- all targets repairable from the current snapshot: `false`;
- near-deployable after current replay repairs: `false`;
- deployable now: `false`.

Target details:

- `order_decision_reprice_fill_price`: `KXBTCD-26MAY2107`; selected-chain fill
  exists and the captured decision fill price explains the one-cent entry/PnL
  drift.
- `blocked_dedupe_scan_clock`: `KXBTCD-26MAY2110`; selected-chain fill exists
  and replay is `0.727796` seconds away from selected timing.
- `same_event_market_selection`: `KXBTCD-26MAY2010`; partial only because the
  actual selected market has model/edge drift and the replay-only replacement
  market has no captured selected-chain row.
- `skip_then_fill_sequence`: `KXBTCD-26MAY1911`; not independently repairable
  from this snapshot because scan-TTL parity has `0` recomputed signal rows and
  `2` no-signal rows for the target fill market.

Interpretation:

- the next replay-code work should start with the two matched-drift rows;
- same-event market choice and skip-then-fill sequencing need exact model-input
  or TTL capture before they can become independent counterfactual evidence;
- captured order-decision parity remains diagnostic only, not promotion
  evidence.

## Replay Repair Attempts

Artifacts:

- `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_selected_scans_latest_codex`;
- `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_selected_scans_latest_codex`;
- `backtest_outputs\btc1h_replay_repair_attempt_audit_latest_codex`.

Strict selected-scan replay was rerun with captured `signal_scan` clock,
`--selected-scan-only`, and `--selected-market-only`. It is not a repair:

- actual rows: `11`;
- replay rows: `6`;
- exact market/side matches: `6`;
- ledger-only rows: `5`;
- replay-only rows: `0`;
- event replacement rows: `0`;
- entry/PnL drift rows: `1 / 1`;
- replay-minus-actual PnL: `-$0.64`;
- verdict: `REGRESSES_ROW_FIDELITY`.

The matched-drift diagnostic patch simulation is the best current-snapshot
unit target, but it is not promotion evidence:

- patched rows: `2`;
- entry/PnL drift rows after patch: `0 / 0`;
- replay rows remain `10` vs `11` actual;
- exact market/side matches remain `9 / 11`;
- ledger-only rows remain `2`;
- replay-only rows remain `1`;
- event replacement rows remain `2`;
- replay-minus-actual PnL remains `+$0.61`;
- remaining blockers:
  `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.

Interpretation:

- `selected_scan_only` / `selected_market_only` is not the blocked-dedupe
  repair; it removes event replacements by dropping too many actual fills;
- the two matched-drift rows are useful replay-engineering unit tests, but the
  independent replay still needs exact model-input/TTL capture plus row-for-row
  market selection and skip/fill reproduction;
- BTC1H remains observe-only and not near-deployable.

## Replay Repair Top-Level Gate

Artifacts:

- `backtest_outputs\btc1h_promotion_gap_matrix_latest_codex`;
- `backtest_outputs\btc1h_research_priority_matrix_latest_codex`;
- `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_repair_attempt_topline_latest_codex`.

The replay repair-attempt result now appears in the top-level BTC1H decision
artifacts:

- promotion gap `blocked_gate_count`: `9`;
- explicit gate: `replay_repair_attempts = BLOCKED`;
- gate blockers:
  `current_snapshot_repairs_not_promotion_usable;selected_scan_attempt_regresses_row_fidelity`;
- strict selected-scan verdict: `REGRESSES_ROW_FIDELITY`;
- current snapshot repairs promotion usable: `false`;
- remaining blockers after best attempt:
  `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.

The priority matrix and next forward packet now carry these fields directly,
so candidate ranking and the frozen forward packet cannot accidentally read the
matched-drift patch simulation as promotion evidence.

Validation:

- focused compile over the promotion, priority, packet, and refresh scripts;
- focused pytest:
  `scripts\test_btc1h_promotion_gap_matrix.py`,
  `scripts\test_btc1h_research_priority_matrix.py`,
  `scripts\test_btc1h_next_forward_candidate_packet.py`, and
  `scripts\test_btc_evidence_stack_refresh.py` -> `10 passed`;
- bounded refresh from `btc1h_replay_root_cause_audit` through
  `gpt_pro_action_status` -> `26 / 26` selected read-only steps passed, with
  deployment readiness returning expected no-deploy code `1`.

Verdict:

- no process changes were made;
- no deployment is authorized;
- the active BTC1H candidate remains a research/control policy only until a
  clean evidence clock, official settlement, execution realism, and
  independent row-for-row replay all pass.

## Objective Completion Audit

Artifacts:

- `backtest_outputs\btc1h_objective_completion_audit_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_objective_audit_latest_codex`.

The objective-level audit now answers the original BTC1H research question from
machine-readable evidence rather than from summary prose.

Current verdict:

- `objective_complete = false`;
- deployable BTC1H candidates: `0`;
- near-deployable BTC1H candidates: `0`;
- promising research candidates: `3`;
- critical blocked requirements:
  `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.

Candidate statuses:

- `high_conf_80_entry70_no_chase`:
  `promising_research_control_blocked`;
- `high_conf_80_entry59_70_no_chase`:
  `promising_research_runner_up_not_independent`;
- `high_conf_80_no_chase`: `basis_watchlist_not_forward_validated`;
- `high_conf_80`: `historical_promising_blocked`.

Requirement statuses:

- multiple-holdout candidate coverage: `PASS_RESEARCH_EVIDENCE`;
- candidate ranking / identification: `PASS_IDENTIFIED`;
- deployable-or-near-deployable verdict:
  `PASS_NO_DEPLOYABLE_OR_NEAR_DEPLOYABLE_FOUND`;
- official settlement: `BLOCKED`;
- execution realism: `BLOCKED`;
- faithful live replay: `BLOCKED`;
- clean evidence clock: `BLOCKED`.

Refresh/validation:

- `btc1h_objective_completion_audit` was initially added immediately after the
  BTC1H promotion-gap matrix; after the snapshot execution diagnostic below it
  now runs as step `55 / 60`;
- bounded refresh from `btc1h_promotion_gap_matrix` through
  `gpt_pro_action_status` passed all `7 / 7` selected read-only steps;
- focused pytest for the objective audit plus refresh-order tests passed:
  `8 passed`.

## Snapshot Execution Realism

Artifacts:

- `backtest_outputs\btc1h_snapshot_execution_realism_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_snapshot_execution_latest_codex`.

The paused BTC1H snapshot contains decision-time execution fields for all
`11` paper-filled ledger rows:

- required execution field complete rate: `1.0`;
- entry matches side ask rate: `1.0`;
- actual entry matches entry rate: `1.0`;
- fee present/nonnegative rate: `1.0`;
- top visible quantity >= contracts rate: `1.0`;
- official rows / PnL: `11` / `+$0.50`.

It still does not satisfy promotion evidence:

- quote-age <= `250ms` rate: `0.9090909090909091`;
- stale quote row:
  `KXBTCD-26MAY2106` / `KXBTCD-26MAY2106-T77699.99`,
  side `no`, entry `0.68`, top visible quantity `75`, quote age
  `260.9622ms`;
- official/proxy mismatches: `1`;
- blank policy rows: `11`;
- promotion usable: `false`;
- blockers:
  `quote_age_above_limit;official_proxy_mismatch_present;pre_clean_clock_blank_policy_rows;old_snapshot_not_clean_clock_promotion_evidence`.

The objective audit now includes this diagnostic under the execution-realism
requirement. The status remains `BLOCKED`: this only proves the old snapshot is
better instrumented than the generic readiness row suggested; it does not
override the clean-clock, official-settlement, quote-age, or replay-fidelity
gates.

Refresh/validation:

- `btc1h_snapshot_execution_realism_audit` was initially step `54 / 60`; after
  the execution-filter audit below it remains step `54`, and the refresh plan
  now has `61` total steps;
- bounded refresh from `btc1h_promotion_gap_matrix` through
  `gpt_pro_action_status` passed all `8 / 8` selected read-only steps;
- focused pytest for snapshot execution, objective audit, and refresh-order
  tests passed: `9 passed`.

## Execution Filter Impact

Artifacts:

- `backtest_outputs\btc1h_execution_filter_impact_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_execution_filter_latest_codex`.

Applying the strict snapshot execution-realism filter removes the one stale
quote row and weakens the current official result:

- all paused snapshot rows:
  - rows: `11`;
  - official PnL: `+$0.50`;
  - proxy PnL: `+$1.50`;
  - official/proxy mismatches: `1`;
  - mismatch rate: `9.09%`;
- strict execution-filtered rows:
  - rows: `10`;
  - removed rows: `1`;
  - official PnL: `+$0.20`;
  - proxy PnL: `+$1.20`;
  - official/proxy mismatches: `1`;
  - mismatch rate: `10.00%`.

Removed row:

- `KXBTCD-26MAY2106` / `KXBTCD-26MAY2106-T77699.99`;
- side `no`;
- entry `0.68`;
- quote age `260.9622ms`;
- official/proxy result both `no`;
- official PnL `+$0.30`;
- removal reason: `quote_age_above_limit`.

The strict filtered slice remains diagnostic only:

- `too_few_execution_filtered_official_rows`;
- `official_proxy_mismatch_remaining`;
- `old_snapshot_not_clean_clock_promotion_evidence`;
- `replay_parity_still_required`.

The objective audit now records this execution-filter impact. The conclusion is
unchanged but sharper: execution filtering does not rescue BTC1H; it reduces the
official PnL and leaves the official/proxy mismatch in the sample.

Refresh/validation:

- `btc1h_execution_filter_impact_audit` was added as step `55 / 61`; after
  the basis-mismatch audit below, it remains after snapshot execution realism
  and immediately before the strict-filter basis diagnostic;
- bounded refresh from `btc1h_promotion_gap_matrix` through
  `gpt_pro_action_status` passed all `9 / 9` selected read-only steps;
- focused pytest passed: `10 passed`.

## Execution-Filtered Basis Mismatch

Artifacts:

- `backtest_outputs\btc1h_execution_filtered_basis_mismatch_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_exec_filter_basis_latest_codex`.

The strict execution-filtered slice still contains the one official/proxy
settlement mismatch:

- strict execution-filtered official rows: `10`;
- strict official PnL: `+$0.20`;
- strict proxy PnL: `+$1.20`;
- strict official/proxy mismatches: `1`;
- mismatch removed by execution filter: `false`.

Surviving mismatch:

- `KXBTCD-26MAY2008` / `KXBTCD-26MAY2008-T77299.99`;
- side `no`;
- entry `0.68`;
- official/proxy result `yes/no`;
- official PnL `-$0.70`;
- proxy PnL `+$0.30`;
- official-minus-proxy spot `+$42.15`;
- proxy close minus strike `-$8.37`;
- official expiration minus strike `+$33.78`;
- quote age `72.4969ms`;
- top visible quantity `881`.

The objective audit now records:

- `execution_filtered_basis_mismatch_rows = 1`;
- `execution_filtered_basis_mismatch_market =
  KXBTCD-26MAY2008-T77299.99`;
- `execution_filtered_basis_mismatch_removed_by_execution_filter = False`;
- `execution_filtered_basis_mismatch_official_minus_proxy_spot = 42.15`.

This is a basis/near-boundary diagnostic, not a fitted guard. Current blockers:

- `single_mismatch_after_execution_filter`;
- `too_few_strict_official_rows`;
- `old_snapshot_not_clean_clock_promotion_evidence`;
- `guard_not_fit_from_current_rows`;
- `replay_parity_still_required`.

Refresh/validation:

- `btc1h_execution_filtered_basis_mismatch_audit` is now step `56 / 62`, after
  execution-filter impact and before objective completion;
- bounded refresh from `btc1h_promotion_gap_matrix` through
  `gpt_pro_action_status` passed all `10 / 10` selected read-only steps;
- focused pytest passed: `11 passed`.

## Remaining Evidence Manifest

Artifacts:

- `backtest_outputs\btc1h_remaining_evidence_manifest_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_remaining_evidence_latest_codex`.

Added `scripts\build_btc1h_remaining_evidence_manifest.py`. It turns the
objective-completion and promotion-gap blockers into explicit evidence rows
with these safety fields:

- `requires_explicit_authorization`;
- `requires_process_control`;
- `can_current_artifacts_satisfy`;
- `preregistration_required`;
- `safe_now_action`.

Current summary:

- manifest status: `BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
- deployable now: `false`;
- near-deployable now: `false`;
- requirements total: `7`;
- current-artifact-satisfiable requirements: `0`;
- requirements requiring explicit authorization: `6`;
- requirements requiring process control: `6`;
- preregistration-required requirements: `6`;
- critical missing evidence count: `5`;
- process control authorized: `false`;
- no process action taken: `true`.

Critical missing evidence:

- `clean_evidence_clock_start`;
- `official_settled_clean_sample`;
- `execution_realism_clean_rows`;
- `faithful_row_for_row_replay_parity`;
- `basis_mismatch_prospective_watch`.

The manifest keeps the current BTC1H result in the right box: research-control
only, not deployable or near-deployable. Current artifacts cannot satisfy the
remaining promotion evidence requirements. The strict-filtered basis mismatch
is recorded as a prospective watch condition only, not a fitted trading guard.

Refresh/validation:

- `btc1h_remaining_evidence_manifest` is now step `58 / 63`, after objective
  completion and before forward evidence reporting;
- bounded refresh from `btc1h_promotion_gap_matrix` through
  `gpt_pro_action_status` passed all `11 / 11` selected read-only steps;
- focused pytest passed: `9 passed`.

## Faithful Replay Data Contract

Artifacts:

- `backtest_outputs\btc1h_faithful_replay_data_contract_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_replay_data_contract_latest_codex`.

Added `scripts\build_btc1h_faithful_replay_data_contract.py`. It consolidates
the exact sidecar fields required for future row-for-row BTC1H replay parity
from the current sidecar schema, clean-clock gate, repair prerequisite audit,
repair target matrix, repair attempt audit, and replay-vs-ledger summary.

Current summary:

- contract status: `BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
- required fields: `56`;
- present required fields: `39`;
- missing required fields: `17`;
- missing required tables: `order_decision;signal_scan`;
- current artifacts can support faithful replay: `false`;
- current replay promotion usable: `false`;
- deployable now: `false`;
- near-deployable now: `false`;
- requires explicit authorization: `true`;
- requires process control: `true`;
- no process action taken: `true`.

Missing required fields:

- `signal_scan.signal_strategy`;
- `signal_scan.model_ttl_policy`;
- `signal_scan.model_policy_version`;
- `signal_scan.edge_threshold_cents`;
- `signal_scan.spread_cents`;
- `signal_scan.top_visible_qty`;
- `signal_scan.quote_received_at_ns`;
- `signal_scan.quote_age_ms`;
- `signal_scan.ttl_min`;
- `signal_scan.close_time`;
- `signal_scan.btc_candle_time`;
- `signal_scan.btc_candle_age_sec`;
- `signal_scan.btc_rv60`;
- `signal_scan.btc_ret_10m_usd`;
- `order_decision.signal_strategy`;
- `order_decision.model_ttl_policy`;
- `order_decision.model_policy_version`.

This makes the faithful-replay blocker concrete: the current sidecar has many
rows (`134648` signal scans, `458458` top-of-book rows), but the fields needed
to separate clean scan-time policy rows and replay exact model inputs are not
there. The remaining-evidence manifest now surfaces this contract directly:

- `faithful_replay_data_contract_status =
  BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
- `faithful_replay_missing_required_field_count = 17`;
- `faithful_replay_current_artifacts_can_support = False`.

Refresh/validation:

- `btc1h_faithful_replay_data_contract` is now step `40 / 64`, after replay
  repair attempt and before official basis mismatch;
- bounded refresh from `btc1h_replay_repair_attempt_audit` through
  `gpt_pro_action_status` passed all `26 / 26` selected read-only steps;
- focused pytest passed: `11 passed`.

## Replay Source-Contract Readiness

Artifacts:

- `backtest_outputs\btc1h_replay_source_contract_readiness_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_source_contract_latest_codex`.

Patched `scripts\materialize_btc_replay_sidecar.py` so sidecar
materialization now preserves:

- `ws_lifecycle.open_ts`;
- `ws_lifecycle.close_ts`;
- `order_decision.side`.

Added `scripts\build_btc1h_replay_source_contract_readiness.py`, a static
source/materializer audit for the `56`-field faithful-replay contract. Current
summary:

- source contract status:
  `SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`;
- required fields: `56`;
- source-ready fields: `56`;
- source missing fields: `0`;
- capture schema missing count: `0`;
- recorder payload missing count: `0`;
- materializer sidecar column missing count: `0`;
- materializer table column missing count: `0`;
- current source contract ready: `true`;
- current artifacts can support faithful replay: `false`;
- current rows remain blocked until clean clock: `true`;
- deployable now: `false`;
- near-deployable now: `false`;
- no process action taken: `true`.

The remaining-evidence manifest now separates these two facts:

- source/materializer readiness is clean:
  `replay_source_contract_ready = True`;
- current-row evidence is still blocked:
  `faithful_replay_current_artifacts_can_support = False`.

Refresh/validation:

- `btc1h_replay_source_contract_readiness` is now step `41 / 66`, after the
  faithful replay data contract and before official basis mismatch;
- bounded refresh from `btc1h_faithful_replay_data_contract` through
  `gpt_pro_action_status` passed all `26 / 26` selected read-only steps;
- focused pytest passed: `13 passed`.

## Clean-Clock Collection Preflight

Artifacts:

- `backtest_outputs\btc1h_clean_clock_collection_preflight_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_clean_preflight_latest_codex`.

Added `scripts\build_btc1h_clean_clock_collection_preflight.py`, a read-only
handoff artifact that separates "prepared enough to ask for explicit guarded
paper-shadow authorization" from "has clean collection evidence." Current
summary:

- preflight status: `READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
- ready for authorization: `true`;
- collection evidence ready: `false`;
- source contract ready: `true`;
- current artifacts can support faithful replay: `false`;
- clean evidence clock ready: `false`;
- restart authorization status: `NEEDS_REVIEW_BEFORE_START_RESTART`;
- user permission required: `true`;
- restart path status: `PASS_RESTART_PATH_READY`;
- fresh capture replay schema status: `PASS_REPLAY_SIDECAR_SCHEMA`;
- post-restart gate status: `PENDING_CONTROLLED_RESTART`;
- post-restart official rows: `0 / 50`;
- promotion collection ready: `false`;
- deployable now: `false`;
- near-deployable now: `false`;
- process control authorized: `false`;
- no process action taken: `true`.

Checklist verdicts:

- `source_contract_ready`: `PASS_SOURCE_READY`;
- `current_rows_faithful_replay_contract`:
  `BLOCKED_CURRENT_ROWS_NOT_FAITHFUL`;
- `clean_evidence_clock_state`: `BLOCKED_CLEAN_CLOCK_NOT_READY`;
- `restart_path_preflight`: `PASS_PREP_READY`;
- `explicit_authorization`: `BLOCKED_AUTHORIZATION_REQUIRED`;
- `post_restart_official_sample`: `BLOCKED_NO_POST_RESTART_ROWS`;
- `deployability_verdict`: `BLOCKED_NO_DEPLOY`.

The remaining-evidence manifest now consumes this preflight and records:

- `clean_clock_collection_preflight_status =
  READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
- `clean_clock_collection_preflight_ready_for_authorization = True`;
- `clean_clock_collection_preflight_collection_evidence_ready = False`;
- `clean_clock_collection_preflight_process_control_authorized = False`.

Refresh/validation:

- `btc1h_clean_clock_collection_preflight` is now step `42 / 66`, after source
  contract readiness and before official basis mismatch;
- bounded refresh from `btc1h_faithful_replay_data_contract` through
  `gpt_pro_action_status` passed all `27 / 27` selected read-only steps;
- focused pytest passed: `11 passed`.

Interpretation: BTC1H is source/preflight-ready for an explicit authorization
decision, but it has `0` clean post-restart official rows. The current
candidate remains research/control only, not deployable or near-deployable.

## Forward Packet Preflight Alignment

Artifacts:

- `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_packet_preflight_latest_codex`.

Patched `scripts\build_btc1h_next_forward_candidate_packet.py` so the
next-forward packet consumes the clean-clock collection preflight. The packet
now distinguishes BTC1H-specific authorization-review readiness from actual
promotion evidence.

Current packet `run_info.json`:

- packet status: `READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
- clean-clock collection preflight status:
  `READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
- clean-clock collection ready for authorization: `true`;
- clean-clock collection evidence ready: `false`;
- clean-clock collection process control authorized: `false`;
- restart authorization ready: `false`;
- deployable now: `false`;
- replay promotion-usable modes: `0 / 6`.

The refreshed `btc1h_current_evidence_snapshot.csv` still preserves the
research-positive but non-deployable shape:

- fixed holdouts: `14 / 14`;
- live-WS cadences: `6 / 6`;
- stale official rows/PnL: `11` / `+$0.50`;
- proxy/official mismatch rate: `0.0909`;
- replay repair attempt verdict: `REGRESSES_ROW_FIDELITY`;
- clean clock: `BLOCKED_CONTROLLED_RESTART_REQUIRED`.

Downstream objective audit after the refresh:

- objective complete: `false`;
- deployable candidates: `0`;
- near-deployable candidates: `0`;
- packet status: `READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
- critical blocked requirements:
  `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.

Refresh/validation:

- bounded refresh from `btc1h_next_forward_candidate_packet` through
  `gpt_pro_action_status` passed all `13 / 13` selected read-only steps;
- focused pytest passed: `8 passed`.

Interpretation: the active BTC1H control is still the only frozen
next-clean-clock candidate. It is ready for an authorization-review decision,
but it remains neither deployable nor near-deployable, and no process control
was taken.

## Holdout Provenance Audit

Artifacts:

- `backtest_outputs\btc1h_multi_holdout_research_latest_codex\btc1h_holdout_provenance.csv`;
- `backtest_outputs\btc1h_multi_holdout_research_latest_codex\btc1h_holdout_provenance_summary.csv`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_holdout_provenance_latest_codex`.

Patched `scripts\build_btc1h_multi_holdout_research.py` so each fixed
holdout row is classified by evidence family and current use:

- `historical_predexon_research`;
- `live_ws_replay_research`;
- `official_forward_shadow`.

Current provenance summary:

- `high_conf_80_entry70_no_chase`:
  - holdout rows: `16`;
  - research-countable rows: `16`;
  - live-WS stability rows: `6`;
  - official-forward diagnostic rows: `1`;
  - official-forward trades: `11`;
  - official/proxy mismatch rows: `1`;
  - near-deployable countable rows: `0`;
  - deployable countable rows: `0`;
  - status: `RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY`.
- `high_conf_80_entry59_70_no_chase`:
  - holdout rows: `15`;
  - live-WS stability rows: `6`;
  - official-forward diagnostic rows: `0`;
  - status: `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.
- `high_conf_80`:
  - holdout rows: `11`;
  - official-forward diagnostic rows: `0`;
  - negative research holdouts:
    `D0_predexon_mar17_24_old_context;H3_predexon_may03_06_external`;
  - status: `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.
- `high_conf_80_no_chase`:
  - holdout rows: `18`;
  - official-forward diagnostic rows: `0`;
  - negative research holdout: `H4_live_ws_may06_12_stride1s`;
  - status: `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.

The active-control official row is now labeled
`official_forward_diagnostic_only` with blockers including too few official
rows, official/proxy mismatch rate above the limit, row-unfaithful replay,
missing exact cached TTL capture, and cached-TTL-only fills. It does not count
toward near-deployable or deployable evidence.

Refresh/validation:

- bounded refresh from `btc1h_multi_holdout_research` through
  `gpt_pro_action_status` passed all `36 / 36` selected read-only steps;
- focused pytest passed: `12 passed`.

Interpretation: the multi-holdout evidence is now machine-separated into
research evidence, live-WS stability evidence, and official diagnostics. There
are still `0` promotion-countable BTC1H holdout rows.

## Official PnL Path Audit

Artifacts:

- `backtest_outputs\btc1h_official_pnl_path_latest_codex\btc1h_official_pnl_path_sequence.csv`;
- `backtest_outputs\btc1h_official_pnl_path_latest_codex\btc1h_official_pnl_path_summary.csv`;
- `backtest_outputs\btc_evidence_stack_refresh_btc1h_pnl_path_latest_codex`.

Added `scripts\build_btc1h_official_pnl_path_audit.py` and wired it into the
refresh tail between the strict execution-filter basis mismatch audit and the
objective completion audit.

Current BTC1H official path:

- official rows: `11`;
- official PnL after fees: `+$0.50`;
- proxy PnL on the same rows: `+$1.50`;
- official-minus-proxy PnL: `-$1.00`;
- official win rate: `0.727273`;
- proxy/official mismatches: `1`;
- proxy/official mismatch rate: `0.090909`;
- max drawdown: `-$1.76`;
- drawdown/PnL ratio: `3.52`;
- max drawdown market: `KXBTCD-26MAY2009-T77499.99`;
- max single loss: `-$0.71`;
- rows with quote age `>90ms`: `3`;
- max quote age: `260.9622ms`.

The path status is
`DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE`, and
`current_rows_count_for_promotion = false`. Blockers include fewer than `50`
clean official rows, proxy/official mismatch rate above the limit, rows from
the pre-clean-clock ledger, no authorized clean collection clock, missing
faithful replay parity, and execution-realism gates still outstanding.

Downstream objective summary now carries:

- `official_pnl_path_rows = 11`;
- `official_pnl_path_official_pnl = 0.5`;
- `official_pnl_path_max_drawdown = -1.76`;
- `official_pnl_path_drawdown_to_pnl_ratio = 3.52`;
- `official_pnl_path_proxy_official_mismatches = 1`;
- `official_pnl_path_current_rows_count_for_promotion = false`.

Refresh/validation:

- bounded refresh from `btc1h_execution_filtered_basis_mismatch_audit` through
  `gpt_pro_action_status` passed all `9 / 9` selected read-only steps;
- focused pytest passed: `9 passed`.

Interpretation: BTC1H finally has a row-level official PnL path artifact, but
the positive `+$0.50` summary does not survive promotion scrutiny because the
path has a `-$1.76` drawdown, a `9.09%` proxy/official mismatch rate, and no
clean evidence clock. No process control was taken.

## Strict Execution-Filtered PnL Path

Artifact:

- `backtest_outputs\btc1h_official_pnl_path_latest_codex\btc1h_official_pnl_path_strict_execution_sequence.csv`.

The official path audit now also sequences the strict execution-filtered
subset from
`backtest_outputs\btc1h_execution_filtered_basis_mismatch_latest_codex\btc1h_execution_filtered_basis_strict_rows.csv`.

Strict execution-filtered path:

- rows: `10`;
- official PnL after fees: `+$0.20`;
- proxy PnL on the same rows: `+$1.20`;
- official-minus-proxy PnL: `-$1.00`;
- max drawdown: `-$1.76`;
- drawdown/PnL ratio: `8.8`;
- proxy/official mismatches: `1`;
- rows with quote age `>90ms`: `2`;
- removed market: `KXBTCD-26MAY2106-T77699.99`.

Objective audit fields added:

- `official_pnl_path_strict_execution_rows = 10`;
- `official_pnl_path_strict_execution_official_pnl = 0.2`;
- `official_pnl_path_strict_execution_max_drawdown = -1.76`;
- `official_pnl_path_strict_execution_proxy_official_mismatches = 1`;
- `official_pnl_path_strict_execution_current_rows_count_for_promotion =
  false`.

Refresh/validation:

- bounded refresh from `btc1h_execution_filtered_basis_mismatch_audit` through
  `gpt_pro_action_status` passed all `9 / 9` selected read-only steps;
- focused pytest passed: `9 passed`.

Interpretation: the strict execution subset removes one positive official row
and leaves the same max drawdown plus the same proxy/official mismatch. This
confirms the execution-realism sensitivity does not rescue the candidate.

## Holdout Independence Feeds Priority

The research priority matrix now consumes
`backtest_outputs\btc1h_holdout_independence_audit_latest_codex\btc1h_holdout_independence_summary.csv`.

Active BTC1H control independence facts:

- pooled historical/proxy rows: `159`;
- unique market-side rows: `69`;
- duplicate market-side rows: `90`;
- pooled stressed historical/proxy PnL: `+$21.39`;
- unique market-side stressed PnL: `+$5.82`;
- historical leave-one-event minimum PnL: `+$19.41`;
- forward official stale leave-one-event minimum PnL: `+$0.09`;
- status: `WEAK_OR_CONCENTRATED_RESEARCH`;
- blocker: `duplicate_market_side_rows`.

The active priority row and objective candidate row now include:

- `holdout_independence_status = WEAK_OR_CONCENTRATED_RESEARCH`;
- `historical_unique_market_side_rows = 69`;
- `historical_duplicate_market_side_rows = 90`;
- `historical_unique_market_side_pnl = 5.82`;
- `active_historical_holdouts_have_duplicate_market_side_rows`;
- `active_historical_independence_weak_or_concentrated`.

Refresh/validation:

- bounded refresh from `btc1h_holdout_independence_audit` through
  `gpt_pro_action_status` passed all `20 / 20` selected read-only steps;
- `deployment_readiness` returned expected code `1` because no strategy is
  production-ready;
- focused pytest passed: `8 passed`.

Interpretation: the active control is still research-positive after
market-side de-duplication, but the edge is much smaller and clearly
concentrated. This is a research caveat and an additional no-deploy blocker,
not a reason to change live policy.

## Promising Set Split

The objective audit now separates the raw multi-holdout promising set from the
stricter priority-ranked research set.

Current summary:

- `priority_research_candidates = 3`;
- `multi_holdout_promising_candidates = 3`;
- priority-ranked research candidates:
  `high_conf_80_entry70_no_chase;high_conf_80_entry59_70_no_chase;high_conf_80_no_chase`;
- raw multi-holdout promising variants:
  `high_conf_80_entry70_no_chase;high_conf_80_entry59_70_no_chase;high_conf_80`;
- raw multi-holdout promising but low priority:
  `high_conf_80`;
- priority watchlist but not raw multi-holdout promising:
  `high_conf_80_no_chase`.

Candidate status:

- `high_conf_80_entry70_no_chase`:
  `promising_research_control_blocked`;
- `high_conf_80_entry59_70_no_chase`:
  `promising_research_runner_up_not_independent`;
- `high_conf_80_no_chase`:
  `basis_watchlist_not_forward_validated`;
- `high_conf_80`:
  `low_priority_or_rejected`.

Refresh/validation:

- bounded refresh from `btc1h_objective_completion_audit` through
  `gpt_pro_action_status` passed all `7 / 7` selected read-only steps;
- focused pytest passed: `1 passed`.

Interpretation: the headline research count is not enough by itself. The
identity of the three candidates changes depending on whether we use the raw
multi-holdout flag or the stricter priority matrix, so future summaries should
name the actual variants rather than only reporting a count.

## Available Data Coverage Manifest

Artifact:

- `backtest_outputs\btc1h_remaining_evidence_manifest_latest_codex\btc1h_available_data_coverage.csv`.

The remaining-evidence manifest now summarizes the known BTC1H data classes
directly, instead of leaving that distinction split across provenance,
replay-contract, and clean-clock artifacts.

Current known available data classes:

- `historical_proxy_research_only`;
- `live_ws_stability_research_only`;
- `official_forward_diagnostic_only`;
- `current_btc1h_replay_coverage_audit`;
- `current_sidecar_and_snapshot_replay_artifacts`;
- `checked_out_source_and_materializer_for_future_rows`.

Current promotion-countable classes: none.

Coverage facts:

- known available near-deployable-countable rows: `0`;
- known available deployable-countable rows: `0`;
- `known_available_data_can_make_near_deployable = false`;
- `no_known_available_data_class_can_make_near_deployable = true`;
- data statuses:
  `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE;RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY;BLOCKED_REPLAY_COVERAGE;BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS;SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`.

Interpretation: the current data inventory is now explicit. Historical/proxy
and live-WS rows support research only, old official-forward rows are
diagnostic only, current replay artifacts are not faithful-replay capable, and
source readiness only means future rows can be collected after authorization.
No known available BTC1H data class can make the active control deployable or
near-deployable today.

Refresh/validation:

- bounded refresh from `btc1h_remaining_evidence_manifest` through
  `gpt_pro_action_status` passed all `6 / 6` selected read-only steps;
- focused pytest passed: `2 passed`.

## Objective Audit Available Data Scope

The top-level objective audit now has its own
`available_data_scope_and_countability` requirement so the final objective
verdict does not depend on reading the remaining-evidence manifest separately.

Current status:

- `available_data_scope_and_countability =
  PASS_SCOPED_NO_PROMOTION_USABLE_AVAILABLE_DATA`;
- known available near-deployable-countable rows: `0`;
- known available deployable-countable rows: `0`;
- promotion-countable available data classes: none;
- `known_available_data_can_make_near_deployable = false`;
- `known_available_data_can_make_deployable = false`;
- faithful-replay data contract:
  `BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
- replay source contract:
  `SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`.

The objective summary now includes the available data classes:

- `historical_proxy_research_only`;
- `live_ws_stability_research_only`;
- `official_forward_diagnostic_only`;
- `current_btc1h_replay_coverage_audit`;
- `current_sidecar_and_snapshot_replay_artifacts`;
- `checked_out_source_and_materializer_for_future_rows`.

Refresh/validation:

- bounded refresh from `btc1h_objective_completion_audit` through
  `gpt_pro_action_status` passed all `7 / 7` selected read-only steps;
- focused pytest passed: `10 passed`.

Interpretation: the main objective audit now says, directly, that current
available BTC1H data can only support research diagnostics. The final verdict
remains unchanged: `0` deployable and `0` near-deployable BTC1H candidates.

## Candidate-Level Gate Columns

The objective candidate table now exposes promotion-countability and gate
blockers per variant, not only the aggregate objective verdict.

Current candidate statuses:

| Variant | Promotion evidence status | Available data status |
| --- | --- | --- |
| `high_conf_80_entry70_no_chase` | `NO_PROMOTION_COUNTABLE_AVAILABLE_DATA` | `RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY` |
| `high_conf_80_entry59_70_no_chase` | `NO_PROMOTION_COUNTABLE_AVAILABLE_DATA` | `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE` |
| `high_conf_80_no_chase` | `NO_PROMOTION_COUNTABLE_AVAILABLE_DATA` | `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE` |
| `high_conf_80` | `NO_PROMOTION_COUNTABLE_AVAILABLE_DATA` | `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE` |

For the active control, the candidate row now marks:

- `official_settlement_gate_blocked = true`;
- `execution_realism_gate_blocked = true`;
- `faithful_replay_gate_blocked = true`;
- `clean_evidence_clock_gate_blocked = true`;
- `statistical_or_basis_caveat_active = true`.

Refresh/validation:

- bounded refresh from `btc1h_objective_completion_audit` through
  `gpt_pro_action_status` passed all `7 / 7` selected read-only steps;
- focused pytest passed: `10 passed`.

Interpretation: the no-near-deployable conclusion is now visible per row.
Every current BTC1H candidate remains research-only or diagnostic-only; no
variant has promotion-countable available data.

## Candidate Holdout Detail Columns

The objective candidate table now also names the failing holdout or cadence
buckets so the multiple-holdout evidence is visible at the same layer as the
deployability verdict.

| Variant | Historical holdouts | WS cadences | Negative bucket | Readiness status |
| --- | --- | --- | --- | --- |
| `high_conf_80_entry70_no_chase` | `14 / 14` | `6 / 6` | none | `promising_but_blocked_by_official_or_fidelity_gates` |
| `high_conf_80_entry59_70_no_chase` | `12 / 13` | `6 / 6` | `H2b_direct_apr23_may01_holdout` | `historical_promising_needs_forward_official_evidence` |
| `high_conf_80_no_chase` | `13 / 14` | `5 / 6` | `H4_live_ws_may06_12_stride1s` | `research_watch_or_reject` |
| `high_conf_80` | `8 / 9` | `6 / 6` | `H3_predexon_may03_06_external` | `historical_promising_needs_forward_official_evidence` |

Refresh/validation:

- bounded refresh from `btc1h_objective_completion_audit` through
  `gpt_pro_action_status` passed all `7 / 7` selected read-only steps;
- focused pytest passed: `10 passed`.

Interpretation: the active control is the only BTC1H candidate with all listed
historical and live-WS research buckets positive, but those buckets remain
research/diagnostic evidence only. The other research/watchlist variants now
show their specific failing holdout or cadence in the objective table itself.

## Replay Repair Feasibility in Objective Audit

The objective audit now surfaces the replay-repair prerequisite artifacts
directly in `faithful_live_replay_gate`, rather than only pointing at the
promotion-gap summary.

Current replay-repair status:

- `replay_repair_blocker_status =
  BLOCKED_FUTURE_EXACT_INPUT_REQUIRED`;
- `repair_target_count = 4`;
- `existing_snapshot_true_target_count = 2`;
- `existing_snapshot_partial_target_count = 1`;
- `existing_snapshot_false_target_count = 1`;
- snapshot-repairable targets:
  `order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
- partial target: `same_event_market_selection`;
- not repairable from the current snapshot: `skip_then_fill_sequence`;
- future exact-input blocked targets:
  `same_event_market_selection;skip_then_fill_sequence`.

The faithful replay gate now records that the current independent replay has
`9 / 11` exact market-side matches, `10` replay rows, and `+0.61` replay-minus
actual PnL drift. The best current snapshot attempt remains
`matched_drift_decision_fill_price_patch_simulation`, but it is still
diagnostic only because the remaining blockers are
`missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-replay-repair-objective`
  -> `8 passed`;
- bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_replay_repair_objective_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status --skip-packet`
  -> `7 / 7` selected read-only steps passed.

Interpretation: current BTC1H replay engineering can still test two diagnostic
drift repairs on the paused snapshot, but current data cannot complete faithful
row-for-row replay for the active candidate. The objective remains incomplete:
`0` deployable candidates, `0` near-deployable candidates, and the hard blockers
are still official settlement, execution realism, faithful replay, and the
clean evidence clock. No process control was taken.

## Root-Cause Field Gaps in Faithful Replay Contract

The faithful replay data contract now maps each replay failure target to the
specific missing capture fields needed for independent row-for-row replay.

Current target-level field gap table:

| Repair target | Snapshot repair status | Missing target fields | Future exact-input required | Promotion-usable now |
| --- | --- | ---: | --- | --- |
| `skip_then_fill_sequence` | `False` | `8` | `True` | `False` |
| `same_event_market_selection` | `Partial` | `8` | `True` | `False` |
| `order_decision_reprice_fill_price` | `True` | `0` | `False` | `False` |
| `blocked_dedupe_scan_clock` | `True` | `0` | `False` | `False` |

The missing target fields are the exact model-input/policy fields absent from
the current `signal_scan` sidecar:

`signal_scan.model_ttl_policy;signal_scan.model_policy_version;signal_scan.ttl_min;signal_scan.close_time;signal_scan.btc_candle_time;signal_scan.btc_candle_age_sec;signal_scan.btc_rv60;signal_scan.btc_ret_10m_usd`.

The objective audit now carries these values:

- `faithful_replay_root_cause_field_gap_rows = 4`;
- `faithful_replay_root_cause_future_exact_input_target_count = 2`;
- `faithful_replay_root_cause_target_missing_required_field_count = 16`;
- `faithful_replay_root_cause_target_repairs_can_make_promotion_usable_now =
  False`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-field-gap-contract`
  -> `10 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_field_gap_contract_latest_codex --start-at btc1h_faithful_replay_data_contract --stop-after gpt_pro_action_status --skip-packet`
  -> `28 / 28` selected read-only steps passed.

Interpretation: two replay repairs are still worth testing diagnostically on
the current paused snapshot, but the two target-selection/sequence failures
cannot be rescued from current data because the exact scan-time model input and
TTL fields were not captured. This tightens the faithful-backtesting blocker
without relaxing any deployability gate. No process control was taken.

## Residual Repair Target Ledger

The replay repair attempt audit now writes
`btc1h_replay_repair_residual_targets.csv`, which keeps diagnostic replay
patches separate from unresolved future-input replay blockers.

Current residual target status:

| Repair target | Residual status | Attempted on current snapshot | Future exact input required | Promotion-usable |
| --- | --- | --- | --- | --- |
| `skip_then_fill_sequence` | `UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED` | `False` | `True` | `False` |
| `same_event_market_selection` | `UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED` | `False` | `True` | `False` |
| `order_decision_reprice_fill_price` | `DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE` | `True` | `False` | `False` |
| `blocked_dedupe_scan_clock` | `DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE` | `True` | `False` | `False` |

The objective audit now carries:

- `replay_repair_residual_target_count = 4`;
- `replay_repair_diagnostic_patch_applied_target_count = 2`;
- `replay_repair_unresolved_future_exact_input_target_count = 2`;
- `replay_repair_diagnostic_patch_applied_targets =
  order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
- `replay_repair_unresolved_future_exact_input_targets =
  skip_then_fill_sequence;same_event_market_selection`;
- `replay_repair_targets_repaired_to_promotion_usable_count = 0`;
- `replay_repair_all_field_ready_repairs_remain_diagnostic_only = True`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_replay_repair_attempt_audit.py scripts\test_btc1h_replay_repair_attempt_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_replay_repair_attempt_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-residual-repair`
  -> `11 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_residual_repair_latest_codex --start-at btc1h_replay_repair_attempt_audit --stop-after gpt_pro_action_status --skip-packet`
  -> `29 / 29` selected read-only steps passed.

Interpretation: the current paused snapshot is useful for replay-engineering
unit work, but it still cannot support a faithful independent BTC1H backtest
that counts toward deployability. Even after applying the two field-ready
diagnostic repairs, the remaining replay blockers include missing actual rows,
row-count drift, extra replay rows, event-level replacements, and nonzero PnL
drift. No process control was taken.

## Priority Matrix Uses Residual Replay Blockers

The BTC1H research priority matrix now carries the residual replay repair
counts directly on the active control row and in
`btc1h_research_priority_summary.csv`.

Current active control replay blockers:

`current_snapshot_repairs_not_promotion_usable;selected_scan_attempt_regresses_row_fidelity;unresolved_future_exact_input_replay_targets;field_ready_replay_repairs_diagnostic_only;zero_replay_targets_repaired_to_promotion_usable`.

Current ranking-layer replay fields:

- `replay_repair_residual_target_count = 4`;
- `replay_repair_diagnostic_patch_applied_target_count = 2`;
- `replay_repair_unresolved_future_exact_input_target_count = 2`;
- `replay_repair_diagnostic_patch_applied_targets =
  order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
- `replay_repair_unresolved_future_exact_input_targets =
  skip_then_fill_sequence;same_event_market_selection`;
- `replay_repair_targets_repaired_to_promotion_usable_count = 0`;
- `replay_repair_all_field_ready_repairs_remain_diagnostic_only = True`.

The candidate ranking remains:

- active forward control: `high_conf_80_entry70_no_chase`;
- top causal replay runner-up:
  `high_conf_80_entry59_70_no_chase`;
- top basis-stress variant: `high_conf_80_no_chase`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_research_priority_matrix.py scripts\test_btc1h_research_priority_matrix.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_research_priority_matrix.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-priority-residual`
  -> `9 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_priority_residual_latest_codex --start-at btc1h_research_priority_matrix --stop-after gpt_pro_action_status --skip-packet`
  -> `15 / 15` selected read-only steps passed.

Interpretation: the ranking layer now agrees with the replay residual ledger:
the active control is still the right research/forward-control candidate, but
its replay repairs are not even near-promotion-usable. Current objective
verdict remains `0` deployable and `0` near-deployable. No process control was
taken.

## Candidate Packet Carries Residual Replay Evidence

The BTC1H next-forward candidate packet now carries residual replay target
fields in the current-evidence snapshot, active-control decision row, report,
and `run_info.json`.

Current packet replay fields:

- `replay_repair_residual_target_count = 4`;
- `replay_repair_diagnostic_patch_applied_target_count = 2`;
- `replay_repair_unresolved_future_exact_input_target_count = 2`;
- `replay_repair_diagnostic_patch_applied_targets =
  order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
- `replay_repair_unresolved_future_exact_input_targets =
  skip_then_fill_sequence;same_event_market_selection`;
- `replay_repair_targets_repaired_to_promotion_usable_count = 0`;
- `replay_repair_all_field_ready_repairs_remain_diagnostic_only = True`.

The packet still freezes only `high_conf_80_entry70_no_chase` as the forward
control for a possible clean evidence clock after explicit authorization. It
does not call the candidate deployable or near-deployable, and it keeps
collection evidence separate from authorization review:

- `packet_status = READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
- `deployable_now = False`;
- `current_rows_count_for_promotion = False`;
- `collection_evidence_ready = False`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-packet-residual`
  -> `8 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_packet_residual_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
  -> `14 / 14` selected read-only steps passed.

Interpretation: the handoff packet now matches the strict replay residual
verdict. It can identify the active BTC1H forward-control candidate, but it
also states that the existing replay repair work has repaired `0` targets to
promotion usability. No process control was taken.

## Clean Preflight Defers To Restart Authorization

The BTC1H clean-clock collection preflight now treats the guarded restart
authorization packet as a hard input before saying the path is ready for
authorization review.

Current refreshed preflight status:

- `preflight_status = READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
- `ready_for_authorization = True`;
- `collection_evidence_ready = False`;
- `restart_authorization_status = READY_FOR_USER_AUTHORIZATION_TO_START`;
- `restart_authorization_packet_ready = True`;
- `pre_authorization_blockers = <blank>`;
- `target_process_count = 0`;
- `target_process_running = False`;
- `target_process_hygiene_status = NOT_RUNNING`;
- `expected_process_state = start_or_restart_allowed`;
- `observed_process_action = start_absent_target`;
- `forward_status_fresh = True`.

The regenerated downstream artifacts now carry the stricter status:

- remaining-evidence manifest:
  `clean_clock_collection_preflight_status =
  READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
- next-forward candidate packet:
  `clean_clock_collection_ready_for_authorization = True`,
  `shadow_running = False`,
  `restart_authorization_ready = True`;
- objective audit:
  `packet_status = READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_clean_clock_collection_preflight.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\build_btc1h_objective_completion_audit.py scripts\build_btc1h_next_forward_candidate_packet.py scripts\check_btc_deployment_readiness.py scripts\test_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-clean-preflight-auth-slice`
  -> `14 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_clean_preflight_auth_latest_codex --start-at btc1h_clean_clock_collection_preflight --stop-after gpt_pro_action_status --skip-packet`
  -> `26 / 26` selected read-only steps passed.

Interpretation: the source/schema path and guarded start/restart packet are
ready for explicit authorization review, but this is still not collection
evidence. BTC1H is not collecting clean rows right now because the paper shadow
is absent, and no current row counts toward promotion. No process control was
taken.

## Restart Packet Separates Expected State From Observed Action

The restart authorization packet now reports both the configured target-process
expectation and the action the guarded script would take from the current
observed process state.

Current BTC1H row:

- `authorization_packet_status = READY_FOR_USER_AUTHORIZATION_TO_START`;
- `pre_authorization_blockers = <blank>`;
- `process_count = 0`;
- `process_hygiene_status = NOT_RUNNING`;
- `expected_process_state = start_or_restart_allowed`;
- `observed_process_action = start_absent_target`;
- `will_stop_existing_processes = False`;
- `will_start_process = True`;
- `will_restart_process = False`;
- `will_start_new_process = True`.

The clean-clock collection preflight carries the same distinction:

- `preflight_status = READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
- `restart_authorization_packet_ready = True`;
- `expected_process_state = start_or_restart_allowed`;
- `observed_process_action = start_absent_target`;
- `will_restart_process = False`;
- `will_start_new_process = True`.

Refresh/validation:

- `python -m py_compile scripts\build_btc_restart_authorization_packet.py scripts\test_btc_paper_restart_safety.py scripts\build_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_clean_clock_collection_preflight.py`;
- `python -m pytest scripts\test_btc_paper_restart_safety.py scripts\test_btc1h_clean_clock_collection_preflight.py -q --basetemp .pytest-codex-tmp-btc1h-start-ready-auth-final`
  -> `13 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_start_ready_auth_latest_codex --start-at restart_authorization_packet --stop-after gpt_pro_action_status --skip-packet`
  -> `56 / 56` selected read-only steps passed.

Interpretation: the authorization review path is ready, but the evidence clock
is still blocked until explicit user authorization is given and future rows are
collected. The current machine state would require starting absent paper
shadows, not merely restarting existing ones. No process control was taken.

## Packet and Objective Carry Start-Ready State

The BTC1H next-forward packet and objective audit now surface the same
start-ready/no-evidence distinction from the clean-clock preflight.

Current next-forward packet fields:

- `packet_status = READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
- `restart_authorization_status = READY_FOR_USER_AUTHORIZATION_TO_START`;
- `restart_authorization_packet_ready = True`;
- `expected_process_state = start_or_restart_allowed`;
- `observed_process_action = start_absent_target`;
- `will_start_new_process = True`;
- `will_restart_process = False`;
- `authorization_ready_but_collection_evidence_false = True`;
- `clean_clock_collection_evidence_ready = False`;
- `no_process_action_taken = True`.

The objective audit carries those fields too and appends the process-state
details to `clean_evidence_clock_gate` evidence. The objective verdict remains:

- `current_verdict =
  objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
- `deployable_candidates = 0`;
- `near_deployable_candidates = 0`;
- critical blockers:
  `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
- `python -m pytest scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-packet-objective-start-state`
  -> `2 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_packet_objective_start_state_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
  -> `14 / 14` selected read-only steps passed.

Interpretation: the packet is ready for authorization review, but this is still
not collection evidence. The next operational move would be starting absent
paper shadows only after explicit authorization, and no current BTC1H row counts
toward promotion.

## Faithful Replay Field Counts Clarified

The faithful replay data contract now separates three related counts that were
easy to confuse:

- `missing_required_field_count = 17`: all missing required sidecar fields in
  the current contract;
- `root_cause_target_missing_required_field_count = 16`: summed missing-field
  occurrences across the replay root-cause targets;
- `root_cause_target_unique_missing_required_field_count = 8`: unique
  target-level fields blocking the unresolved exact-input replay targets.

Those eight target fields are:

`signal_scan.model_ttl_policy;signal_scan.model_policy_version;signal_scan.ttl_min;signal_scan.close_time;signal_scan.btc_candle_time;signal_scan.btc_candle_age_sec;signal_scan.btc_rv60;signal_scan.btc_ret_10m_usd`.

The objective audit and remaining-evidence manifest now carry the same
distinction. Current status remains:

- `current_artifacts_can_support_faithful_replay = False`;
- `deployable_candidates = 0`;
- `near_deployable_candidates = 0`;
- `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py`;
- `python -m pytest scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_remaining_evidence_manifest.py -q --basetemp .pytest-codex-tmp-btc1h-faithful-count-clarity`
  -> `5 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_faithful_count_clarity_latest_codex --start-at btc1h_faithful_replay_data_contract --stop-after gpt_pro_action_status --skip-packet`
  -> `28 / 28` selected read-only steps passed.

Interpretation: this is a blocker-ledger clarification, not a promotion
improvement. The old snapshot still lacks exact model-input/TTL fields needed
to make the unresolved same-event-selection and skip-then-fill replay targets
faithful.

## Available Data Coverage Blockers

The remaining-evidence manifest now puts the blocker explanation directly on
each available-data row via `blockers`, not only the older
`blocker_summary`.

Current available-data coverage:

- all rows have `current_rows_count_for_promotion = False`;
- all rows have `can_make_near_deployable_now = False`;
- historical/proxy and live-WS holdout rows are research-only until
  clean-clock, official-settlement, execution-realism, and faithful-replay gates
  pass;
- replay coverage rows are only plumbing and cannot replace official clean rows
  or row-for-row replay parity;
- sidecar/snapshot replay artifacts remain blocked by missing required fields,
  missing clean policy identity, and non-promotion-usable replay repairs;
- source readiness is future collection capability after authorization, not
  current evidence.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py`;
- `python -m pytest scripts\test_btc1h_remaining_evidence_manifest.py -q --basetemp .pytest-codex-tmp-btc1h-coverage-blockers`
  -> `2 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_available_data_blockers_latest_codex --start-at btc1h_remaining_evidence_manifest --stop-after gpt_pro_action_status --skip-packet`
  -> `6 / 6` selected read-only steps passed.

Interpretation: the current available data is useful for research ranking, but
no class of it is promotion-countable or near-deployable-countable under the
original gates.

## Candidate Research-Only Verdicts

`backtest_outputs\btc1h_objective_completion_audit_latest_codex\btc1h_candidate_objective_status.csv`
now includes candidate-level fields that explain why each policy remains
research-only:

- `candidate_research_class`;
- `candidate_is_research_only`;
- `current_available_data_class`;
- `promotion_gate_failures`;
- `why_not_near_deployable`;
- `promotion_blocker_evidence_snapshot`;
- `promotion_blocker_evidence_sources`;
- `next_evidence_to_reconsider`.

Current candidate classes:

- `high_conf_80_entry70_no_chase`:
  `active_forward_control_research_only`; available data is
  `research_plus_official_diagnostic_only`.
- `high_conf_80_entry59_70_no_chase`:
  `causal_replay_runner_up_research_only`; needs evidence where it differs
  from `entry70` before any separate promotion path.
- `high_conf_80_no_chase`:
  `basis_robust_watchlist_research_only`; needs fresh causal replay and clean
  official forward rows.
- `high_conf_80`: `multi_holdout_historical_promising_research_only`, but it
  remains low-priority/rejected despite historical positives.

All four rows still say `promotion_countable_available_data = False` and
`candidate_promotion_evidence_status =
NO_PROMOTION_COUNTABLE_AVAILABLE_DATA`. The summary is unchanged:

- `objective_complete = False`;
- `deployable_candidates = 0`;
- `near_deployable_candidates = 0`;
- `promising_research_candidates = 3`;
- critical blockers =
  `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
- `python -m pytest scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-candidate-research-only`
  -> `1 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status`
  -> `7 / 7` selected read-only steps passed.

Interpretation: this makes the no-near-deployable conclusion auditable per
candidate. It does not create deployable or near-deployable evidence.

## Candidate Blocker Source Proof

The same candidate status CSV now links each candidate blocker verdict to the
artifact sources that prove it. The active control row currently shows:

- `available_data_status = RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY`;
- `near_deployable_countable_rows = 0`;
- `deployable_countable_rows = 0`;
- `forward_official_rows = 11`;
- `forward_official_pnl = 0.5`;
- `forward_proxy_mismatch_rate = 0.0909`;
- `replay_ledger_promotion_usable = False`;
- `replay_ledger_exact_match_rate = 0.818182`.

Per-row sources include:

- `btc1h_candidate_gate_summary.csv`;
- `btc1h_research_priority_matrix.csv`;
- `btc1h_holdout_provenance_summary.csv`;
- `btc1h_promotion_gap_matrix.csv`;
- `btc1h_official_pnl_path_summary.csv`;
- execution filter and basis-mismatch summaries;
- snapshot execution realism summary;
- replay coverage, faithful replay data contract, and replay repair summaries;
- `btc1h_next_forward_candidate_packet_latest_codex\run_info.json`.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
- `python -m pytest scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-source-backed-candidate-blockers`
  -> `1 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status`
  -> `7 / 7` selected read-only steps passed.

Interpretation: the conclusion is still negative, but now each candidate row
contains the evidence values and artifact list needed to audit that conclusion.

## Candidate Promotion Deficits

`backtest_outputs\btc1h_candidate_promotion_deficit_latest_codex\btc1h_candidate_promotion_deficits.csv`
now quantifies the gap from current research evidence to near-deployable status
for each BTC1H candidate.

Current summary:

- `candidates_current_artifacts_can_make_near_deployable = 0`;
- `candidates_with_no_promotion_countable_data = 4`;
- `current_clean_post_restart_official_rows = 0`;
- `clean_official_row_deficit = 50`;
- active `entry70_no_chase` proxy/official mismatch excess over the `2%` max
  is `0.0709`;
- active replay exact-match deficit is `0.181818`;
- active execution-field completeness deficit under the promotion gate is
  `1.0`;
- faithful replay still has `17` overall missing required fields and `8`
  unique target-level missing fields.

Current active-control blockers:

- no promotion-countable available data;
- too few clean official rows;
- proxy/official mismatch rate above limit;
- incomplete execution-realism fields;
- row-for-row replay not promotion-usable;
- missing faithful replay capture fields;
- missing clean policy identity rows.

Refresh/validation:

- `python -m py_compile scripts\build_btc1h_candidate_promotion_deficit_audit.py scripts\test_btc1h_candidate_promotion_deficit_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
- `python -m pytest scripts\test_btc1h_candidate_promotion_deficit_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-promotion-deficit`
  -> `8 passed`;
- bounded dependency refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status`
  -> `8 / 8` selected read-only steps passed, with
  `btc1h_candidate_promotion_deficit_audit` now integrated as step `62 / 69`.

Interpretation: this is a sharper negative result. No BTC1H candidate can be
called near-deployable from current artifacts.

## Research Loop

1. Keep the current 1H and 15m remote collectors/shadows running.
2. Refresh official settlement daily for the remote shadow ledgers.
3. Track BTC1H official-vs-proxy settlement drift before model changes:
   side, strike distance, entry price, TTL, quote age, visible quantity, and
   official-minus-proxy basis.
4. Do not create a settlement-distance guard from the current `11` rows. A guard
   can be preregistered only after enough official rows to avoid pure hindsight.
5. Use paused DuckDB snapshots for BTC1H replay parity. Active DuckDBs are
   locked by live writers; multi-GB sidecar scans are not the default path.
6. Build or refactor a faithful BTC1H replay path that reuses captured
   `signal_scan` / live scan semantics instead of re-implementing the clock and
   candidate selection loosely.
7. Use `--candidate-scan-only` only for active-policy parity diagnostics. It
   cannot discover new variants because it relies on the active policy's
   captured `candidate_count` rows.
8. Require exact cached TTL and BTC candle/RV state capture before treating
   model-recomputed selected-signal parity as promotion evidence.
9. After an authorized BTC1H restart, separate the new scan-time-TTL policy
   rows from the old cached-TTL policy rows in every official-settlement and
   parity report.
10. Promotion gate remains:
   - at least `50` official-settled forward BTC1H rows;
   - positive official PnL after fees;
   - official/proxy mismatch rate <= `2%`;
   - no adverse proxy-win/official-loss cluster;
   - row-for-row live decision parity on snapshot;
   - replay and ledger agree on executable side ask, spread, visible size, and
     FOK/no-fill assumptions.

## Immediate Next Tests

- Let the running BTC1H shadow accumulate official rows; do not restart it for
  retuned variants yet.
- If restarting BTC1H to collect scan-time-TTL evidence, treat that as a new
  evidence clock and do not pool it with the old cached-TTL rows.
- After any authorized restart, require nonblank
  `model_policy_version = btc1h_live_model_20260522_scan_ttl_v1` in both
  ledger rows and official settlement exports before counting rows toward the
  new evidence clock.
- On the next pause/snapshot window, run BTC1H row parity from the DuckDB
  snapshot and compare captured `order_decision` against the ledger.
- For `entry59_70_no_chase`, broaden causal replay before any separate shadow;
  the May 21 paused-snapshot replay is an exact row-set match with active
  `entry70_no_chase`, and the floor-filter audit found only one historical
  direct independent market/side row.
- For broad `high_conf_80_no_chase`, do not start/restart a shadow from
  basis-stress strength alone; the extra-row audit shows its removed `>70c`
  rows are live-WS damaging under the fixed +2c stress, including `-$0.84`
  extra-row stress on the paused full-scan replay.
- Once BTC1H has roughly `25` official rows, rerun basis danger tables and
  inspect whether the one observed mismatch is isolated or a systematic
  near-strike problem.
- At `50+` official rows, decide whether to preregister a distance/basis guard
  for a fresh forward run.
