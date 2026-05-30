import json

from scripts.build_btc15m_research_candidate_screen import screen_ml_remote_sidecar


def test_remote_ml_sidecar_zero_selection_blocks_ordering(tmp_path):
    out_dir = tmp_path / "btc15m_ml_sidecar_remote_raw_score_20260530_1235"
    out_dir.mkdir()
    (out_dir / "ml_sidecar_remote_score_summary.json").write_text(
        json.dumps(
            {
                "candidate_id": "btc15m_lightgbm_tabular_nontrading_sidecar",
                "model_name": "lightgbm_tabular",
                "candidate_rows": 380168,
                "candidate_events": 12,
                "proxy_rows": 0,
                "proxy_pnl_2c": 0.0,
                "proxy_premium": 0.0,
                "proxy_max_drawdown": 0.0,
                "proxy_win_rate_pct": 0.0,
                "official_rows": 0,
                "official_pnl_2c": 0.0,
                "blockers": "no_selected_proxy_rows;clean_proxy_rows_below_min;official_rows_below_min",
                "advisories": "no_official_settled_rows_yet",
                "start_utc": "2026-05-30T13:00:00+00:00",
                "end_utc": "2026-05-30 16:14:03.838195+00:00",
            }
        ),
        encoding="utf-8",
    )

    rows = screen_ml_remote_sidecar(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["candidate_id"] == "btc15m_lightgbm_tabular_nontrading_sidecar"
    assert row["family"] == "ml_sidecar_forward"
    assert row["status"] == "forward_raw_metric_no_selection"
    assert row["forward_action"] == "score_later_raw_capture_snapshot;do_not_start_ordering_shadow"
    assert row["deployable_now"] is False
    assert row["can_start_new_forward"] is False
    assert row["trades"] == 0
    assert row["official_rows"] == 0
    assert "no_selected_proxy_rows" in row["blockers"]
    assert "candidate_rows=380168" in row["notes"]
