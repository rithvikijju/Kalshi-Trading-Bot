#!/usr/bin/env python3
"""Fetch and normalize BTC-USD 1-minute Coinbase spot candles.

This is a small repairable cache builder for BTC15M research.  It accepts an
existing mixed/raw cache, dedupes by bucket_start, recomputes causal
available_at and realized-volatility features, and writes a normalized parquet.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


COINBASE_URL = "https://api.exchange.coinbase.com"
DEFAULT_OUT = Path("data/btc15m_historical_datamart/spot_1m.parquet")


def parse_ts(raw: str) -> pd.Timestamp:
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def normalize(df: pd.DataFrame, product_id: str = "BTC-USD") -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "bucket_start" not in out.columns and "time" in out.columns:
        out = out.rename(columns={"time": "bucket_start"})
    if "bucket_start" not in out.columns and "available_at" in out.columns:
        out["bucket_start"] = pd.to_datetime(out["available_at"], utc=True, errors="coerce") - pd.Timedelta(minutes=1)
    out["bucket_start"] = pd.to_datetime(out["bucket_start"], utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["bucket_start", "open", "high", "low", "close"])
    out = out.drop_duplicates("bucket_start", keep="last").sort_values("bucket_start").reset_index(drop=True)
    out["product_id"] = product_id
    out["asset"] = product_id.split("-")[0]
    out["available_at"] = out["bucket_start"] + pd.Timedelta(minutes=1)
    out["log_ret"] = np.log(out["close"] / out["close"].shift(1))
    annual_factor = 60 * 24 * 365
    out["rv_15m"] = out["log_ret"].rolling(15, min_periods=10).std() * np.sqrt(annual_factor)
    out["rv_60m"] = out["log_ret"].rolling(60, min_periods=20).std() * np.sqrt(annual_factor)
    out["rv_1d"] = out["log_ret"].rolling(1440, min_periods=240).std() * np.sqrt(annual_factor)
    out["rkurt_60m"] = out["log_ret"].rolling(60, min_periods=20).kurt()
    return out[
        [
            "product_id",
            "asset",
            "bucket_start",
            "available_at",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "log_ret",
            "rv_15m",
            "rv_60m",
            "rv_1d",
            "rkurt_60m",
        ]
    ]


def request_json(session: requests.Session, path: str, params: dict[str, Any], retries: int = 6) -> Any:
    url = COINBASE_URL + path
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(min(30.0, 2.0**attempt))
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < retries - 1:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(30.0, 2.0**attempt)
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"request failed after {retries} attempts: {path}")


def fetch_range(product_id: str, start: pd.Timestamp, end: pd.Timestamp, sleep_sec: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cursor = start.floor("min")
    chunks = int(np.ceil((end.ceil("min") - cursor).total_seconds() / (300 * 60)))
    with requests.Session() as session:
        i = 0
        while cursor < end:
            chunk_end = min(end.ceil("min"), cursor + pd.Timedelta(minutes=300))
            params = {
                "granularity": 60,
                "start": cursor.isoformat(),
                "end": chunk_end.isoformat(),
            }
            data = request_json(session, f"/products/{product_id}/candles", params=params)
            for item in data:
                if isinstance(item, list) and len(item) >= 6:
                    rows.append(
                        {
                            "bucket_start": pd.Timestamp(int(item[0]), unit="s", tz="UTC"),
                            "low": float(item[1]),
                            "high": float(item[2]),
                            "open": float(item[3]),
                            "close": float(item[4]),
                            "volume": float(item[5]),
                        }
                    )
            i += 1
            if i == 1 or i % 25 == 0:
                print(f"fetched chunk {i}/{chunks} through {chunk_end}", flush=True)
            cursor = chunk_end
            if sleep_sec > 0:
                time.sleep(sleep_sec)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--product-id", default="BTC-USD")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep-sec", type=float, default=0.02)
    args = ap.parse_args()

    start = parse_ts(args.start)
    end = parse_ts(args.end)
    existing = pd.DataFrame()
    if args.out.exists():
        existing = pd.read_parquet(args.out)
        existing = normalize(existing, args.product_id)
        print(
            f"existing normalized rows={len(existing):,} "
            f"{existing['available_at'].min() if not existing.empty else None} -> {existing['available_at'].max() if not existing.empty else None}",
            flush=True,
        )
    fetched = fetch_range(args.product_id, start, end, args.sleep_sec)
    combined = pd.concat([existing, fetched], ignore_index=True) if not existing.empty else fetched
    normalized = normalize(combined, args.product_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    normalized.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(args.out)
    print(
        f"wrote {args.out} rows={len(normalized):,} "
        f"{normalized['available_at'].min()} -> {normalized['available_at'].max()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
