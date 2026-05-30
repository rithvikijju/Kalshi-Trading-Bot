from __future__ import annotations

from argparse import Namespace

import pandas as pd

from scripts.build_btc1h_multi_holdout_research import aggregate_by_variant, build_holdout_provenance


ACTIVE = "high_conf_80_entry70_no_chase"


def base_summary(*, mismatch_rate: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "evidence_source": "robustness_trade_logs",
                "variant": ACTIVE,
                "holdout": "H1_predexon_apr01_14_holdout",
                "trades": 2,
                "pnl": 0.7,
            },
            {
                "evidence_source": "robustness_trade_logs",
                "variant": ACTIVE,
                "holdout": "H4_live_ws_may06_12_stride1s",
                "trades": 2,
                "pnl": 0.5,
            },
            {
                "evidence_source": "forward_shadow_official",
                "variant": ACTIVE,
                "holdout": "H5_forward_remote_official_may18_plus",
                "trades": 11,
                "pnl": 0.5,
                "official_proxy_mismatch_rate": mismatch_rate,
            },
        ]
    )


def fidelity_pass() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "selected_without_decision": 0,
                "decision_without_selected": 0,
                "fill_without_official": 0,
                "official_without_fill": 0,
                "selected_to_decision_matched": 13,
                "fill_decision_to_official_matched": 11,
            }
        ]
    )


def args() -> Namespace:
    return Namespace(min_forward_official_rows=50, max_forward_mismatch_rate=0.02)


def active_row(out: pd.DataFrame) -> pd.Series:
    rows = out[out["variant"].astype(str).eq(ACTIVE)]
    assert len(rows) == 1
    return rows.iloc[0]


def test_multi_holdout_does_not_mark_official_mismatch_sample_near_deployable() -> None:
    out = aggregate_by_variant(
        base_summary(mismatch_rate=0.0909),
        args(),
        fidelity=fidelity_pass(),
        selected_model_parity=pd.DataFrame(),
        replay_reconciliation=pd.DataFrame(),
    )

    row = active_row(out)
    assert bool(row["research_promising"])
    assert not bool(row["near_deployable_candidate"])
    assert row["promotion_readiness_status"] == "promising_but_blocked_by_official_or_fidelity_gates"
    assert "official_proxy_mismatch_gate_failed" in row["near_deployable_disqualifying_blockers"]


def test_multi_holdout_allows_near_deployable_only_for_sample_and_final_execution_gate() -> None:
    out = aggregate_by_variant(
        base_summary(mismatch_rate=0.0),
        args(),
        fidelity=fidelity_pass(),
        selected_model_parity=pd.DataFrame(),
        replay_reconciliation=pd.DataFrame(),
    )

    row = active_row(out)
    assert bool(row["near_deployable_candidate"])
    assert row["promotion_readiness_status"] == "near_deployable_pending_sample_and_final_execution_gate"
    assert row["deploy_blockers"] == (
        "too_few_forward_official_rows;needs_full_counterfactual_replay_or_live_execution_gate"
    )
    assert row["near_deployable_disqualifying_blockers"] == ""


def test_multi_holdout_row_unfaithful_replay_disqualifies_near_deployable() -> None:
    replay = pd.DataFrame(
        [
            {
                "promotion_usable_replay": False,
                "actual_rows": 11,
                "replay_rows": 10,
                "exact_market_side_matches": 9,
                "exact_match_rate_vs_actual": 0.818182,
                "replay_minus_actual_pnl": 0.61,
                "blockers": "missing_actual_rows;replay_row_count_differs",
            }
        ]
    )
    out = aggregate_by_variant(
        base_summary(mismatch_rate=0.0),
        args(),
        fidelity=fidelity_pass(),
        selected_model_parity=pd.DataFrame(),
        replay_reconciliation=replay,
    )

    row = active_row(out)
    assert not bool(row["near_deployable_candidate"])
    assert "counterfactual_replay_not_row_faithful" in row["deploy_blockers"]
    assert "counterfactual_replay_not_row_faithful" in row["near_deployable_disqualifying_blockers"]


def test_holdout_provenance_separates_research_rows_from_official_diagnostics() -> None:
    candidates = aggregate_by_variant(
        base_summary(mismatch_rate=0.0909),
        args(),
        fidelity=fidelity_pass(),
        selected_model_parity=pd.DataFrame(),
        replay_reconciliation=pd.DataFrame(),
    )

    provenance, provenance_summary = build_holdout_provenance(base_summary(mismatch_rate=0.0909), candidates, args())

    official = provenance[provenance["evidence_family"].eq("official_forward_shadow")].iloc[0]
    live_ws = provenance[provenance["evidence_family"].eq("live_ws_replay_research")].iloc[0]
    variant_summary = provenance_summary[provenance_summary["variant"].eq(ACTIVE)].iloc[0]
    assert official["current_use"] == "official_forward_diagnostic_only"
    assert bool(official["counts_for_official_forward_diagnostic"]) is True
    assert bool(official["counts_for_near_deployable_now"]) is False
    assert "official_proxy_mismatch_rate_above_limit" in official["promotion_blockers"]
    assert live_ws["current_use"] == "live_ws_stability_research_only"
    assert bool(live_ws["counts_for_live_ws_stability"]) is True
    assert variant_summary["holdout_evidence_status"] == "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY"
    assert variant_summary["deployable_countable_rows"] == 0
    assert variant_summary["near_deployable_countable_rows"] == 0


def test_holdout_provenance_can_mark_near_deployable_evidence_when_candidate_gate_allows_it() -> None:
    candidates = aggregate_by_variant(
        base_summary(mismatch_rate=0.0),
        args(),
        fidelity=fidelity_pass(),
        selected_model_parity=pd.DataFrame(),
        replay_reconciliation=pd.DataFrame(),
    )

    provenance, provenance_summary = build_holdout_provenance(base_summary(mismatch_rate=0.0), candidates, args())

    official = provenance[provenance["evidence_family"].eq("official_forward_shadow")].iloc[0]
    variant_summary = provenance_summary[provenance_summary["variant"].eq(ACTIVE)].iloc[0]
    assert official["current_use"] == "near_deployable_evidence"
    assert bool(official["counts_for_near_deployable_now"]) is True
    assert "too_few_official_rows_for_promotion" in official["promotion_blockers"]
    assert variant_summary["holdout_evidence_status"] == "HAS_NEAR_DEPLOYABLE_HOLDOUT_EVIDENCE"
    assert variant_summary["near_deployable_countable_rows"] == 1
