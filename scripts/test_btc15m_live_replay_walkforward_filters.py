from __future__ import annotations

import pandas as pd
import numpy as np

from scripts.audit_btc15m_live_replay_walkforward_filters import (
    FilterSpec,
    add_filter_columns,
    apply_filter,
    build_folds,
    metrics,
    select_best_candidate,
)


def sample_trades() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-05-01T00:00:00Z")
    for i in range(10):
        rows.append(
            {
                "received_at_utc": start + pd.Timedelta(hours=i * 12),
                "event_ticker": f"EV{i}",
                "strategy": "current_lowdd_no_rv",
                "side": "yes" if i % 2 == 0 else "no",
                "entry_price": 0.45 if i < 6 else 0.70,
                "visible_qty": 600,
                "ttl_min": 4.7,
                "score": 0.10 if i < 6 else 0.30,
                "spread_cents": 1.0,
                "btc_ret_1m_bps": 0.2,
                "btc_ret_3m_bps": 4.0 if i < 6 else 0.5,
                "btc_ret_15m_bps": 3.0,
                "premium": 0.50,
                "win": i not in {7, 8, 9},
                "pnl": 0.50 if i < 6 else -0.50,
                "status": "finalized",
            }
        )
    return pd.DataFrame(rows)


def test_add_filter_columns_and_fixed_filter() -> None:
    df = add_filter_columns(sample_trades())
    filtered = apply_filter(df, FilterSpec("btc3_abs_bin", "btc3_3_6bps"))
    assert len(filtered) == 6
    assert filtered["pnl"].sum() == 3.0
    assert "side_btc3_abs_bin" in df.columns


def test_build_folds_uses_chronological_train_then_test() -> None:
    df = add_filter_columns(sample_trades())
    folds = build_folds(df, train_days=1.0, test_hours=24.0, step_hours=24.0)
    assert folds
    assert folds[0][0] == pd.Timestamp("2026-05-02T00:00:00Z")
    assert folds[0][1] == pd.Timestamp("2026-05-03T00:00:00Z")


def test_select_best_candidate_requires_positive_train_bootstrap() -> None:
    rows = pd.DataFrame(
        [
            {
                "filter": "a=x",
                "field": "a",
                "value": "x",
                "train_rows": 50,
                "train_pnl": 1.0,
                "train_event_bootstrap_p05": -0.1,
                "train_event_bootstrap_prob_profit": 0.99,
            },
            {
                "filter": "b=y",
                "field": "b",
                "value": "y",
                "train_rows": 50,
                "train_pnl": 2.0,
                "train_event_bootstrap_p05": 0.2,
                "train_event_bootstrap_prob_profit": 0.95,
            },
        ]
    )
    selected = select_best_candidate(rows, min_train_rows=40, min_train_prob=0.90)
    assert selected is not None
    assert selected["filter"] == "b=y"


def test_metrics_uses_event_cluster_bootstrap() -> None:
    df = sample_trades().iloc[:4].copy()
    result = metrics(df, iters=200, rng=np.random.default_rng(1))
    assert result["rows"] == 4
    assert result["events"] == 4
    assert result["pnl"] == 2.0
    assert result["event_bootstrap_p05"] > 0


def test_metrics_flags_pair_lock_rows() -> None:
    df = sample_trades().iloc[:2].copy()
    df.loc[df.index[0], "side"] = "pair"
    df.loc[df.index[0], "legs"] = "yes->no"
    df.loc[df.index[0], "lock_profit"] = 0.02
    result = metrics(df, iters=200, rng=np.random.default_rng(1))
    assert result["pair_lock_rows"] == 1
    assert "pair_lock_or_position_aware_rows" in result["row_quality_blockers"]
