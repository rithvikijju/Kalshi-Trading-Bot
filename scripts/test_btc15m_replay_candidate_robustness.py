import pandas as pd
import pytest

from scripts.audit_btc15m_replay_candidate_robustness import (
    DatasetSpec,
    audit_dataset,
    parse_dataset,
    robustness_verdict,
)


def test_parse_dataset_accepts_optional_window_path():
    spec = parse_dataset("full|trades.csv|windows.csv")

    assert spec.label == "full"
    assert str(spec.trades_path) == "trades.csv"
    assert str(spec.window_summary_path) == "windows.csv"


def test_parse_dataset_rejects_bad_shape():
    with pytest.raises(Exception):
        parse_dataset("only-label")


def test_audit_dataset_marks_small_positive_sample_research_only(tmp_path):
    trades = pd.DataFrame(
        [
            {"strategy": "current_lowdd_no_rv", "event_ticker": "A", "received_at_utc": "2026-05-30T00:00:00Z", "pnl": 0.5, "premium": 0.5},
            {"strategy": "current_lowdd_no_rv", "event_ticker": "B", "received_at_utc": "2026-05-30T00:15:00Z", "pnl": -0.1, "premium": 0.1},
            {"strategy": "current_lowdd_no_rv", "event_ticker": "C", "received_at_utc": "2026-05-30T00:30:00Z", "pnl": 0.4, "premium": 0.6},
        ]
    )
    path = tmp_path / "trades.csv"
    trades.to_csv(path, index=False)

    rows = audit_dataset(
        DatasetSpec("sample", path),
        iters=200,
        seed=1,
        min_trades=10,
        min_prob=0.95,
        excluded=set(),
    )

    assert len(rows) == 1
    assert rows[0]["pnl"] == pytest.approx(0.8)
    assert rows[0]["trades"] == 3
    assert rows[0]["verdict"] == "research_promising_insufficient_sample"


def test_small_positive_sample_reports_bootstrap_fragility_when_ci_crosses_zero(tmp_path):
    trades = pd.DataFrame(
        [
            {"strategy": "cheap_yes_rr_first", "event_ticker": "A", "received_at_utc": "2026-05-30T00:00:00Z", "pnl": 1.0, "premium": 1.0},
            {"strategy": "cheap_yes_rr_first", "event_ticker": "B", "received_at_utc": "2026-05-30T00:15:00Z", "pnl": -0.8, "premium": 0.8},
            {"strategy": "cheap_yes_rr_first", "event_ticker": "C", "received_at_utc": "2026-05-30T00:30:00Z", "pnl": 0.1, "premium": 0.1},
        ]
    )
    path = tmp_path / "trades.csv"
    trades.to_csv(path, index=False)

    rows = audit_dataset(
        DatasetSpec("sample", path),
        iters=500,
        seed=2,
        min_trades=10,
        min_prob=0.95,
        excluded=set(),
    )

    assert rows[0]["pnl"] > 0
    assert rows[0]["bootstrap_pnl_p05"] <= 0
    assert rows[0]["verdict"] == "research_promising_insufficient_sample_bootstrap_fragile"


def test_verdict_excludes_prior_rejected_pair_lock():
    row = {
        "strategy": "cheap_pair_lock_rr",
        "trades": 100,
        "pnl": 10.0,
        "bootstrap_pnl_p05": 5.0,
        "bootstrap_prob_profit": 1.0,
        "windows": 10,
        "positive_window_rate": 1.0,
    }

    assert robustness_verdict(row, min_trades=50, min_prob=0.95, excluded={"cheap_pair_lock_rr"}) == "excluded_by_prior_selection_bias_audit"
