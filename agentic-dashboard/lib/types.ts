// Domain types for the Agentic Market Research & Paper Trading System.
// Everything in here is venue-agnostic. The concrete data connectors and
// strategy engines map external worlds (Kalshi/Polymarket/Alpaca/SEC/etc.)
// onto these shapes.

export type MarketType =
  | "prediction_markets"
  | "crypto"
  | "equities"
  | "macro"
  | "commodities"
  | "rates"
  | "private_signals"
  | "sports"
  | "news_arb"
  | "cross_asset_rv";

export type StrategyType =
  | "arbitrage"
  | "momentum"
  | "mean_reversion"
  | "event_driven"
  | "sentiment"
  | "statistical"
  | "market_making"
  | "relative_value";

export type AgentStatus =
  | "idle"
  | "researching"
  | "backtesting"
  | "paper_trading"
  | "paused";

export type ThesisStatus =
  | "watching"
  | "active"
  | "profitable"
  | "losing"
  | "invalidated";

export type LogLevel = "trace" | "info" | "signal" | "trade" | "warn" | "error";

export interface Agent {
  id: string;
  name: string;
  market: MarketType;
  strategy: StrategyType;
  dataSources: string[];                // ids of connectors this agent uses
  status: AgentStatus;
  confidenceThreshold: number;          // 0..1, gate to fire a paper trade
  riskBudgetUsd: number;                // max dollar exposure
  maxPositionUsd: number;
  thesisRefreshSec: number;             // cadence agent re-evaluates universe
  stopLossPct: number;
  takeProfitPct: number;
  createdAt: string;                    // ISO
  lastActivityAt: string;
  pinned?: boolean;
}

export interface Thesis {
  id: string;
  agentId: string;
  title: string;
  market: MarketType;
  symbol: string;                        // primary ticker / market id this points at
  summary: string;
  catalyst: string;
  evidence: string[];
  dataSources: string[];
  confidence: number;                    // 0..1
  expectedEdgeBps: number;               // basis points of expected edge
  horizonHours: number;
  risks: string[];
  status: ThesisStatus;
  createdAt: string;
  updatedAt: string;
  linkedTradeIds: string[];
}

export interface PaperTrade {
  id: string;
  agentId: string;
  thesisId?: string;
  symbol: string;
  market: MarketType;
  side: "long" | "short";
  qty: number;
  entryPrice: number;
  exitPrice?: number;
  currentPrice: number;
  openedAt: string;
  closedAt?: string;
  rationale: string;
  status: "open" | "closed";
  realizedPnl?: number;
  feesUsd: number;
  stopLossPct: number;
  takeProfitPct: number;
}

export interface AgentLog {
  id: string;
  agentId: string;
  ts: string;
  level: LogLevel;
  message: string;
  meta?: Record<string, unknown>;
}

export interface DiscoveryEvent {
  id: string;
  ts: string;
  agentId: string;
  agentName: string;
  market: MarketType;
  headline: string;
  detail: string;
  severity: "info" | "signal" | "alert";
  thesisId?: string;
}

export interface DataSource {
  id: string;
  name: string;
  category:
    | "prediction_market"
    | "crypto"
    | "equity"
    | "macro"
    | "filings"
    | "news"
    | "social"
    | "alt"
    | "weather"
    | "onchain";
  status: "connected" | "mock" | "disconnected";
  description: string;
  // free-text on where to wire the real API in code
  wireUpHint: string;
}

export interface PnlPoint {
  ts: string;
  totalEquity: number;
  realizedPnl: number;
  unrealizedPnl: number;
}

export interface RegimeSnapshot {
  ts: string;
  vix?: number;
  btcVol30d?: number;
  ratesRegime: "easing" | "tightening" | "neutral";
  riskOn: boolean;
  notes: string[];
}

export interface AppState {
  agents: Record<string, Agent>;
  theses: Record<string, Thesis>;
  trades: Record<string, PaperTrade>;
  logs: AgentLog[];                       // append-only, capped
  discoveries: DiscoveryEvent[];          // append-only, capped
  pnlHistory: PnlPoint[];                 // sampled, capped
  regime: RegimeSnapshot;
  dataSources: DataSource[];
  startingEquity: number;
  cashUsd: number;                        // simulated cash
  updatedAt: string;
}
