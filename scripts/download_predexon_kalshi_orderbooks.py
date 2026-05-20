#!/usr/bin/env python3
"""Download Predexon Kalshi historical orderbook snapshots to Parquet.

This is a research/backfill source, not a live-replay replacement. Predexon
provides historical snapshots with provider timestamps, prices in cents, and
full YES depth. We store both top-of-book and optional depth levels with a
manifest so later backtests can distinguish these snapshots from our own
receive-timestamped websocket capture.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "predexon_kalshi_orderbooks"
DEFAULT_BTC15M_MARKETS = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "kalshi_markets.parquet"
DEFAULT_BTC1H_MARKETS = PROJECT_ROOT / "data" / "research_datamart" / "kalshi_markets.parquet"
BASE_URL = "https://api.predexon.com"


@dataclass(frozen=True)
class MarketWindow:
    market_ticker: str
    event_ticker: str
    series_ticker: str
    open_time: pd.Timestamp
    close_time: pd.Timestamp
    request_start: pd.Timestamp
    request_end: pd.Timestamp
    priority_score: float


class RateLimiter:
    def __init__(self, rps: float) -> None:
        self.delay = 1.0 / max(float(rps), 0.01)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
            self.next_at = time.monotonic() + self.delay


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull Predexon Kalshi orderbook history into compressed Parquet parts."
    )
    parser.add_argument("--api-key", default=None, help="Predexon API key. Prefer env PREDEXON_API_KEY.")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / "credentials.env")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--series",
        nargs="+",
        default=["KXBTC15M", "KXBTCD"],
        help="Kalshi series to discover from Predexon when --market-file is not supplied.",
    )
    parser.add_argument(
        "--market-file",
        action="append",
        type=Path,
        help="Local markets parquet/CSV to use instead of Predexon market discovery. Can be repeated.",
    )
    parser.add_argument("--tickers", nargs="+", help="Explicit market tickers. Requires --start and --end.")
    parser.add_argument("--start", required=True, help="Inclusive ISO time/date or Unix ms.")
    parser.add_argument("--end", required=True, help="Exclusive ISO time/date or Unix ms.")
    parser.add_argument("--status", choices=["open", "closed", "all"], default="closed")
    parser.add_argument("--window", choices=["full", "late"], default="late")
    parser.add_argument(
        "--late-minutes",
        type=float,
        default=20.0,
        help="When --window late, request only close_time - this many minutes through close_time.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Predexon page size, max 200.")
    parser.add_argument("--rps", type=float, default=1.0, help="Total request rate. Free plan is 1 req/sec.")
    parser.add_argument("--workers", type=int, default=1, help="Use >1 only if your plan permits higher RPS.")
    parser.add_argument("--max-tickers", type=int, default=None)
    parser.add_argument(
        "--max-markets-per-event",
        type=int,
        default=12,
        help="Cap markets per event after priority sorting. Use 9999 for all markets.",
    )
    parser.add_argument(
        "--priority",
        choices=["recent_liquid", "chronological", "reverse_chronological"],
        default="recent_liquid",
    )
    parser.add_argument("--write-levels", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-after-requests", type=int, default=None)
    return parser.parse_args()


def load_api_key(args: argparse.Namespace) -> str | None:
    if args.api_key:
        return args.api_key.strip()
    for name in ["PREDEXON_API_KEY", "PREDEXON_KEY"]:
        value = os.environ.get(name)
        if value:
            return value.strip()
    if args.env_file.exists():
        for line in args.env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key.strip().upper() in {"PREDEXON_API_KEY", "PREDEXON_KEY"}:
                return value.strip().strip('"').strip("'")
    return None


def parse_time(value: str) -> pd.Timestamp:
    raw = str(value).strip()
    if raw.isdigit():
        integer = int(raw)
        unit = "ms" if integer > 10_000_000_000 else "s"
        return pd.Timestamp(integer, unit=unit, tz="UTC")
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def ts_ms(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).tz_convert("UTC").timestamp() * 1000)


def iso_safe(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y%m%dT%H%M%SZ")


def request_json(
    session: requests.Session,
    path: str,
    *,
    params: dict[str, Any],
    rate: RateLimiter,
    max_attempts: int = 6,
) -> dict[str, Any]:
    url = BASE_URL + path
    for attempt in range(max_attempts):
        rate.wait()
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            if attempt == max_attempts - 1:
                raise RuntimeError(f"request failed after retries: {type(exc).__name__}") from exc
            time.sleep(min(30.0, 2.0**attempt) + random.random())
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < max_attempts - 1:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(30.0, 2.0**attempt)
            time.sleep(delay + random.random())
            continue
        if response.status_code == 401:
            raise RuntimeError("Predexon returned 401 Unauthorized. Add PREDEXON_API_KEY to credentials.env or env.")
        if response.status_code == 403:
            raise RuntimeError("Predexon returned 403 Forbidden. Check API key/plan access.")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text[:500].replace("\n", " ")
            raise RuntimeError(f"Predexon {response.status_code} for {path}: {body}") from exc
        return response.json()
    raise RuntimeError(f"request failed after {max_attempts} attempts: {path}")


def predexon_session(api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "x-api-key": api_key,
            "user-agent": "Kalshi-Trading-Bot research downloader (REST only)",
            "accept": "application/json",
        }
    )
    return session


def normalize_market_frame(df: pd.DataFrame) -> pd.DataFrame:
    # Local manifests can already contain normalized columns next to the raw
    # Kalshi names. Avoid creating duplicate columns during the compatibility
    # rename step, because pandas then returns a DataFrame for df[col].
    df = df.copy()
    for raw, normalized in [
        ("ticker", "market_ticker"),
        ("yes_subtitle", "yes_sub_title"),
        ("no_subtitle", "no_sub_title"),
    ]:
        if raw in df.columns and normalized in df.columns:
            df = df.drop(columns=[raw])
    rename = {
        "ticker": "market_ticker",
        "yes_subtitle": "yes_sub_title",
        "no_subtitle": "no_sub_title",
    }
    df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated()].copy()
    if "event" in df.columns and "series_ticker" not in df.columns:
        df["series_ticker"] = df["event"].map(lambda x: (x or {}).get("series_ticker") if isinstance(x, dict) else None)
    if "event" in df.columns and "event_ticker" not in df.columns:
        df["event_ticker"] = df["event"].map(lambda x: (x or {}).get("event_ticker") if isinstance(x, dict) else None)
    for col in ["market_ticker", "event_ticker", "series_ticker"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype(str).str.upper()
    for col in ["open_time", "close_time", "expected_expiration_time", "settlement_time", "determination_time", "created_at", "updated_at"]:
        if col not in df.columns:
            df[col] = pd.NaT
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    for col in ["volume", "open_interest", "dollar_volume", "dollar_open_interest", "last_price"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    if "floor_strike" not in df.columns:
        df["floor_strike"] = pd.NA
    df["floor_strike"] = pd.to_numeric(df["floor_strike"], errors="coerce")
    return df.dropna(subset=["market_ticker", "open_time", "close_time"])


def discover_markets_predexon(
    session: requests.Session,
    *,
    series: list[str],
    status: str,
    rate: RateLimiter,
    limit: int,
) -> pd.DataFrame:
    frames = []
    for series_ticker in series:
        cursor = None
        pages = 0
        while True:
            params: dict[str, Any] = {
                "series_ticker": series_ticker.upper(),
                "sort": "close_time",
                "limit": min(max(int(limit), 1), 100),
            }
            if status != "all":
                params["status"] = status
            if cursor:
                params["pagination_key"] = cursor
            data = request_json(session, "/v2/kalshi/markets", params=params, rate=rate)
            markets = data.get("markets") or []
            if markets:
                frames.append(pd.DataFrame(markets))
            pages += 1
            pagination = data.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            cursor = pagination.get("pagination_key")
            if not cursor:
                break
            print(f"discovered {series_ticker} pages={pages} markets={sum(len(x) for x in frames):,}", flush=True)
    if not frames:
        return pd.DataFrame()
    return normalize_market_frame(pd.concat(frames, ignore_index=True))


def load_local_markets(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        if not path.exists():
            print(f"warning: missing market file {path}", file=sys.stderr)
            continue
        if path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        elif path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        else:
            raise ValueError(f"unsupported market file type: {path}")
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return normalize_market_frame(pd.concat(frames, ignore_index=True))


def explicit_ticker_markets(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        ticker_u = ticker.upper()
        event = infer_event_ticker(ticker_u)
        rows.append(
            {
                "market_ticker": ticker_u,
                "event_ticker": event,
                "series_ticker": ticker_u.split("-", 1)[0],
                "open_time": start,
                "close_time": end,
                "volume": 0,
                "open_interest": 0,
                "dollar_volume": 0,
                "dollar_open_interest": 0,
                "last_price": 0,
            }
        )
    return normalize_market_frame(pd.DataFrame(rows))


def infer_event_ticker(market_ticker: str) -> str:
    ticker = str(market_ticker).upper()
    if "-T" in ticker:
        return ticker.split("-T", 1)[0]
    parts = ticker.split("-")
    if len(parts) >= 3 and parts[0] == "KXBTC15M":
        return "-".join(parts[:2])
    return ticker


def select_windows(df: pd.DataFrame, args: argparse.Namespace, start: pd.Timestamp, end: pd.Timestamp) -> list[MarketWindow]:
    if df.empty:
        return []
    df = df.drop_duplicates("market_ticker").copy()
    df = df[df["series_ticker"].isin([x.upper() for x in args.series]) | df["market_ticker"].isin([*(args.tickers or [])])].copy()
    df = df[(df["close_time"] > start) & (df["open_time"] < end)].copy()
    if df.empty:
        return []
    df["priority_score"] = (
        pd.to_numeric(df.get("dollar_volume", 0), errors="coerce").fillna(0.0)
        + pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0.0)
        + 0.1 * pd.to_numeric(df.get("dollar_open_interest", 0), errors="coerce").fillna(0.0)
        + 0.1 * pd.to_numeric(df.get("open_interest", 0), errors="coerce").fillna(0.0)
    )
    median_strike = df.groupby("event_ticker")["floor_strike"].transform("median")
    strike_distance = (df["floor_strike"] - median_strike).abs()
    df["atm_score"] = -strike_distance.fillna(1e12)
    df["selection_score"] = np_where_priority(df["priority_score"], df["atm_score"])
    if args.max_markets_per_event and args.max_markets_per_event < 9999:
        df = (
            df.sort_values(["event_ticker", "selection_score", "priority_score", "close_time"], ascending=[True, False, False, False])
            .groupby("event_ticker", as_index=False, group_keys=False)
            .head(args.max_markets_per_event)
        )
    windows: list[MarketWindow] = []
    for row in df.itertuples(index=False):
        open_time = pd.Timestamp(row.open_time).tz_convert("UTC")
        close_time = pd.Timestamp(row.close_time).tz_convert("UTC")
        if args.window == "late":
            req_start = close_time - pd.Timedelta(minutes=float(args.late_minutes))
        else:
            req_start = open_time
        req_start = max(req_start, start)
        req_end = min(close_time, end)
        if req_start >= req_end:
            continue
        windows.append(
            MarketWindow(
                market_ticker=str(row.market_ticker).upper(),
                event_ticker=str(row.event_ticker).upper(),
                series_ticker=str(row.series_ticker).upper(),
                open_time=open_time,
                close_time=close_time,
                request_start=req_start,
                request_end=req_end,
                priority_score=float(getattr(row, "selection_score", 0.0) or 0.0),
            )
        )
    if args.priority == "chronological":
        windows.sort(key=lambda w: (w.close_time, w.series_ticker, -w.priority_score))
    elif args.priority == "reverse_chronological":
        windows.sort(key=lambda w: (w.close_time, w.series_ticker, w.priority_score), reverse=True)
    else:
        series_rank = {"KXBTC15M": 0, "KXBTCD": 1}
        windows.sort(key=lambda w: (series_rank.get(w.series_ticker, 9), -w.priority_score, -ts_ms(w.close_time)))
    if args.max_tickers:
        windows = windows[: args.max_tickers]
    return windows


def np_where_priority(priority: pd.Series, fallback: pd.Series) -> pd.Series:
    """Use liquidity priority when present, otherwise near-center strike score."""
    priority = pd.to_numeric(priority, errors="coerce").fillna(0.0)
    fallback = pd.to_numeric(fallback, errors="coerce").fillna(-1e12)
    return priority.where(priority.gt(0.0), fallback)


def output_paths(out_dir: Path, window: MarketWindow) -> tuple[Path, Path]:
    date = window.close_time.strftime("%Y-%m-%d")
    root = out_dir / f"series={window.series_ticker}" / f"date={date}" / window.event_ticker
    root.mkdir(parents=True, exist_ok=True)
    suffix = f"{iso_safe(window.request_start)}_{iso_safe(window.request_end)}"
    return root / f"{window.market_ticker}.{suffix}.top.parquet", root / f"{window.market_ticker}.{suffix}.levels.parquet"


def cents_to_prob(value: Any) -> float | None:
    if value is None:
        return None
    try:
        cents = float(value)
    except (TypeError, ValueError):
        return None
    if cents <= 0:
        return None
    return cents / 100.0


def snapshot_rows(snapshot: dict[str, Any], window: MarketWindow, downloaded_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timestamp_ms = int(snapshot.get("timestamp"))
    yes_bids = snapshot.get("yes_bids") or []
    yes_asks = snapshot.get("yes_asks") or []
    best_bid = snapshot.get("best_bid")
    best_ask = snapshot.get("best_ask")
    top = {
        "provider": "predexon",
        "fidelity": "historical_snapshot_provider_time",
        "ticker": str(snapshot.get("ticker") or window.market_ticker).upper(),
        "market_ticker": window.market_ticker,
        "event_ticker": window.event_ticker,
        "series_ticker": window.series_ticker,
        "timestamp_ms": timestamp_ms,
        "timestamp_utc": pd.Timestamp(timestamp_ms, unit="ms", tz="UTC"),
        "sequence": snapshot.get("sequence"),
        "best_bid_cents": best_bid,
        "best_ask_cents": best_ask,
        "yes_bid": cents_to_prob(best_bid),
        "yes_ask": cents_to_prob(best_ask),
        "no_bid": cents_to_prob(100 - float(best_ask)) if best_ask is not None and float(best_ask) > 0 else None,
        "no_ask": cents_to_prob(100 - float(best_bid)) if best_bid is not None and float(best_bid) > 0 else None,
        "bid_depth": snapshot.get("bid_depth"),
        "ask_depth": snapshot.get("ask_depth"),
        "yes_bid_levels": len(yes_bids),
        "yes_ask_levels": len(yes_asks),
        "request_start_utc": window.request_start,
        "request_end_utc": window.request_end,
        "downloaded_at_utc": downloaded_at,
    }
    levels: list[dict[str, Any]] = []
    for side, values in [("yes_bid", yes_bids), ("yes_ask", yes_asks)]:
        for idx, level in enumerate(values):
            if not isinstance(level, dict):
                continue
            levels.append(
                {
                    "provider": "predexon",
                    "market_ticker": window.market_ticker,
                    "event_ticker": window.event_ticker,
                    "series_ticker": window.series_ticker,
                    "timestamp_ms": timestamp_ms,
                    "timestamp_utc": pd.Timestamp(timestamp_ms, unit="ms", tz="UTC"),
                    "sequence": snapshot.get("sequence"),
                    "side": side,
                    "level": idx,
                    "price_cents": level.get("price"),
                    "price": cents_to_prob(level.get("price")),
                    "size": level.get("size"),
                    "request_start_utc": window.request_start,
                    "request_end_utc": window.request_end,
                    "downloaded_at_utc": downloaded_at,
                }
            )
    return top, levels


def to_parquet_safe(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=False, compression="zstd")
    except Exception:
        df.to_parquet(path, index=False, compression="snappy")


def download_window(
    session: requests.Session,
    window: MarketWindow,
    *,
    args: argparse.Namespace,
    rate: RateLimiter,
    request_counter: threading.Semaphore | None,
) -> dict[str, Any]:
    top_path, levels_path = output_paths(args.out_dir, window)
    if top_path.exists() and (levels_path.exists() or not args.write_levels) and not args.overwrite:
        return {
            "market_ticker": window.market_ticker,
            "event_ticker": window.event_ticker,
            "status": "skipped_exists",
            "top_path": str(top_path),
            "levels_path": str(levels_path) if args.write_levels else "",
        }

    cursor = None
    pages = 0
    total_requests = 0
    top_rows: list[dict[str, Any]] = []
    level_rows: list[dict[str, Any]] = []
    downloaded_at = datetime.now(timezone.utc).isoformat()
    while True:
        if request_counter is not None:
            if not request_counter.acquire(blocking=False):
                break
        params: dict[str, Any] = {
            "ticker": window.market_ticker,
            "start_time": ts_ms(window.request_start),
            "end_time": ts_ms(window.request_end),
            "limit": min(max(int(args.limit), 1), 200),
        }
        if cursor:
            params["pagination_key"] = cursor
        data = request_json(session, "/v2/kalshi/orderbooks", params=params, rate=rate)
        total_requests += 1
        pages += 1
        snapshots = data.get("snapshots") or []
        for snapshot in snapshots:
            top, levels = snapshot_rows(snapshot, window, downloaded_at)
            top_rows.append(top)
            if args.write_levels:
                level_rows.extend(levels)
        pagination = data.get("pagination") or {}
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("pagination_key")
        if not cursor:
            break

    if top_rows:
        top_df = pd.DataFrame(top_rows).drop_duplicates(["market_ticker", "timestamp_ms", "sequence"])
        top_df = top_df.sort_values(["market_ticker", "timestamp_ms", "sequence"])
        to_parquet_safe(top_df, top_path)
    else:
        top_df = pd.DataFrame()
    if args.write_levels:
        if level_rows:
            levels_df = pd.DataFrame(level_rows).drop_duplicates(["market_ticker", "timestamp_ms", "sequence", "side", "level"])
            levels_df = levels_df.sort_values(["market_ticker", "timestamp_ms", "sequence", "side", "level"])
            to_parquet_safe(levels_df, levels_path)
    return {
        "market_ticker": window.market_ticker,
        "event_ticker": window.event_ticker,
        "series_ticker": window.series_ticker,
        "status": "ok" if top_rows else "ok_empty",
        "snapshots": int(len(top_rows)),
        "requests": int(total_requests),
        "pages": int(pages),
        "request_start_utc": str(window.request_start),
        "request_end_utc": str(window.request_end),
        "min_timestamp_utc": str(top_df["timestamp_utc"].min()) if not top_df.empty else "",
        "max_timestamp_utc": str(top_df["timestamp_utc"].max()) if not top_df.empty else "",
        "top_path": str(top_path) if top_rows else "",
        "levels_path": str(levels_path) if args.write_levels and level_rows else "",
    }


def write_manifest(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def write_market_metadata(out_dir: Path, markets: pd.DataFrame, windows: list[MarketWindow], start: pd.Timestamp, end: pd.Timestamp) -> Path | None:
    """Persist scalar market metadata needed to score downloaded snapshots later."""
    if markets.empty:
        return None
    selected = {w.market_ticker for w in windows}
    keep_cols = [
        "market_ticker",
        "event_ticker",
        "series_ticker",
        "market_id",
        "title",
        "status",
        "yes_sub_title",
        "no_sub_title",
        "result",
        "open_time",
        "close_time",
        "expected_expiration_time",
        "settlement_time",
        "determination_time",
        "can_close_early",
        "strike_type",
        "custom_strike",
        "floor_strike",
        "last_price",
        "volume",
        "open_interest",
        "dollar_volume",
        "dollar_open_interest",
        "created_at",
        "updated_at",
    ]
    out = markets.copy()
    for col in keep_cols:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[keep_cols].drop_duplicates("market_ticker", keep="last").copy()
    out["selected_for_download"] = out["market_ticker"].isin(selected)
    out["metadata_source"] = "predexon_kalshi_markets"
    out["metadata_written_at_utc"] = datetime.now(timezone.utc).isoformat()
    out["request_range_start_utc"] = start
    out["request_range_end_utc"] = end
    path = out_dir / "market_metadata" / f"markets_{iso_safe(start)}_{iso_safe(end)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    to_parquet_safe(out, path)
    return path


def main() -> int:
    args = parse_args()
    start = parse_time(args.start)
    end = parse_time(args.end)
    if start >= end:
        raise SystemExit("--start must be earlier than --end")

    api_key = load_api_key(args)
    market_files = args.market_file

    rate = RateLimiter(args.rps)
    session = predexon_session(api_key or "missing-key")

    if args.tickers:
        markets = explicit_ticker_markets(args.tickers, start, end)
    elif market_files:
        markets = load_local_markets(market_files)
        print(f"loaded local markets={len(markets):,} from {len(market_files)} file(s)")
    else:
        if not api_key:
            raise SystemExit("Need PREDEXON_API_KEY to discover markets from Predexon.")
        print(f"discovering markets from Predexon series={','.join(args.series)} status={args.status}")
        markets = discover_markets_predexon(session, series=args.series, status=args.status, rate=rate, limit=100)

    windows = select_windows(markets, args, start, end)
    print(
        f"selected windows={len(windows):,} "
        f"series={','.join(sorted({w.series_ticker for w in windows})) if windows else ''} "
        f"range={start} -> {end} window={args.window}"
    )
    if windows:
        preview = pd.DataFrame([w.__dict__ for w in windows[:20]])
        print(preview[["market_ticker", "event_ticker", "series_ticker", "request_start", "request_end", "priority_score"]].to_string(index=False))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = write_market_metadata(args.out_dir, markets, windows, start, end)
    if metadata_path:
        print(f"wrote market metadata: {metadata_path}", flush=True)
    (args.out_dir / "last_plan.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "start": str(start),
                "end": str(end),
                "series": args.series,
                "window": args.window,
                "late_minutes": args.late_minutes,
                "selected_windows": len(windows),
                "max_markets_per_event": args.max_markets_per_event,
                "priority": args.priority,
                "market_metadata_path": str(metadata_path) if metadata_path else "",
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    if args.dry_run:
        print("dry-run: no Predexon orderbook requests made")
        return 0
    if not api_key:
        raise SystemExit("Need PREDEXON_API_KEY in env or credentials.env before downloading.")

    manifest = args.out_dir / "manifest.jsonl"
    request_counter = threading.Semaphore(args.stop_after_requests) if args.stop_after_requests else None
    ok = 0
    failed = 0
    if args.workers <= 1:
        for idx, window in enumerate(windows, start=1):
            print(f"[{idx}/{len(windows)}] {window.market_ticker} {window.request_start} -> {window.request_end}", flush=True)
            try:
                row = download_window(session, window, args=args, rate=rate, request_counter=request_counter)
                ok += 1 if row.get("status") in {"ok", "skipped_exists"} else 0
            except Exception as exc:
                failed += 1
                row = {
                    "market_ticker": window.market_ticker,
                    "event_ticker": window.event_ticker,
                    "series_ticker": window.series_ticker,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            write_manifest(manifest, row)
            print(json.dumps(row, default=str), flush=True)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_window, session, w, args=args, rate=rate, request_counter=request_counter): w for w in windows}
            for fut in as_completed(futures):
                window = futures[fut]
                try:
                    row = fut.result()
                    ok += 1 if row.get("status") in {"ok", "skipped_exists"} else 0
                except Exception as exc:
                    failed += 1
                    row = {
                        "market_ticker": window.market_ticker,
                        "event_ticker": window.event_ticker,
                        "series_ticker": window.series_ticker,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                write_manifest(manifest, row)
                print(json.dumps(row, default=str), flush=True)
    print(f"done ok={ok} failed={failed} manifest={manifest}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
