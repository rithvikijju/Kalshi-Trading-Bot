/**
 * Data connector registry.
 *
 * Each entry describes an external feed the agent platform can use to source
 * alpha. Today they are mock — every connector exposes a `fetch()` that emits
 * synthetic but plausible signals. To wire a real API:
 *
 *   1. Replace the body of `fetch()` with an authenticated HTTP/WS call.
 *   2. Map the upstream response to the `ConnectorSample[]` shape returned here.
 *   3. (Optional) add a health/auth status into `DataSource.status`.
 *
 * Strategy engines consume `ConnectorSample[]`. They never speak to upstream
 * APIs directly — keeping the contract here means a single seam to swap
 * mock → live without touching agent code.
 */
import type { DataSource } from "./types";

export interface ConnectorSample {
  sourceId: string;
  ts: string;
  symbol: string;                 // canonical symbol for the universe
  field: string;                  // e.g. "yes_mid", "funding_rate", "yield_2y"
  value: number;
  meta?: Record<string, unknown>;
}

export const DEFAULT_DATA_SOURCES: DataSource[] = [
  // ── Prediction markets
  { id: "kalshi", name: "Kalshi", category: "prediction_market", status: "mock",
    description: "BTC, ETH, macro & sports binary markets",
    wireUpHint: "Replace with kalshi_v2/client.py REST + WS (RSA-PSS signed)." },
  { id: "polymarket", name: "Polymarket", category: "prediction_market", status: "mock",
    description: "On-chain CLOB binary markets, BTC/ETH/SOL/XRP 5-min",
    wireUpHint: "Gamma REST (markets/events) + wss://ws-subscriptions-clob.polymarket.com/ws/market" },
  // ── Crypto
  { id: "binance_perps", name: "Binance Perps", category: "crypto", status: "mock",
    description: "Perp funding & basis",
    wireUpHint: "Wire fapi.binance.com /fapi/v1/premiumIndex (no auth for reads)." },
  { id: "hyperliquid", name: "Hyperliquid", category: "crypto", status: "mock",
    description: "Cross-margin perps, info-only feed",
    wireUpHint: "api.hyperliquid.xyz /info endpoint." },
  { id: "deribit", name: "Deribit Options", category: "crypto", status: "mock",
    description: "BTC/ETH option surface",
    wireUpHint: "deribit.com /api/v2/public WS feed." },
  // ── Equity / private signal
  { id: "polygon_equities", name: "Polygon Equities", category: "equity", status: "mock",
    description: "Tick-level US equities & options",
    wireUpHint: "api.polygon.io with X-Auth-Token header." },
  { id: "alpaca", name: "Alpaca", category: "equity", status: "mock",
    description: "Bars + paper broker (real if creds present)",
    wireUpHint: "api.alpaca.markets v2; use paper endpoint until promoted." },
  { id: "sec_edgar", name: "SEC EDGAR", category: "filings", status: "mock",
    description: "8-K, 10-K, 13F, 13D/G",
    wireUpHint: "data.sec.gov/submissions/CIK########.json (free, no auth, set UA)." },
  { id: "private_jobs", name: "Private Co. Jobs Scrape", category: "alt", status: "mock",
    description: "Headcount growth from job boards",
    wireUpHint: "Greenhouse/Lever boards or LinkedIn paid sources." },
  { id: "app_store", name: "App Store Ranks", category: "alt", status: "mock",
    description: "Daily app downloads/ranks",
    wireUpHint: "Sensor Tower / app-store-scraper." },
  { id: "github_signals", name: "GitHub Signals", category: "alt", status: "mock",
    description: "Repo stars, commits, contributor growth",
    wireUpHint: "api.github.com /search & GraphQL." },
  // ── Macro & rates
  { id: "fred", name: "FRED", category: "macro", status: "mock",
    description: "Macro time series (rates, inflation, payrolls)",
    wireUpHint: "fred.stlouisfed.org JSON API." },
  { id: "treasury_direct", name: "TreasuryDirect", category: "macro", status: "mock",
    description: "Auction results & curve",
    wireUpHint: "treasurydirect.gov data API." },
  // ── News / social
  { id: "newsapi", name: "Newsapi/Bloomberg-mirror", category: "news", status: "mock",
    description: "Headlines + entity tagging",
    wireUpHint: "newsapi.org or a paid wire (Refinitiv/Benzinga)." },
  { id: "twitter_x", name: "X / Twitter", category: "social", status: "mock",
    description: "Cashtag firehose",
    wireUpHint: "v2 filtered stream — requires Pro tier." },
  { id: "reddit", name: "Reddit", category: "social", status: "mock",
    description: "Subreddit mention velocity",
    wireUpHint: "reddit.com/.json (rate-limited; use PRAW)." },
  { id: "google_trends", name: "Google Trends", category: "social", status: "mock",
    description: "Search interest",
    wireUpHint: "Unofficial pytrends; or paid SimilarWeb." },
  // ── Weather / on-chain / commodities
  { id: "nws", name: "NWS Weather", category: "weather", status: "mock",
    description: "Forecasts, alerts",
    wireUpHint: "api.weather.gov (free, no auth)." },
  { id: "etherscan", name: "Etherscan/Onchain", category: "onchain", status: "mock",
    description: "Token flows, whale moves",
    wireUpHint: "api.etherscan.io + Dune for derived series." },
];

/* ────────────────────────────────────────────────────────────────────────
   Mock signal generators — small deterministic-ish RNG seeded by ts to
   keep the dashboard 'breathing' with plausible numbers. Replace with
   real connector implementations as you wire each source.
   ──────────────────────────────────────────────────────────────────── */

function rng(seed: number) { // mulberry32
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Single-call mock fetch used by strategy engines. */
export function sampleConnector(sourceId: string, symbol: string): ConnectorSample {
  const r = rng((Date.now() / 1500 | 0) ^ hash(sourceId + symbol));
  const ts = new Date().toISOString();
  switch (sourceId) {
    case "kalshi":
    case "polymarket": {
      const yes_mid = clamp01(0.5 + (r() - 0.5) * 0.4);
      return { sourceId, ts, symbol, field: "yes_mid", value: yes_mid };
    }
    case "binance_perps":
    case "hyperliquid": {
      const funding_apr = (r() - 0.45) * 0.35;
      return { sourceId, ts, symbol, field: "funding_apr", value: funding_apr };
    }
    case "deribit": {
      const iv = 0.4 + r() * 0.7;
      return { sourceId, ts, symbol, field: "iv_30d", value: iv };
    }
    case "polygon_equities":
    case "alpaca": {
      const last = 50 + r() * 200;
      return { sourceId, ts, symbol, field: "last_price", value: last };
    }
    case "fred":
    case "treasury_direct": {
      const yield2y = 3.8 + (r() - 0.5) * 0.4;
      return { sourceId, ts, symbol, field: "yield_2y", value: yield2y };
    }
    case "sec_edgar":
      return { sourceId, ts, symbol, field: "filings_24h", value: Math.floor(r() * 7) };
    case "private_jobs":
      return { sourceId, ts, symbol, field: "job_post_velocity_pct", value: (r() - 0.4) * 0.6 };
    case "app_store":
      return { sourceId, ts, symbol, field: "rank_delta_7d", value: Math.floor((r() - 0.5) * 60) };
    case "github_signals":
      return { sourceId, ts, symbol, field: "star_velocity_7d", value: Math.floor(r() * 400) };
    case "newsapi":
      return { sourceId, ts, symbol, field: "headline_velocity", value: r() * 4 };
    case "twitter_x":
      return { sourceId, ts, symbol, field: "cashtag_velocity", value: r() * 6 };
    case "reddit":
      return { sourceId, ts, symbol, field: "mention_velocity", value: r() * 3 };
    case "google_trends":
      return { sourceId, ts, symbol, field: "search_index", value: r() * 100 };
    case "nws":
      return { sourceId, ts, symbol, field: "temp_anomaly_f", value: (r() - 0.5) * 14 };
    case "etherscan":
      return { sourceId, ts, symbol, field: "whale_netflow_eth", value: (r() - 0.5) * 8000 };
    default:
      return { sourceId, ts, symbol, field: "value", value: r() };
  }
}

function clamp01(x: number) { return Math.max(0.01, Math.min(0.99, x)); }
function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
