from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_decision_distance_guard_audit.py"


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


def row_for(rows: list[dict[str, str]], threshold: str) -> dict[str, str]:
    matches = [row for row in rows if row["threshold_usd"] == threshold]
    assert len(matches) == 1
    return matches[0]


def test_decision_distance_guard_excludes_near_strike_official_flip(tmp_path: Path) -> None:
    hist = tmp_path / "historical.csv"
    official = tmp_path / "official.csv"
    write_csv(
        hist,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "side": "no",
                "entry_time": "2026-05-07T13:00:00Z",
                "entry_price": 0.60,
                "entry_spot_minus_strike": -80.0,
                "win": 1,
            },
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "side": "no",
                "entry_time": "2026-05-07T14:00:00Z",
                "entry_price": 0.60,
                "entry_spot_minus_strike": -30.0,
                "win": 0,
            },
            {
                "variant": "other",
                "source": "websocket",
                "cadence_sec": 1,
                "side": "no",
                "entry_time": "2026-05-07T15:00:00Z",
                "entry_price": 0.60,
                "entry_spot_minus_strike": -500.0,
                "win": 1,
            },
        ],
    )
    write_csv(
        official,
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-20T08:00:00Z",
                "market_ticker": "KXBTCD-TEST-T100",
                "side": "no",
                "entry_price": 0.68,
                "entry_btc_spot": 54.0,
                "floor_strike": 100.0,
                "official_result": "yes",
                "proxy_result": "no",
                "official_win": "False",
                "proxy_win": "True",
                "official_pnl": -0.70,
                "proxy_pnl": 0.30,
                "official_premium": 0.70,
                "official_proxy_result_mismatch": "True",
                "official_minus_proxy_spot": 42.0,
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-20T09:00:00Z",
                "market_ticker": "KXBTCD-TEST-T200",
                "side": "no",
                "entry_price": 0.60,
                "entry_btc_spot": 120.0,
                "floor_strike": 200.0,
                "official_result": "no",
                "proxy_result": "no",
                "official_win": "True",
                "proxy_win": "True",
                "official_pnl": 0.38,
                "proxy_pnl": 0.38,
                "official_premium": 0.62,
                "official_proxy_result_mismatch": "False",
                "official_minus_proxy_spot": 0.0,
            },
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
            str(tmp_path / "missing.csv"),
            "--shadow-official-trades",
            str(official),
            "--thresholds-usd",
            "0,50",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_rows(out_dir / "btc1h_decision_distance_guard_summary.csv")
    no_guard = row_for(summary, "0.0")
    guard_50 = row_for(summary, "50.0")
    assert no_guard["forward_official_rows_current_diagnostic"] == "2"
    assert no_guard["forward_official_proxy_mismatches_current_diagnostic"] == "1"
    assert no_guard["forward_proxy_win_official_loss_flips_current_diagnostic"] == "1"
    assert guard_50["forward_official_rows_current_diagnostic"] == "1"
    assert guard_50["forward_official_proxy_mismatches_current_diagnostic"] == "0"
    assert guard_50["excluded_current_mismatch_rows"] == "1"

    forward_rows = read_rows(out_dir / "btc1h_decision_distance_guard_forward_rows.csv")
    excluded = [
        row
        for row in forward_rows
        if row["threshold_usd"] == "50.0" and row["market_ticker"] == "KXBTCD-TEST-T100"
    ]
    assert excluded[0]["guard_row_status"] == "excluded"
