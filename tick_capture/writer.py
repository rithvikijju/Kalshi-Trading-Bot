"""Parquet shard writer.

Rotates one parquet file per (venue, msg_type, UTC hour). Buffers rows in
memory and flushes either when buffer hits MAX_BUFFER or when the rotation
clock ticks. Flush is atomic: write to .tmp then rename. Existing hour
shards are merged (read + append + rewrite) so the file remains a single
queryable artifact.

Schema (all venues, all msg types — raw payload column carries the rest):
    ts_us       INT64  microseconds since UTC epoch (exchange-stamped if available, else recv)
    recv_us     INT64  local recv time microseconds
    venue       STRING kalshi | polymarket
    msg_type    STRING orderbook_snapshot | orderbook_delta | ticker | trade | book | price_change | last_trade_price | ...
    market_id   STRING kalshi ticker / polymarket condition_id
    asset_id    STRING polymarket token_id (empty for kalshi)
    side        STRING yes / no / buy / sell / "" if N/A
    price       DOUBLE top-of-book or trade price ($), nullable
    size        DOUBLE size (contracts or shares), nullable
    payload     STRING raw JSON of the original message
"""
from __future__ import annotations
import asyncio, json, os, threading, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA = pa.schema([
    ("ts_us",     pa.int64()),
    ("recv_us",   pa.int64()),
    ("venue",     pa.string()),
    ("msg_type",  pa.string()),
    ("market_id", pa.string()),
    ("asset_id",  pa.string()),
    ("side",      pa.string()),
    ("price",     pa.float64()),
    ("size",      pa.float64()),
    ("payload",   pa.string()),
])

# Flush either when buffer hits this many rows, or this many seconds elapse.
MAX_BUFFER_ROWS  = 2000
FLUSH_INTERVAL_S = 10.0


class ParquetWriter:
    """Thread-safe row buffer that rolls into hour-bucketed parquet files."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._buf: List[dict] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._stop = False
        self._counters: Dict[str, int] = {}

    def write(self, row: dict) -> None:
        with self._lock:
            self._buf.append(row)
            k = f"{row['venue']}/{row['msg_type']}"
            self._counters[k] = self._counters.get(k, 0) + 1
            if len(self._buf) >= MAX_BUFFER_ROWS:
                self._flush_locked()

    def write_many(self, rows: List[dict]) -> None:
        if not rows:
            return
        with self._lock:
            self._buf.extend(rows)
            for r in rows:
                k = f"{r['venue']}/{r['msg_type']}"
                self._counters[k] = self._counters.get(k, 0) + 1
            if len(self._buf) >= MAX_BUFFER_ROWS:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def counters(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def _flush_locked(self) -> None:
        if not self._buf:
            self._last_flush = time.monotonic()
            return
        rows, self._buf = self._buf, []
        self._last_flush = time.monotonic()

        # Group rows by (venue, msg_type, dt, hr) shard.
        shards: Dict[tuple, List[dict]] = {}
        for r in rows:
            ts_us = int(r["ts_us"])
            dt = datetime.fromtimestamp(ts_us / 1_000_000, tz=timezone.utc)
            key = (r["venue"], r["msg_type"], dt.strftime("%Y-%m-%d"), dt.strftime("%H"))
            shards.setdefault(key, []).append(r)

        for (venue, mt, day, hr), shard_rows in shards.items():
            self._write_shard(venue, mt, day, hr, shard_rows)

    def _write_shard(self, venue: str, mt: str, day: str, hr: str,
                     rows: List[dict]) -> None:
        out_dir = self.root / venue / mt / f"dt={day}" / f"hr={hr}"
        out_dir.mkdir(parents=True, exist_ok=True)
        # One file per writer-lifetime per hour: stable name keeps DuckDB scans light.
        out_path = out_dir / "part.parquet"
        tmp_path = out_dir / f".part.{os.getpid()}.{int(time.time()*1000)}.tmp.parquet"

        # If the file exists, append by reading + concatenating. Otherwise write fresh.
        try:
            new_table = pa.Table.from_pylist(rows, schema=SCHEMA)
        except Exception:
            # Fallback: coerce by row to tolerate stray Nones.
            cleaned = [{k: r.get(k) for k in SCHEMA.names} for r in rows]
            new_table = pa.Table.from_pylist(cleaned, schema=SCHEMA)

        if out_path.exists():
            try:
                existing = pq.read_table(out_path, schema=SCHEMA)
                combined = pa.concat_tables([existing, new_table])
            except Exception:
                combined = new_table
        else:
            combined = new_table

        pq.write_table(combined, tmp_path, compression="zstd",
                       use_dictionary=True, version="2.6")
        os.replace(tmp_path, out_path)

    async def periodic_flusher(self) -> None:
        while not self._stop:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            try:
                self.flush()
            except Exception as e:
                print(f"[writer] flush err: {e}", flush=True)

    def stop(self) -> None:
        self._stop = True
        try:
            self.flush()
        except Exception as e:
            print(f"[writer] final flush err: {e}", flush=True)


def now_us() -> int:
    return int(time.time() * 1_000_000)


def to_us(iso_str: Optional[str]) -> Optional[int]:
    if not iso_str:
        return None
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000)
    except Exception:
        return None


def safe_dumps(obj) -> str:
    try:
        return json.dumps(obj, separators=(",", ":"), default=str)
    except Exception:
        return "{}"
