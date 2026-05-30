from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_statistical_confidence_audit.py"


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


def panel(rows: list[dict[str, str]], name: str) -> dict[str, str]:
    matches = [row for row in rows if row["panel"] == name]
    assert len(matches) == 1
    return matches[0]


def test_statistical_confidence_audit_flags_stale_forward_and_dedupes(tmp_path: Path) -> None:
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
        ],
    )
    write_csv(
        direct,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "predexon_orderbook_snapshots",
                "split": "mar24_apr01_train",
                "event_ticker": "E2",
                "market_ticker": "M2",
                "side": "no",
                "entry_time": "2026-03-25T21:00:00Z",
                "entry_price": 0.40,
                "entry_spot": 90,
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
                "event_ticker": "E3",
                "market_ticker": "M3",
                "side": "no",
                "entry_price": 0.68,
                "entry_btc_spot": 54,
                "floor_strike": 100,
                "official_result": "yes",
                "official_win": "False",
                "official_pnl": -0.70,
                "official_premium": 0.70,
                "official_proxy_result_mismatch": "True",
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
            "--bootstrap-sims",
            "200",
            "--null-sims",
            "200",
            "--seed",
            "7",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = read_rows(out_dir / "btc1h_statistical_confidence_summary.csv")
    naive = panel(rows, "historical_naive_all_rows")
    unique = panel(rows, "historical_unique_market_side")
    forward = panel(rows, "forward_official_stale_rows")
    assert naive["rows"] == "3"
    assert naive["duplicate_market_side_rows"] == "1"
    assert unique["rows"] == "2"
    assert forward["status"] == "DIAGNOSTIC_ONLY_STALE_OFFICIAL"
    assert forward["pnl"] == "-0.7"
