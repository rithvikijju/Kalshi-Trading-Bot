from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_holdout_independence_audit.py"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def row_by(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    matches = [row for row in rows if row[key] == value]
    assert len(matches) == 1
    return matches[0]


def test_holdout_independence_audit_flags_duplicates_and_concentration(tmp_path: Path) -> None:
    hist = tmp_path / "historical.csv"
    direct = tmp_path / "direct.csv"
    official = tmp_path / "official.csv"
    write_csv(
        hist,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "event_ticker": "E1",
                "market_ticker": "M1",
                "side": "yes",
                "entry_time": "2026-05-07T13:00:00Z",
                "entry_price": 0.40,
                "btc_spot_model": 120,
                "floor_strike": 100,
                "win": 1,
            },
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 5,
                "event_ticker": "E1",
                "market_ticker": "M1",
                "side": "yes",
                "entry_time": "2026-05-07T13:00:01Z",
                "entry_price": 0.40,
                "btc_spot_model": 120,
                "floor_strike": 100,
                "win": 1,
            },
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "event_ticker": "E2",
                "market_ticker": "M2",
                "side": "no",
                "entry_time": "2026-05-07T14:00:00Z",
                "entry_price": 0.60,
                "btc_spot_model": 90,
                "floor_strike": 100,
                "win": 0,
            },
        ],
    )
    write_csv(
        direct,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "predexon_orderbook_snapshots",
                "split": "mar24_apr01_train",
                "event_ticker": "E3",
                "market_ticker": "M3",
                "side": "yes",
                "entry_time": "2026-03-25T21:00:00Z",
                "entry_price": 0.40,
                "entry_spot": 120,
                "strike": 100,
                "win": 1,
            }
        ],
    )
    write_csv(
        official,
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-20T08:00:00Z",
                "event_ticker": "E4",
                "market_ticker": "M4",
                "side": "no",
                "entry_price": 0.68,
                "entry_btc_spot": 90,
                "floor_strike": 100,
                "official_result": "no",
                "official_win": "True",
                "official_pnl": 0.30,
                "official_premium": 0.70,
                "official_proxy_result_mismatch": "False",
            }
        ],
    )
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--robustness-trades",
            str(hist),
            "--direct-trades",
            str(direct),
            "--shadow-official-trades",
            str(official),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_rows(out_dir / "btc1h_holdout_independence_summary.csv")
    hist_row = row_by(summary, "panel", "historical_all_rows")
    assert hist_row["duplicate_market_side_rows"] == "1"
    assert "duplicate_market_side_rows" in hist_row["blockers"]
    assert "single_event_removal_can_flip_pnl" in hist_row["blockers"]
    forward_row = row_by(summary, "panel", "forward_official_stale_rows")
    assert forward_row["status"] == "DIAGNOSTIC_ONLY_STALE_OFFICIAL"
    top_clusters = read_rows(out_dir / "btc1h_holdout_independence_top_clusters.csv")
    assert {row["event_ticker"] for row in top_clusters} >= {"E1", "E2"}
