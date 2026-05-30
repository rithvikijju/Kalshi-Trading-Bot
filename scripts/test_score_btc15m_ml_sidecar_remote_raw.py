import json

import pandas as pd

from scripts.score_btc15m_ml_sidecar_remote_raw import build_gate_summary


def write_score_dir(tmp_path, proxy_trades=3, proxy_pnl=1.2, official_trades=1, official_pnl=0.4):
    score_dir = tmp_path / "score"
    score_dir.mkdir()
    pd.DataFrame(
        [
            {
                "model": "lightgbm_tabular",
                "gate_min_p": 0.82,
                "gate_min_ev": 0.2,
                "pnl_col": "pnl_2c",
                "official_subset": False,
                "trades": proxy_trades,
                "pnl": proxy_pnl,
                "premium": 2.5,
                "win_rate_pct": 66.67,
                "max_dd": -0.5,
            },
            {
                "model": "lightgbm_tabular",
                "gate_min_p": 0.82,
                "gate_min_ev": 0.2,
                "pnl_col": "pnl_official_2c",
                "official_subset": True,
                "trades": official_trades,
                "pnl": official_pnl,
                "premium": 0.8,
                "win_rate_pct": 100.0,
                "max_dd": 0.0,
            },
        ]
    ).to_csv(score_dir / "ml_live_ws_summary.csv", index=False)
    (score_dir / "run_info.json").write_text(
        json.dumps(
            {
                "capture_end_utc": "2026-05-30 16:00:00+00:00",
                "candidate_rows": 1234,
                "candidate_events": 17,
            }
        ),
        encoding="utf-8",
    )
    return score_dir


def test_remote_score_gate_blocks_under_sampled_metric(tmp_path):
    score_dir = write_score_dir(tmp_path)

    gate = build_gate_summary(
        model_name="lightgbm_tabular",
        score_dir=score_dir,
        materialized_db=tmp_path / "raw.duckdb",
        start_utc="2026-05-30T13:00:00+00:00",
        end_utc="",
        min_official_rows=50,
        min_clean_proxy_rows=100,
    )

    assert gate["deployable_now"] is False
    assert gate["paper_order_allowed"] is False
    assert gate["order_submission_allowed"] is False
    assert gate["proxy_rows"] == 3
    assert gate["official_rows"] == 1
    assert "clean_proxy_rows_below_min" in gate["blockers"]
    assert "official_rows_below_min" in gate["blockers"]
    assert gate["research_status"] == "metric_only_collect_more"


def test_remote_score_gate_labels_zero_selection_clearly(tmp_path):
    score_dir = write_score_dir(tmp_path, proxy_trades=0, proxy_pnl=0.0, official_trades=0, official_pnl=0.0)

    gate = build_gate_summary(
        model_name="lightgbm_tabular",
        score_dir=score_dir,
        materialized_db=tmp_path / "raw.duckdb",
        start_utc="2026-05-30T13:00:00+00:00",
        end_utc="",
        min_official_rows=50,
        min_clean_proxy_rows=100,
    )

    assert "no_selected_proxy_rows" in gate["blockers"]
    assert "proxy_pnl_2c_not_positive" not in gate["blockers"]
    assert gate["advisories"] == "no_official_settled_rows_yet"


def test_remote_score_gate_flags_negative_official_subset(tmp_path):
    score_dir = write_score_dir(tmp_path, proxy_trades=120, proxy_pnl=5.0, official_trades=55, official_pnl=-1.0)

    gate = build_gate_summary(
        model_name="lightgbm_tabular",
        score_dir=score_dir,
        materialized_db=tmp_path / "raw.duckdb",
        start_utc="2026-05-30T13:00:00+00:00",
        end_utc="2026-05-30T16:00:00+00:00",
        min_official_rows=50,
        min_clean_proxy_rows=100,
    )

    assert "clean_proxy_rows_below_min" not in gate["blockers"]
    assert "official_rows_below_min" not in gate["blockers"]
    assert "official_pnl_2c_not_positive" in gate["blockers"]
    assert gate["official_pnl_2c"] == -1.0
