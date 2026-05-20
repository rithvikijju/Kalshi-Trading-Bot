from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any

import requests


GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"


def utc_now_tag() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def parse_time(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    if " " in cleaned and "+" in cleaned and "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T")
    return int(dt.datetime.fromisoformat(cleaned).timestamp())


def as_list_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return json.loads(value)


def get_json(session: requests.Session, url: str, params: dict[str, Any], retries: int = 3) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=30)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(0.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # pragma: no cover - network guard
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GET failed url={url} params={params}: {last_exc}")


def search_markets(session: requests.Session, query: str, limit: int) -> list[dict[str, Any]]:
    payload = get_json(session, f"{GAMMA}/public-search", {"q": query, "limit": limit})
    rows: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        for market in event.get("markets", []) or []:
            token_ids = as_list_json(market.get("clobTokenIds"))
            outcomes = as_list_json(market.get("outcomes"))
            if len(token_ids) != len(outcomes) or not token_ids:
                continue
            title = market.get("question") or event.get("title") or ""
            slug = market.get("slug") or event.get("slug") or ""
            if "Bitcoin Up or Down" not in title:
                continue
            rows.append(
                {
                    "event_id": event.get("id"),
                    "market_id": market.get("id"),
                    "ticker": event.get("ticker"),
                    "slug": slug,
                    "title": title,
                    "condition_id": market.get("conditionId"),
                    "event_start_ts": parse_time(market.get("eventStartTime")),
                    "end_ts": parse_time(market.get("endDate")),
                    "closed_time_ts": parse_time(market.get("closedTime")),
                    "closed": bool(market.get("closed")),
                    "volume": float(market.get("volumeNum") or market.get("volume") or 0.0),
                    "outcomes": outcomes,
                    "outcome_prices": as_list_json(market.get("outcomePrices")),
                    "token_ids": token_ids,
                    "maker_base_fee": market.get("makerBaseFee"),
                    "taker_base_fee": market.get("takerBaseFee"),
                    "order_min_size": market.get("orderMinSize"),
                    "order_price_min_tick_size": market.get("orderPriceMinTickSize"),
                    "spread": market.get("spread"),
                    "last_trade_price": market.get("lastTradePrice"),
                    "best_bid": market.get("bestBid"),
                    "best_ask": market.get("bestAsk"),
                }
            )
    rows.sort(key=lambda r: (r["end_ts"] or 0, r["volume"]), reverse=True)
    return rows


def fetch_price_history(
    session: requests.Session,
    token_id: str,
    start_ts: int,
    end_ts: int,
) -> tuple[list[dict[str, Any]], str]:
    # Polymarket's API rejects 1-minute fidelity for some lookback windows and
    # silently returns empty rows for others. Try precise recent settings first,
    # then fall back to 5-minute history for older markets.
    attempts = [
        ("1d", 1),
        ("6h", 1),
        ("1w", 5),
        ("all", 5),
        ("max", 5),
        ("1m", 10),
    ]
    for interval, fidelity in attempts:
        payload = get_json(
            session,
            f"{CLOB}/prices-history",
            {
                "market": token_id,
                "startTs": start_ts,
                "endTs": end_ts,
                "interval": interval,
                "fidelity": fidelity,
            },
        )
        history = payload.get("history", [])
        filtered = [row for row in history if start_ts <= int(row.get("t", 0)) <= end_ts]
        if filtered:
            return filtered, f"{interval}/fidelity={fidelity}"
    return [], "none"


def fetch_public_trades(
    session: requests.Session,
    condition_id: str,
    start_ts: int,
    end_ts: int,
    max_pages: int,
    split_truncated: bool,
    depth: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    limit = 500
    truncated = False
    for page in range(max_pages):
        try:
            payload = get_json(
                session,
                f"{DATA_API}/trades",
                {
                    "market": condition_id,
                    "after": start_ts,
                    "before": end_ts,
                    "limit": limit,
                    "offset": page * limit,
                },
            )
        except RuntimeError as exc:
            # The Data API currently rejects some deeper offsets with HTTP 400.
            # Keep the already-pulled rows and mark the market as truncated.
            print(f"trade pagination stopped for {condition_id} page={page}: {exc}")
            truncated = True
            break
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        if len(payload) < limit:
            break
        if page + 1 == max_pages:
            truncated = True
    filtered = [row for row in rows if start_ts <= int(row.get("timestamp", 0)) <= end_ts]
    if split_truncated and truncated and depth < 8 and end_ts - start_ts > 60:
        midpoint = (start_ts + end_ts) // 2
        left, left_truncated = fetch_public_trades(
            session, condition_id, start_ts, midpoint, max_pages, split_truncated, depth + 1
        )
        right, right_truncated = fetch_public_trades(
            session, condition_id, midpoint + 1, end_ts, max_pages, split_truncated, depth + 1
        )
        dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in left + right:
            key = (
                row.get("transactionHash"),
                row.get("timestamp"),
                row.get("asset"),
                row.get("side"),
                row.get("price"),
                row.get("size"),
                row.get("proxyWallet"),
            )
            dedup[key] = row
        return list(dedup.values()), left_truncated or right_truncated
    return filtered, truncated


def winning_outcome(outcomes: list[Any], outcome_prices: list[Any]) -> str | None:
    best_idx = None
    best_value = -math.inf
    for idx, price in enumerate(outcome_prices):
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        if value > best_value:
            best_value = value
            best_idx = idx
    if best_idx is None or best_value < 0.99:
        return None
    return str(outcomes[best_idx])


def nearest_trade_diffs(price_rows: list[dict[str, Any]], trades: list[dict[str, Any]], token_id: str) -> list[float]:
    token_trades = [
        (int(row["timestamp"]), float(row["price"]))
        for row in trades
        if str(row.get("asset")) == token_id and row.get("price") is not None
    ]
    diffs: list[float] = []
    for price in price_rows:
        t = int(price["t"])
        p = float(price["p"])
        near = [(abs(t - tt), tp) for tt, tp in token_trades if abs(t - tt) <= 120]
        if near:
            near.sort(key=lambda x: x[0])
            diffs.append(abs(p - near[0][1]))
    return diffs


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull a small Polymarket BTC CLOB historical slice.")
    parser.add_argument("--query", default="Bitcoin Up or Down April 15")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--markets", type=int, default=5)
    parser.add_argument("--only-15m", action="store_true")
    parser.add_argument("--max-trade-pages", type=int, default=4)
    parser.add_argument("--split-truncated-trades", action="store_true")
    parser.add_argument("--pre-buffer-seconds", type=int, default=900)
    parser.add_argument("--post-buffer-seconds", type=int, default=600)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    output_dir = Path(args.output_dir or f".codex_work/polymarket_btc_clob_slice_{utc_now_tag()}")
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "kalshi-polymarket-research/0.1"})

    candidates = search_markets(session, args.query, args.limit)
    if args.only_15m:
        candidates = [row for row in candidates if "btc-updown-15m" in str(row["slug"])]
    selected = candidates[: args.markets]
    if not selected:
        raise SystemExit(f"No markets found for query={args.query!r}")

    market_rows: list[dict[str, Any]] = []
    price_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for market in selected:
        start_ts = int(market["event_start_ts"] or ((market["end_ts"] or 0) - 900))
        end_ts = int(market["end_ts"] or (start_ts + 900))
        pull_start = start_ts - args.pre_buffer_seconds
        pull_end = end_ts + args.post_buffer_seconds
        win = winning_outcome(market["outcomes"], market["outcome_prices"])
        print(f"pull {market['slug']} start={start_ts} end={end_ts} winner={win}", flush=True)

        trades, trades_truncated = fetch_public_trades(
            session,
            market["condition_id"],
            pull_start,
            pull_end,
            args.max_trade_pages,
            args.split_truncated_trades,
        )
        trade_rows.extend(
            {
                "slug": market["slug"],
                "condition_id": market["condition_id"],
                "timestamp": row.get("timestamp"),
                "asset": row.get("asset"),
                "outcome": row.get("outcome"),
                "side": row.get("side"),
                "price": row.get("price"),
                "size": row.get("size"),
                "transaction_hash": row.get("transactionHash"),
                "proxy_wallet": row.get("proxyWallet"),
            }
            for row in trades
        )

        per_token_counts: dict[str, int] = {}
        per_token_sources: dict[str, str] = {}
        nearest_diffs: list[float] = []
        for outcome, token_id in zip(market["outcomes"], market["token_ids"]):
            history, source = fetch_price_history(session, str(token_id), pull_start, pull_end)
            per_token_counts[str(outcome)] = len(history)
            per_token_sources[str(outcome)] = source
            nearest_diffs.extend(nearest_trade_diffs(history, trades, str(token_id)))
            price_rows.extend(
                {
                    "slug": market["slug"],
                    "condition_id": market["condition_id"],
                    "token_id": token_id,
                    "outcome": outcome,
                    "t": row.get("t"),
                    "p": row.get("p"),
                    "source": source,
                }
                for row in history
            )

        summary_rows.append(
            {
                "slug": market["slug"],
                "title": market["title"],
                "start_utc": dt.datetime.fromtimestamp(start_ts, dt.timezone.utc).isoformat(),
                "end_utc": dt.datetime.fromtimestamp(end_ts, dt.timezone.utc).isoformat(),
                "winner": win,
                "volume": market["volume"],
                "price_rows": sum(per_token_counts.values()),
                "trade_rows": len(trades),
                "trades_truncated": trades_truncated,
                "price_sources": json.dumps(per_token_sources, sort_keys=True),
                "nearest_trade_pairs": len(nearest_diffs),
                "median_abs_price_vs_trade": (
                    sorted(nearest_diffs)[len(nearest_diffs) // 2] if nearest_diffs else ""
                ),
            }
        )
        market_rows.append(
            {
                **{k: v for k, v in market.items() if k not in {"outcomes", "outcome_prices", "token_ids"}},
                "outcomes": json.dumps(market["outcomes"]),
                "outcome_prices": json.dumps(market["outcome_prices"]),
                "token_ids": json.dumps(market["token_ids"]),
                "winner": win,
            }
        )

    write_csv(
        output_dir / "markets.csv",
        market_rows,
        [
            "event_id",
            "market_id",
            "ticker",
            "slug",
            "title",
            "condition_id",
            "event_start_ts",
            "end_ts",
            "closed_time_ts",
            "closed",
            "volume",
            "outcomes",
            "outcome_prices",
            "token_ids",
            "winner",
            "maker_base_fee",
            "taker_base_fee",
            "order_min_size",
            "order_price_min_tick_size",
            "spread",
            "last_trade_price",
            "best_bid",
            "best_ask",
        ],
    )
    write_csv(output_dir / "prices.csv", price_rows, ["slug", "condition_id", "token_id", "outcome", "t", "p", "source"])
    write_csv(
        output_dir / "trades.csv",
        trade_rows,
        [
            "slug",
            "condition_id",
            "timestamp",
            "asset",
            "outcome",
            "side",
            "price",
            "size",
            "transaction_hash",
            "proxy_wallet",
        ],
    )
    write_csv(
        output_dir / "summary.csv",
        summary_rows,
        [
            "slug",
            "title",
            "start_utc",
            "end_utc",
            "winner",
            "volume",
            "price_rows",
            "trade_rows",
            "trades_truncated",
            "price_sources",
            "nearest_trade_pairs",
            "median_abs_price_vs_trade",
        ],
    )
    (output_dir / "README.md").write_text(
        "Polymarket BTC CLOB slice\n"
        "=========================\n\n"
        f"Query: `{args.query}`\n\n"
        "Files:\n"
        "- `markets.csv`: Gamma metadata, CLOB token IDs, settlement proxy from `outcomePrices`.\n"
        "- `prices.csv`: CLOB `/prices-history` rows for each token.\n"
        "- `trades.csv`: public Data API trade tape rows filtered to the event window.\n"
        "- `summary.csv`: row counts and price/trade alignment diagnostics.\n\n"
        "Important limitation: this is not a historical orderbook replay. It has price samples and executed trades, "
        "but not resting depth, cancellations, quote lifetime, queue position, or failed FOK state.\n",
        encoding="utf-8",
    )

    print(f"wrote {output_dir}")
    print("summary")
    for row in summary_rows:
        print(
            f"{row['slug']} winner={row['winner']} price_rows={row['price_rows']} "
            f"trades={row['trade_rows']} truncated={row['trades_truncated']} "
            f"median_abs_price_vs_trade={row['median_abs_price_vs_trade']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
