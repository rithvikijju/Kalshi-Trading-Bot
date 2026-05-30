from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_order_decision_replay_baseline.py"


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


def read_one(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def test_order_decision_replay_baseline_materializes_exact_diagnostic_rows(tmp_path: Path) -> None:
    fill_chain = tmp_path / "fill_chain.csv"
    write_csv(
        fill_chain,
        [
            {
                "decision_received_at_ns": 100,
                "decision_received_at_utc": "2026-05-22T12:00:00Z",
                "decision_action": "paper_fill",
                "event_ticker_decision": "KXBTCD-26MAY2212",
                "event_ticker_official": "KXBTCD-26MAY2212",
                "market_ticker": "KXBTCD-26MAY2212-T70000",
                "side": "no",
                "decision_entry_price": 0.62,
                "decision_net_edge_cents": 14.2,
                "decision_btc_spot": 69940.0,
                "fee": 0.02,
                "top_visible_qty": 8,
                "spread_cents": 1.0,
                "model_p_yes": 0.18,
                "quote_received_at_ns": 90,
                "official_result": "no",
                "official_win": "True",
                "official_premium": 0.64,
                "official_pnl": 0.36,
                "settlement_ts": "2026-05-22T13:00:00Z",
                "_merge": "both",
            },
            {
                "decision_received_at_ns": 200,
                "decision_received_at_utc": "2026-05-22T12:00:01Z",
                "decision_action": "skip",
                "event_ticker_decision": "KXBTCD-26MAY2212",
                "market_ticker": "KXBTCD-26MAY2212-T70100",
                "side": "no",
                "decision_entry_price": 0.63,
                "official_result": "no",
                "_merge": "left_only",
            },
        ],
    )
    out_dir = tmp_path / "out"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--fill-official-chain", str(fill_chain), "--out-dir", str(out_dir)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    trade = read_one(out_dir / "btc1h_order_decision_replay_trades.csv")
    summary = read_one(out_dir / "btc1h_order_decision_replay_summary.csv")
    run_info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))

    assert trade["variant"] == "high_conf_80_entry70_no_chase"
    assert trade["market_ticker"] == "KXBTCD-26MAY2212-T70000"
    assert trade["side"] == "no"
    assert trade["entry_price"] == "0.62"
    assert trade["pnl"] == "0.36"
    assert trade["diagnostic_replay_source"] == "captured_order_decision_log"
    assert trade["independent_counterfactual_replay"] == "False"
    assert summary["baseline_rows"] == "1"
    assert summary["official_pnl"] == "0.36"
    assert summary["promotion_usable_as_counterfactual"] == "False"
    assert run_info["independent_counterfactual_replay"] is False
