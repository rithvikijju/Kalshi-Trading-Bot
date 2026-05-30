import argparse
import pickle

import pandas as pd

from scripts.build_btc15m_ml_sidecar_preregistration import (
    build_freeze_spec,
    candidate_screen_evidence,
    gate_from_model_summary,
    load_model_metadata,
    ml_grid_evidence,
)


def write_inputs(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    with (model_dir / "lightgbm_tabular.pkl").open("wb") as f:
        pickle.dump({"model": object(), "features": ["entry_price", "spread_cents"]}, f)
    pd.DataFrame(
        [
            {
                "model": "lightgbm_tabular",
                "split": "validation",
                "pnl_model": "pnl",
                "gate_min_p": 0.82,
                "gate_min_ev": 0.2,
                "trades": 12,
                "pnl": 2.88,
                "max_dd": -0.62,
                "win_rate_pct": 83.33,
            }
        ]
    ).to_csv(model_dir / "model_summary.csv", index=False)

    grid_dir = tmp_path / "grid"
    grid_dir.mkdir()
    pd.DataFrame(
        [
            {
                "model": "lightgbm_tabular",
                "subset": "proxy_all",
                "rows": 48,
                "events": 48,
                "official_rows": 5,
                "proxy_pnl_2c": 3.0,
                "official_pnl_2c": 1.37,
                "premium": 26.03,
                "proxy_win_rate": 0.625,
                "official_win_rate": 0.8,
                "max_dd_proxy_2c": -1.97,
                "max_dd_official_2c": -0.27,
                "row_quality_blockers": "",
            },
            {
                "model": "lightgbm_tabular",
                "subset": "official_subset",
                "rows": 5,
                "events": 5,
                "official_rows": 5,
                "proxy_pnl_2c": 1.37,
                "official_pnl_2c": 1.37,
                "premium": 2.53,
                "proxy_win_rate": 0.8,
                "official_win_rate": 0.8,
                "max_dd_proxy_2c": -0.27,
                "max_dd_official_2c": -0.27,
                "row_quality_blockers": "",
            },
        ]
    ).to_csv(grid_dir / "ml_window_grid_official_proxy_summary.csv", index=False)

    screen_dir = tmp_path / "screen"
    screen_dir.mkdir()
    pd.DataFrame(
        [
            {
                "candidate_id": "ml_lightgbm_tabular",
                "status": "diagnostic_sidecar_candidate_under_sampled",
                "forward_action": "freeze_non_trading_sidecar_metric_only",
                "deployable_now": False,
                "blockers": "official_rows_below_min",
                "notes": "official_pnl_2c=1.37",
            }
        ]
    ).to_csv(screen_dir / "candidate_screen.csv", index=False)
    return model_dir, grid_dir, screen_dir


def args(model_dir):
    return argparse.Namespace(
        model_dir=model_dir,
        model_name="lightgbm_tabular",
        min_future_official_rows=50,
        min_future_clean_proxy_rows=100,
    )


def test_lightgbm_metric_freezes_but_stays_undeployable(tmp_path):
    model_dir, grid_dir, screen_dir = write_inputs(tmp_path)
    model_meta = load_model_metadata(model_dir / "lightgbm_tabular.pkl")
    gate = gate_from_model_summary(model_dir, "lightgbm_tabular")
    evidence = ml_grid_evidence(grid_dir, "lightgbm_tabular")
    screen = candidate_screen_evidence(screen_dir, "lightgbm_tabular")

    spec, gate_row = build_freeze_spec(
        args=args(model_dir),
        model_path=model_dir / "lightgbm_tabular.pkl",
        model_meta=model_meta,
        gate=gate,
        evidence=evidence,
        screen=screen,
    )

    assert spec["status"] == "FROZEN_NON_TRADING_FORWARD_METRIC"
    assert spec["order_submission_allowed"] is False
    assert spec["paper_order_allowed"] is False
    assert spec["deployable_now"] is False
    assert spec["feature_count"] == 2
    assert "official_rows_below_forward_review_min" in spec["deployment_blockers"]
    assert "clean_proxy_rows_below_forward_review_min" in spec["deployment_blockers"]
    assert spec["freeze_blockers"] == ""
    assert gate_row["can_freeze_non_trading_metric"] is True
    assert gate_row["recommended_next_action"] == "score_future_raw_capture_snapshots_no_orders"


def test_missing_model_blocks_freeze(tmp_path):
    _, grid_dir, screen_dir = write_inputs(tmp_path)
    missing_model_dir = tmp_path / "missing_model_dir"
    missing_model_dir.mkdir()
    pd.DataFrame(
        [
            {
                "model": "lightgbm_tabular",
                "split": "validation",
                "pnl_model": "pnl",
                "gate_min_p": 0.82,
                "gate_min_ev": 0.2,
            }
        ]
    ).to_csv(missing_model_dir / "model_summary.csv", index=False)

    spec, gate_row = build_freeze_spec(
        args=args(missing_model_dir),
        model_path=missing_model_dir / "lightgbm_tabular.pkl",
        model_meta=load_model_metadata(missing_model_dir / "lightgbm_tabular.pkl"),
        gate=gate_from_model_summary(missing_model_dir, "lightgbm_tabular"),
        evidence=ml_grid_evidence(grid_dir, "lightgbm_tabular"),
        screen=candidate_screen_evidence(screen_dir, "lightgbm_tabular"),
    )

    assert spec["status"] == "FREEZE_BLOCKED"
    assert "model_file_missing" in spec["freeze_blockers"]
    assert "model_features_missing" in spec["freeze_blockers"]
    assert gate_row["can_freeze_non_trading_metric"] is False
