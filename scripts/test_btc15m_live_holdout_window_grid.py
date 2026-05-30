import pandas as pd
import pytest

from scripts.build_btc15m_live_holdout_window_grid import build_windows


def test_build_windows_advances_by_step_and_clips_end():
    start = pd.Timestamp("2026-05-30T00:00:00Z")
    end = pd.Timestamp("2026-05-30T02:30:00Z")

    windows = build_windows(start, end, window_hours=1.0, step_hours=1.0)

    assert windows == [
        (pd.Timestamp("2026-05-30T00:00:00Z"), pd.Timestamp("2026-05-30T01:00:00Z")),
        (pd.Timestamp("2026-05-30T01:00:00Z"), pd.Timestamp("2026-05-30T02:00:00Z")),
        (pd.Timestamp("2026-05-30T02:00:00Z"), pd.Timestamp("2026-05-30T02:30:00Z")),
    ]


def test_build_windows_rejects_bad_bounds():
    ts = pd.Timestamp("2026-05-30T00:00:00Z")

    with pytest.raises(ValueError):
        build_windows(ts, ts, window_hours=1.0, step_hours=1.0)
    with pytest.raises(ValueError):
        build_windows(ts, ts + pd.Timedelta(hours=1), window_hours=0.0, step_hours=1.0)
