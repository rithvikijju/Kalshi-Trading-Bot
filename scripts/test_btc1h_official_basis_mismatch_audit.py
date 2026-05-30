from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_official_basis_mismatch_audit.py"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_one(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def test_btc1h_basis_audit_detects_no_side_proxy_win_official_loss(tmp_path: Path) -> None:
    trades = tmp_path / "trades.csv"
    write_csv(
        trades,
        [
            {
                "ledger": LEDGER,
                "created_at": "2026-05-20T11:52:04+00:00",
                "market_ticker": "KXBTCD-26MAY2008-T77299.99",
                "side": "no",
                "entry_price": 0.68,
                "model_p_yes": 0.15,
                "net_edge_cents": 14.4,
                "quote_age_ms": 72,
                "top_visible_qty": 881,
                "official_result": "yes",
                "proxy_result": "no",
                "official_pnl": -0.70,
                "proxy_pnl": 0.30,
                "official_minus_proxy_spot": 42.15,
                "proxy_close_minus_strike": -8.37,
                "official_expiration_minus_strike": 33.78,
                "official_proxy_result_mismatch": True,
            },
            {
                "ledger": LEDGER,
                "created_at": "2026-05-20T13:47:23+00:00",
                "market_ticker": "KXBTCD-26MAY2010-T77199.99",
                "side": "no",
                "entry_price": 0.57,
                "model_p_yes": 0.18,
                "net_edge_cents": 22.1,
                "quote_age_ms": 42,
                "top_visible_qty": 350,
                "official_result": "no",
                "proxy_result": "no",
                "official_pnl": 0.41,
                "proxy_pnl": 0.41,
                "official_minus_proxy_spot": -70.41,
                "proxy_close_minus_strike": -19.99,
                "official_expiration_minus_strike": -90.40,
                "official_proxy_result_mismatch": False,
            },
        ],
    )
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--trades-csv",
            str(trades),
            "--out-dir",
            str(out_dir),
            "--min-promotion-official-rows",
            "50",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_one(out_dir / "btc1h_basis_mismatch_summary.csv")
    assert summary["official_rows"] == "2"
    assert summary["official_proxy_mismatches"] == "1"
    assert summary["proxy_win_official_loss_flips"] == "1"
    assert summary["deployable_guard_now"] == "False"
    assert "TOO_FEW_OFFICIAL_ROWS" in summary["audit_status"]
    assert "OBSERVED_PROXY_OFFICIAL_MISMATCH" in summary["audit_status"]

    with (out_dir / "btc1h_basis_mismatch_watchlist.csv").open("r", newline="", encoding="utf-8") as f:
        watch = list(csv.DictReader(f))
    assert watch[0]["market_ticker"] == "KXBTCD-26MAY2008-T77299.99"
    assert watch[0]["proxy_win_official_loss"] == "True"
