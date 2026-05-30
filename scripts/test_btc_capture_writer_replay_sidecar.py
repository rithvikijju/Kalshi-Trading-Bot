import json
import tempfile
from pathlib import Path

import duckdb

from scripts.btc_1hr_research_live import LiveCaptureWriter


def test_live_capture_writer_replay_sidecar_includes_raw_coinbase_ticks():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "capture.duckdb"
        writer = LiveCaptureWriter(db_path, enabled=True)
        try:
            writer.record(
                "coinbase_ticker",
                {
                    "received_at_ns": 100,
                    "received_at_utc": "2026-05-30T12:00:00Z",
                    "product_id": "KRAKEN:XBT/USD",
                    "price": 73456.25,
                    "best_bid": 73456.0,
                    "best_ask": 73456.5,
                    "sequence": 7,
                    "exchange_time": None,
                },
            )
            writer.record(
                "ws_orderbook_top",
                {
                    "received_at_ns": 110,
                    "received_at_utc": "2026-05-30T12:00:00.000000110Z",
                    "market_ticker": "KXBTC15M-26MAY300800-00",
                    "event_ticker": "KXBTC15M-26MAY300800",
                    "sid": 1,
                    "seq": 2,
                    "yes_bid": 0.42,
                    "yes_bid_qty": 10.0,
                    "yes_ask": 0.43,
                    "yes_ask_qty": 11.0,
                    "no_bid": 0.56,
                    "no_bid_qty": 12.0,
                    "no_ask": 0.57,
                    "no_ask_qty": 13.0,
                    "btc_spot": 73456.25,
                    "source": "test",
                },
            )
        finally:
            writer.close()

        assert not writer.failed, writer.last_error

        sidecar_rows = [
            json.loads(line)
            for line in db_path.with_name(db_path.name + ".replay.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [row["table"] for row in sidecar_rows] == ["coinbase_ticker", "ws_orderbook_top"]

        status = json.loads(db_path.with_name(db_path.name + ".status.json").read_text(encoding="utf-8-sig"))
        assert status["replay_sidecar_rows_by_table"]["coinbase_ticker"] == 1
        assert status["replay_sidecar_rows_by_table"]["ws_orderbook_top"] == 1

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            coinbase = con.execute("SELECT product_id, price, best_bid, best_ask FROM coinbase_ticker").fetchall()
        finally:
            con.close()

        assert coinbase == [("KRAKEN:XBT/USD", 73456.25, 73456.0, 73456.5)]
