import argparse

from scripts.build_btc15m_lowdd_forward_promotion_gate import evaluate_gate


def args(**overrides):
    values = {
        "min_selected_rows": 50,
        "min_settled_rows": 50,
        "min_order_rows": 50,
        "min_paper_rows": 50,
        "min_live_sidecar_parity_pass_rate": 1.0,
        "max_order_price_mismatch_rows": 0,
        "min_order_max_drawdown": -5.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def positive_summary(rows=3, order_mismatch=0, order_pnl=5.05):
    return {
        "selected_rows": rows,
        "settled_rows": rows,
        "signal_one_contract_pnl": 0.51,
        "signal_one_contract_premium": 1.49,
        "signal_max_drawdown": -0.25,
        "order_rows": rows,
        "order_scaled_pnl": order_pnl,
        "order_scaled_premium": 10.95,
        "order_max_drawdown": -0.97,
        "order_price_mismatch_rows": order_mismatch,
    }


def parity_summary(rows=3, live_pass_rows=3, generic_mismatch_rows=1):
    return {
        "paper_rows": rows,
        "live_sidecar_parity_pass_rows": live_pass_rows,
        "generic_replay_price_mismatch_rows": generic_mismatch_rows,
    }


def paper_summary(rows=3, pnl=5.05):
    return {
        "trades": rows,
        "settled": rows,
        "official_pnl": pnl,
        "official_premium": 10.95,
    }


def test_small_positive_forward_sample_is_research_only():
    result = evaluate_gate(
        candidate="candidate",
        selected_summary=positive_summary(rows=3),
        parity_summary=parity_summary(rows=3),
        paper_summary=paper_summary(rows=3),
        args=args(),
    )

    assert result["production_ready"] is False
    assert result["research_status"] == "research_promising_insufficient_forward_sample"
    assert "selected_rows_below_min" in result["blockers"]
    assert "settled_rows_below_min" in result["blockers"]
    assert "generic_replay_price_mismatch_sidecar_selected_is_authoritative" in result["advisories"]


def test_price_and_parity_failures_are_blockers():
    result = evaluate_gate(
        candidate="candidate",
        selected_summary=positive_summary(rows=80, order_mismatch=1),
        parity_summary=parity_summary(rows=80, live_pass_rows=79),
        paper_summary=paper_summary(rows=80),
        args=args(),
    )

    assert result["production_ready"] is False
    assert "order_price_mismatch_rows_nonzero" in result["blockers"]
    assert "live_sidecar_parity_not_all_rows" in result["blockers"]


def test_enough_rows_with_clean_parity_can_pass_gate():
    result = evaluate_gate(
        candidate="candidate",
        selected_summary=positive_summary(rows=80, order_mismatch=0),
        parity_summary=parity_summary(rows=80, live_pass_rows=80, generic_mismatch_rows=0),
        paper_summary=paper_summary(rows=80),
        args=args(),
    )

    assert result["production_ready"] is True
    assert result["research_status"] == "production_ready_pending_human_approval"
    assert result["blockers"] == ""
