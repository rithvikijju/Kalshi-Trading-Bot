import type { Agent, MarketType, StrategyType, AgentStatus, RegimeSnapshot } from "./types";

type SeedAgent = Omit<Agent, "id" | "createdAt" | "lastActivityAt"> & {
  status?: AgentStatus;
};

/* The "interesting" defaults — one agent per major market vertical so
   the dashboard has something on first boot. */
export const SEED_AGENTS: SeedAgent[] = [
  {
    name: "Prediction Market Arbitrage Agent",
    market: "prediction_markets",
    strategy: "arbitrage",
    dataSources: ["kalshi", "polymarket", "binance_perps"],
    status: "paper_trading",
    confidenceThreshold: 0.7,
    riskBudgetUsd: 75_000,
    maxPositionUsd: 15_000,
    thesisRefreshSec: 30,
    stopLossPct: 0.08,
    takeProfitPct: 0.15,
    pinned: true,
  },
  {
    name: "Crypto Funding Rate Agent",
    market: "crypto",
    strategy: "relative_value",
    dataSources: ["binance_perps", "hyperliquid", "deribit"],
    status: "paper_trading",
    confidenceThreshold: 0.62,
    riskBudgetUsd: 120_000,
    maxPositionUsd: 40_000,
    thesisRefreshSec: 60,
    stopLossPct: 0.04,
    takeProfitPct: 0.07,
    pinned: true,
  },
  {
    name: "Macro Rates Agent",
    market: "macro",
    strategy: "event_driven",
    dataSources: ["fred", "treasury_direct", "newsapi"],
    status: "researching",
    confidenceThreshold: 0.66,
    riskBudgetUsd: 100_000,
    maxPositionUsd: 30_000,
    thesisRefreshSec: 90,
    stopLossPct: 0.05,
    takeProfitPct: 0.1,
  },
  {
    name: "AI Infrastructure Equity Agent",
    market: "equities",
    strategy: "momentum",
    dataSources: ["polygon_equities", "github_signals", "private_jobs", "sec_edgar"],
    status: "researching",
    confidenceThreshold: 0.72,
    riskBudgetUsd: 90_000,
    maxPositionUsd: 25_000,
    thesisRefreshSec: 120,
    stopLossPct: 0.07,
    takeProfitPct: 0.18,
  },
  {
    name: "Private Company Signal Agent",
    market: "private_signals",
    strategy: "sentiment",
    dataSources: ["private_jobs", "app_store", "github_signals", "newsapi"],
    status: "researching",
    confidenceThreshold: 0.74,
    riskBudgetUsd: 50_000,
    maxPositionUsd: 12_000,
    thesisRefreshSec: 240,
    stopLossPct: 0.1,
    takeProfitPct: 0.25,
  },
  {
    name: "News Catalyst Agent",
    market: "news_arb",
    strategy: "event_driven",
    dataSources: ["newsapi", "twitter_x", "reddit", "google_trends"],
    status: "paper_trading",
    confidenceThreshold: 0.65,
    riskBudgetUsd: 60_000,
    maxPositionUsd: 15_000,
    thesisRefreshSec: 45,
    stopLossPct: 0.06,
    takeProfitPct: 0.12,
  },
  {
    name: "Cross-Asset Relative Value Agent",
    market: "cross_asset_rv",
    strategy: "statistical",
    dataSources: ["polygon_equities", "fred", "binance_perps", "deribit"],
    status: "backtesting",
    confidenceThreshold: 0.7,
    riskBudgetUsd: 110_000,
    maxPositionUsd: 28_000,
    thesisRefreshSec: 75,
    stopLossPct: 0.05,
    takeProfitPct: 0.09,
  },
  {
    name: "Sports Market Inefficiency Agent",
    market: "sports",
    strategy: "arbitrage",
    dataSources: ["kalshi", "polymarket", "newsapi"],
    status: "idle",
    confidenceThreshold: 0.68,
    riskBudgetUsd: 40_000,
    maxPositionUsd: 8_000,
    thesisRefreshSec: 180,
    stopLossPct: 0.1,
    takeProfitPct: 0.2,
  },
];

export const SYMBOL_UNIVERSE: Record<MarketType, string[]> = {
  prediction_markets: ["KXBTC-21CLOSE", "KXBTC15M-23B", "PM-BTC-UPDOWN-5M-A", "PM-ETH-UPDOWN-5M-A", "PM-FED-RATE-JUL"],
  crypto: ["BTC-PERP", "ETH-PERP", "SOL-PERP", "ARB-PERP", "HYPE-PERP", "BTC-CME-BASIS"],
  equities: ["NVDA", "AMD", "AVGO", "ORCL", "VRT", "ANET", "ASML", "DELL"],
  macro: ["UST-2Y", "UST-10Y", "DXY", "GOLD", "BRENT", "USDJPY", "CPI-FRONT-MONTH"],
  commodities: ["NG-FRONT", "CL-FRONT", "GLD", "SLV", "URA"],
  rates: ["SOFR-DEC", "FF-NOV", "SR3-MAR-JUN"],
  private_signals: ["ANTHROPIC-SECONDARY", "ANYSCALE-PRIV", "GROQ-PRIV", "FIGURE-AI-PRIV"],
  sports: ["NBA-FIN-G3", "MLB-NYY-BOS-TOTAL", "NFL-PRESEASON-W2"],
  news_arb: ["URANIUM-CLUSTER", "TSMC-EARNINGS-RUN", "GLP1-AGGREGATE"],
  cross_asset_rv: ["NDX-VS-BTC", "GOLD-VS-2Y", "SOL-VS-NVDA"],
};

export const STRATEGY_BLURBS: Record<StrategyType, string[]> = {
  arbitrage: [
    "Same outcome, mispriced venues — buy low, sell high before HFTs catch up.",
    "Cross-venue probability spread exceeds bid/ask + fee cushion.",
  ],
  momentum: [
    "Multi-source flow divergence persistent over 4h window.",
    "Velocity acceleration across alt-data + price + volume confirms breakout.",
  ],
  mean_reversion: [
    "Z-score >2.5 on a stationary spread with 70d half-life.",
    "Vol-of-vol expansion in absence of catalyst — fade move.",
  ],
  event_driven: [
    "Catalyst window opens; market underpricing realized-vol expansion.",
    "Filing/news cluster suggests imminent guidance revision.",
  ],
  sentiment: [
    "Social velocity divergence — retail buying into negative organic signal.",
    "Cashtag volume up 4x with neutral price action — flow likely informed.",
  ],
  statistical: [
    "Cointegrating residual at 2.8 sigma vs 90d stationary band.",
    "Cross-asset PCA factor 3 dislocated, mean-revert play.",
  ],
  market_making: [
    "Spread/queue position favorable; adverse selection low in this regime.",
  ],
  relative_value: [
    "Funding/basis term structure inverted vs realized — RV carry.",
    "Cheap leg in spread carries positive expected funding.",
  ],
};

export const HEADLINE_TEMPLATES: Partial<Record<MarketType, string[]>> = {
  macro: [
    "Macro agent detected unusual move in 2Y yields",
    "Macro agent: front-end OIS curve dislocated vs CPI nowcast",
    "Macro agent: 5y5y inflation breakeven snap below 50d range",
  ],
  prediction_markets: [
    "Prediction agent: Kalshi/Polymarket probability spread > 6¢",
    "Prediction agent: stale quote on KXBTC15M vs spot drift",
    "Prediction agent: BTC 5-min YES depth thinned to <$1k",
  ],
  crypto: [
    "Crypto agent: BTC funding/basis divergence — basis-conv RV open",
    "Crypto agent: ETH perp funding flipped negative on Hyperliquid",
    "Crypto agent: SOL OI down 18%, options skew flat — vol-rich tape",
  ],
  equities: [
    "Equity agent: unusual hiring growth for AI infrastructure peer",
    "Equity agent: insider 4-form cluster on networking subsector",
    "Equity agent: GitHub commit acceleration on key supplier repos",
  ],
  news_arb: [
    "News agent detected catalyst cluster around uranium supply",
    "News agent: GLP-1 follow-on indications cluster — basket dislocates",
    "News agent: TSMC capacity headline — basket beta spike unjustified",
  ],
  private_signals: [
    "Private signal agent: job postings up 220% for inference startup",
    "Private signal agent: app download rank flipped #1 → #4 quietly",
  ],
  cross_asset_rv: [
    "RV agent: NDX vs BTC spread back-end widened, mean-revert tilt",
    "RV agent: gold vs 2y real-yield residual 2.4σ rich",
  ],
  sports: [
    "Sports agent: line drift unjustified by injury news velocity",
  ],
};

export const DEFAULT_REGIME: RegimeSnapshot = {
  ts: new Date().toISOString(),
  vix: 14.2,
  btcVol30d: 0.41,
  ratesRegime: "neutral",
  riskOn: true,
  notes: ["Equities chopping, breadth narrow",
          "BTC funding compressed vs historical median",
          "Front-end yields range-bound; CPI Fri"],
};
