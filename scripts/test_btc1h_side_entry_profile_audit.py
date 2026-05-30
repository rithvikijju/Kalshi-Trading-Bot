from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_side_entry_profile_audit.py"


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


def profile(rows: list[dict[str, str]], name: str) -> dict[str, str]:
    matches = [row for row in rows if row["profile"] == name]
    assert len(matches) == 1
    return matches[0]


def test_side_entry_profile_audit_splits_forward_official_rows(tmp_path: Path) -> None:
    hist = tmp_path / "historical.csv"
    official = tmp_path / "official.csv"
    write_csv(
        hist,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "side": "yes",
                "entry_time": "2026-05-07T13:00:00Z",
                "entry_price": 0.55,
                "btc_spot_model": 120.0,
                "floor_strike": 100.0,
                "win": 1,
            },
            {
                "variant": "high_conf_80_entry70_no_chase",
                "source": "websocket",
                "cadence_sec": 1,
                "side": "no",
                "entry_time": "2026-05-07T14:00:00Z",
                "entry_price": 0.66,
                "btc_spot_model": 90.0,
                "floor_strike": 100.0,
                "win": 0,
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
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-20T09:00:00Z",
                "market_ticker": "KXBTCD-TEST-T200",
                "side": "yes",
                "entry_price": 0.55,
                "entry_btc_spot": 220.0,
                "floor_strike": 200.0,
                "official_result": "yes",
                "proxy_result": "yes",
                "official_win": "True",
                "proxy_win": "True",
                "official_pnl": 0.43,
                "proxy_pnl": 0.43,
                "official_premium": 0.57,
                "official_proxy_result_mismatch": "False",
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
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_rows(out_dir / "btc1h_side_entry_profile_summary.csv")
    yes = profile(summary, "yes_only")
    no = profile(summary, "no_only")
    assert yes["forward_official_rows_current_diagnostic"] == "1"
    assert yes["forward_official_proxy_mismatches_current_diagnostic"] == "0"
    assert no["forward_official_rows_current_diagnostic"] == "1"
    assert no["forward_official_proxy_mismatches_current_diagnostic"] == "1"
    assert profile(summary, "no_entry_60_70")["forward_proxy_win_official_loss_flips_current_diagnostic"] == "1"
