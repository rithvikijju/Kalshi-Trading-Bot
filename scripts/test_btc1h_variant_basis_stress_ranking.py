from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_variant_basis_stress_ranking.py"


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


def test_variant_basis_stress_ranking_keeps_fixed_variants_separate(tmp_path: Path) -> None:
    hist = tmp_path / "historical.csv"
    direct = tmp_path / "direct.csv"
    derived = tmp_path / "derived.csv"
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
                "variant": "high_conf_80_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "event_ticker": "E2",
                "market_ticker": "M2-T100",
                "side": "yes",
                "entry_time": "2026-05-07T14:00:00Z",
                "entry_price": 0.40,
                "btc_spot_model": 150,
                "floor_strike": 100,
                "settlement_spot": 110,
                "win": 1,
            },
        ],
    )
    write_csv(direct, [])
    write_csv(derived, [])
    write_csv(
        basis,
        [
            {
                "p95_abs_official_minus_proxy_spot": 2,
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
            "--derived-entry59-trades",
            str(derived),
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
    ranking = read_rows(out_dir / "btc1h_variant_basis_stress_ranking.csv")
    assert ranking[0]["variant"] == "high_conf_80_no_chase"
    active = next(row for row in ranking if row["variant"] == "high_conf_80_entry70_no_chase")
    assert active["max_basis_shock_with_positive_unique_pnl"] == "0.0"
    summary = read_rows(out_dir / "btc1h_variant_basis_stress_summary.csv")
    active_shock = [
        row
        for row in summary
        if row["variant"] == "high_conf_80_entry70_no_chase" and row["basis_shock_usd"] == "2.0"
    ][0]
    assert active_shock["basis_flip_rows"] == "1"
    assert active_shock["positive_unique_market_side_pnl"] == "False"
