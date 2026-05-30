from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_basis_stress_audit.py"


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


def summary_row(rows: list[dict[str, str]], shock: str) -> dict[str, str]:
    matches = [row for row in rows if row["basis_shock_usd"] == shock]
    assert len(matches) == 1
    return matches[0]


def test_basis_stress_flips_side_adverse_rows(tmp_path: Path) -> None:
    hist = tmp_path / "historical.csv"
    direct = tmp_path / "direct.csv"
    official = tmp_path / "official.csv"
    basis = tmp_path / "basis.csv"
    write_csv(
        hist,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "event_ticker": "E1",
                "market_ticker": "M1-T100",
                "side": "yes",
                "entry_time": "2026-05-07T13:00:00Z",
                "entry_price": 0.40,
                "btc_spot_model": 120,
                "floor_strike": 100,
                "settlement_spot": 101,
                "win": 1,
            },
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "event_ticker": "E2",
                "market_ticker": "M2-T100",
                "side": "no",
                "entry_time": "2026-05-07T14:00:00Z",
                "entry_price": 0.40,
                "btc_spot_model": 90,
                "floor_strike": 100,
                "settlement_spot": 99,
                "win": 1,
            },
        ],
    )
    write_csv(direct, [])
    write_csv(
        official,
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-20T08:00:00Z",
                "event_ticker": "E3",
                "market_ticker": "M3-T100",
                "side": "no",
                "entry_price": 0.68,
                "entry_btc_spot": 90,
                "floor_strike": 100,
                "official_result": "no",
                "official_win": "True",
                "official_pnl": 0.30,
                "official_premium": 0.70,
                "proxy_close_minus_strike": -10,
            }
        ],
    )
    write_csv(
        basis,
        [
            {
                "p95_abs_official_minus_proxy_spot": 1,
                "max_abs_official_minus_proxy_spot": 2,
                "max_adverse_basis_usd": 2,
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
            "--basis-summary",
            str(basis),
            "--basis-shocks-usd",
            "0,2",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = read_rows(out_dir / "btc1h_basis_stress_summary.csv")
    no_shock = summary_row(rows, "0.0")
    shock = summary_row(rows, "2.0")
    assert no_shock["basis_flip_rows"] == "0"
    assert shock["basis_flip_rows"] == "2"
    assert shock["positive_pnl"] == "False"
