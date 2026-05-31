import json

from scripts.refresh_btc15m_research_loop import build_report
from scripts.refresh_btc15m_research_loop import build_summary


def test_research_loop_summary_prefers_stamp_artifacts(tmp_path):
    backtest = tmp_path / "backtest_outputs"
    runtime = tmp_path / "runtime"
    stamp = "20260531_0045"
    lowdd_dir = backtest / f"btc15m_lowdd_forward_promotion_gate_remote_{stamp}"
    ml_dir = backtest / f"btc15m_ml_sidecar_remote_raw_score_{stamp}"
    screen_dir = backtest / f"btc15m_research_candidate_screen_{stamp}"
    status_dir = runtime / "remote_snapshots" / f"lowdd_refresh_{stamp}"
    for path in [lowdd_dir, ml_dir, screen_dir, status_dir]:
        path.mkdir(parents=True)

    (lowdd_dir / "lowdd_forward_promotion_gate_summary.json").write_text(
        json.dumps(
            {
                "research_status": "research_promising_insufficient_forward_sample",
                "production_ready": False,
                "paper_settled_rows": 19,
                "paper_official_pnl": 43.08,
                "blockers": "selected_rows_below_min",
            }
        ),
        encoding="utf-8",
    )
    (ml_dir / "ml_sidecar_remote_score_summary.json").write_text(
        json.dumps(
            {
                "research_status": "metric_only_collect_more",
                "proxy_rows": 2,
                "official_rows": 0,
                "proxy_pnl_2c": 0.08,
                "blockers": "official_rows_below_min",
            }
        ),
        encoding="utf-8",
    )
    (screen_dir / "run_info.json").write_text(
        json.dumps({"deployable_now_count": 0, "row_count": 44}),
        encoding="utf-8",
    )
    (screen_dir / "candidate_screen.csv").write_text(
        "candidate_id,status,forward_action,trades,official_rows,pnl,blockers\n"
        "btc15m_lowdd_current_wrapper,active_forward_research_insufficient_sample,keep_raw_and_lowdd_running,19,19,43.08,selected_rows_below_min\n",
        encoding="utf-8",
    )
    (status_dir / "remote_status.json").write_text(
        json.dumps(
            {
                "raw_status": {"pid": 13912, "failed": False, "dropped": 0},
                "lowdd_status": {"pid": 9408, "failed": False, "dropped": 0},
            }
        ),
        encoding="utf-8",
    )

    summary = build_summary(
        stamp=stamp,
        out_dir=backtest / f"btc15m_research_loop_refresh_{stamp}",
        backtest_root=backtest,
        runtime_root=runtime,
        steps=[{"name": "x", "ok": True}],
        git={"status_short_branch": {"stdout": "## sami...origin/sami"}},
    )

    assert summary["steps_ok"] is True
    assert summary["candidate_screen_info"]["deployable_now_count"] == 0
    assert summary["lowdd_gate"]["paper_settled_rows"] == 19
    assert summary["ml_sidecar"]["proxy_rows"] == 2
    assert summary["remote_status"]["raw"]["pid"] == 13912
    assert len(summary["priority_rows"]) == 1


def test_research_loop_report_keeps_no_deployment_verdict():
    report = build_report(
        {
            "created_at_utc": "2026-05-31T00:45:00+00:00",
            "stamp": "20260531_0045",
            "candidate_screen_info": {"deployable_now_count": 0},
            "lowdd_gate": {
                "research_status": "research_promising_insufficient_forward_sample",
                "production_ready": False,
                "paper_settled_rows": 19,
                "paper_official_pnl": 43.08,
                "blockers": "selected_rows_below_min",
            },
            "ml_sidecar": {
                "research_status": "metric_only_collect_more",
                "proxy_rows": 2,
                "official_rows": 0,
                "proxy_pnl_2c": 0.08,
                "blockers": "official_rows_below_min",
            },
            "remote_status": {
                "raw": {"pid": 13912, "failed": False, "dropped": 0},
                "lowdd": {"pid": 9408, "failed": False, "dropped": 0},
            },
            "git": {
                "status_short_branch": {"stdout": "## sami...origin/sami"},
                "remote_branch_recency": {"stdout": "origin/sami b5982ec"},
            },
            "lowdd_gate_artifact": "backtest_outputs/lowdd",
            "ml_sidecar_artifact": "backtest_outputs/ml",
            "candidate_screen_artifact": "backtest_outputs/screen",
            "remote_status_artifact": "runtime/status.json",
        }
    )

    assert "Candidate screen deployable count: `0`" in report
    assert "No live deployment or process restart was performed" in report
    assert "proxy_rows=`2`" in report
