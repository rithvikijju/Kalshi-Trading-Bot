/**
 * Strategy engines.
 *
 * Each engine receives an agent + the world (data connectors) and returns
 * an optional candidate trade idea + a thesis blurb. New strategies plug
 * in by adding an entry to ENGINES — the worker dispatches based on
 * `agent.strategy`.
 *
 * The engines below are intentionally simple: they read a few samples
 * from the agent's chosen data sources, fuse them into a confidence
 * score, and emit a candidate when the score crosses the agent's
 * threshold. They are deliberately structured so swapping in a real
 * strategy is just replacing the `decide()` body.
 */
import { sampleConnector, ConnectorSample } from "./dataConnectors";
import { SYMBOL_UNIVERSE, STRATEGY_BLURBS, HEADLINE_TEMPLATES } from "./seed";
import type { Agent, MarketType, StrategyType, Thesis, DiscoveryEvent } from "./types";
import { makeId } from "./store";

export interface Candidate {
  symbol: string;
  side: "long" | "short";
  refPrice: number;
  confidence: number;          // 0..1
  expectedEdgeBps: number;
  thesis: Omit<Thesis, "id" | "agentId" | "createdAt" | "updatedAt" | "status" | "linkedTradeIds">;
  discoveryHeadline: string;
  discoveryDetail: string;
  rationale: string;
}

export interface EngineCtx {
  agent: Agent;
  pickSymbol: () => string;
  sample: (sourceId: string, symbol: string) => ConnectorSample;
  rand: () => number;
}

function makeCtx(agent: Agent): EngineCtx {
  const universe = SYMBOL_UNIVERSE[agent.market] ?? ["UNK"];
  return {
    agent,
    pickSymbol: () => universe[Math.floor(Math.random() * universe.length)],
    sample: sampleConnector,
    rand: Math.random,
  };
}

function basePrice(market: MarketType): number {
  switch (market) {
    case "prediction_markets":
    case "sports":
      return 0.45 + Math.random() * 0.2;
    case "crypto":
      return 60_000 + (Math.random() - 0.5) * 6000;
    case "equities":
      return 80 + Math.random() * 300;
    case "macro":
    case "rates":
      return 3.8 + (Math.random() - 0.5) * 0.4;
    case "commodities":
      return 80 + Math.random() * 50;
    case "private_signals":
      return 25 + Math.random() * 25;
    default:
      return 100 + (Math.random() - 0.5) * 30;
  }
}

function buildThesis(
  ctx: EngineCtx,
  candidateBits: {
    symbol: string;
    title: string;
    summary: string;
    catalyst: string;
    evidence: string[];
    confidence: number;
    expectedEdgeBps: number;
    horizonHours: number;
    risks: string[];
  }
): Candidate["thesis"] {
  return {
    title: candidateBits.title,
    market: ctx.agent.market,
    symbol: candidateBits.symbol,
    summary: candidateBits.summary,
    catalyst: candidateBits.catalyst,
    evidence: candidateBits.evidence,
    dataSources: ctx.agent.dataSources,
    confidence: candidateBits.confidence,
    expectedEdgeBps: candidateBits.expectedEdgeBps,
    horizonHours: candidateBits.horizonHours,
    risks: candidateBits.risks,
  };
}

function pickHeadline(market: MarketType, fallback: string) {
  const arr = HEADLINE_TEMPLATES[market];
  if (!arr || arr.length === 0) return fallback;
  return arr[Math.floor(Math.random() * arr.length)];
}

function pickBlurb(strategy: StrategyType) {
  const arr = STRATEGY_BLURBS[strategy];
  return arr[Math.floor(Math.random() * arr.length)];
}

/* ────────────────────────────────────────────────────────────────────────
   ENGINES
   ──────────────────────────────────────────────────────────────────── */

function arbitrageEngine(ctx: EngineCtx): Candidate | null {
  // Multi-source price spread
  const symbol = ctx.pickSymbol();
  const samples = ctx.agent.dataSources.slice(0, 3).map(s => ctx.sample(s, symbol));
  if (samples.length < 2) return null;
  const values = samples.map(s => s.value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const spread = hi - lo;
  if (spread < 0.02 && ctx.agent.market === "prediction_markets") return null;
  const confidence = Math.min(0.96, 0.55 + spread * (ctx.agent.market === "prediction_markets" ? 4 : 0.6));
  const side: "long" | "short" = samples[0].value < samples[1].value ? "long" : "short";
  const refPrice = basePrice(ctx.agent.market);
  const edge = Math.max(40, Math.floor(spread * 2200 + 20));

  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `Spread ${(spread).toFixed(3)} across ${ctx.agent.dataSources.slice(0,3).join("/")}`,
    discoveryHeadline: pickHeadline(ctx.agent.market, "Arbitrage agent: cross-venue spread detected"),
    discoveryDetail: `${symbol} mispriced by ~${edge}bps · sources: ${ctx.agent.dataSources.slice(0,3).join(", ")}`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `Cross-venue ${symbol} probability/price spread`,
      summary: `${ctx.agent.dataSources.slice(0, 3).join(" vs ")} disagree on ${symbol}. Fade the leg with the unstable book.`,
      catalyst: "Liquidity divergence + slow follower venue",
      evidence: samples.map(s => `${s.sourceId}.${s.field}=${s.value.toFixed(4)}`),
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 0.25 + Math.random() * 1.5,
      risks: ["sub-second mean revert", "fee floor exceeds edge"],
    }),
  };
}

function fundingRateEngine(ctx: EngineCtx): Candidate | null {
  const symbol = ctx.pickSymbol();
  const venues = ctx.agent.dataSources.filter(s => ["binance_perps", "hyperliquid", "deribit"].includes(s));
  if (venues.length === 0) return null;
  const samples = venues.map(v => ctx.sample(v, symbol));
  const median = samples.map(s => s.value).sort((a, b) => a - b)[Math.floor(samples.length / 2)];
  const dispersion = Math.max(...samples.map(s => s.value)) - Math.min(...samples.map(s => s.value));
  if (Math.abs(median) < 0.02 && dispersion < 0.03) return null;
  const confidence = Math.min(0.95, 0.55 + Math.abs(median) * 2 + dispersion * 2.5);
  const side: "long" | "short" = median > 0 ? "short" : "long"; // short funding-positive perp, long spot/hedge
  const edge = Math.max(50, Math.floor(Math.abs(median) * 4000 + dispersion * 6000));
  const refPrice = basePrice(ctx.agent.market);

  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `Median funding ${(median * 100).toFixed(2)}% APR, venue dispersion ${(dispersion * 100).toFixed(2)}%`,
    discoveryHeadline: pickHeadline("crypto", "Crypto agent: funding divergence"),
    discoveryDetail: `${symbol} basis/funding term structure dislocated. ${samples.map(s => `${s.sourceId}=${(s.value * 100).toFixed(2)}%`).join("  ")}`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `${symbol} basis/funding carry vs realized`,
      summary: `Funding term structure on ${symbol} carries positively while realized basis stays inside threshold. Pair: spot long / perp short (or inverse).`,
      catalyst: "Persistent funding payments + basis convergence",
      evidence: samples.map(s => `${s.sourceId}.funding=${(s.value*100).toFixed(2)}%`).concat([`dispersion=${(dispersion*100).toFixed(2)}%`]),
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 6 + Math.random() * 18,
      risks: ["funding flip mid-cycle", "spot/perp basis blowout"],
    }),
  };
}

function momentumEngine(ctx: EngineCtx): Candidate | null {
  const symbol = ctx.pickSymbol();
  const ssrc = ctx.agent.dataSources;
  const samples = ssrc.map(s => ctx.sample(s, symbol));
  const score = samples.reduce((acc, s) => acc + (s.value > 0.5 ? 1 : -1), 0) / samples.length;
  if (Math.abs(score) < 0.4) return null;
  const confidence = Math.min(0.94, 0.5 + Math.abs(score) * 0.5 + (Math.random() * 0.1));
  const side: "long" | "short" = score > 0 ? "long" : "short";
  const refPrice = basePrice(ctx.agent.market);
  const edge = Math.max(40, Math.floor(Math.abs(score) * 220 + 80));
  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `${samples.length} alt-data sources agree (score=${score.toFixed(2)}) on ${side.toUpperCase()} ${symbol}`,
    discoveryHeadline: pickHeadline(ctx.agent.market, "Momentum agent: alt-data convergence"),
    discoveryDetail: `Convergent flow: ${samples.map(s => `${s.sourceId}:${s.value.toFixed(2)}`).join("  ")}`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `Multi-source momentum: ${symbol}`,
      summary: pickBlurb("momentum"),
      catalyst: "Alt-data velocity acceleration",
      evidence: samples.map(s => `${s.sourceId}.${s.field}=${s.value.toFixed(3)}`),
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 24 + Math.random() * 96,
      risks: ["narrative shift", "macro de-risk overrides factor"],
    }),
  };
}

function eventDrivenEngine(ctx: EngineCtx): Candidate | null {
  const symbol = ctx.pickSymbol();
  const filingsLike = ctx.agent.dataSources.find(s => ["sec_edgar", "newsapi", "fred", "treasury_direct"].includes(s));
  if (!filingsLike) return null;
  const s = ctx.sample(filingsLike, symbol);
  const intensity = Math.abs(s.value) > 1 ? Math.abs(s.value) : Math.abs(s.value) * 6;
  if (intensity < 0.6) return null;
  const confidence = Math.min(0.93, 0.55 + intensity * 0.13);
  const side: "long" | "short" = ctx.rand() > 0.5 ? "long" : "short";
  const refPrice = basePrice(ctx.agent.market);
  const edge = Math.max(80, Math.floor(intensity * 90 + 50));
  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `Catalyst cluster (${filingsLike}.${s.field}=${s.value.toFixed(2)})`,
    discoveryHeadline: pickHeadline(ctx.agent.market, "Event agent: catalyst cluster forming"),
    discoveryDetail: `${symbol} – ${filingsLike} fired ${s.value.toFixed(2)} on ${s.field}.`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `Event-driven setup on ${symbol}`,
      summary: pickBlurb("event_driven"),
      catalyst: `${filingsLike} → ${s.field} = ${s.value.toFixed(3)}`,
      evidence: [`${filingsLike}.${s.field}=${s.value.toFixed(3)}`, "supporting tape pattern: vol pickup pre-event"],
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 12 + Math.random() * 60,
      risks: ["event already priced in", "macro spillover"],
    }),
  };
}

function meanReversionEngine(ctx: EngineCtx): Candidate | null {
  const symbol = ctx.pickSymbol();
  const samples = ctx.agent.dataSources.map(s => ctx.sample(s, symbol));
  const mean = samples.reduce((a, b) => a + b.value, 0) / samples.length;
  const last = samples[samples.length - 1].value;
  const z = (last - mean) / (Math.abs(mean) + 0.2);
  if (Math.abs(z) < 0.8) return null;
  const confidence = Math.min(0.92, 0.5 + Math.abs(z) * 0.25);
  const side: "long" | "short" = z > 0 ? "short" : "long";
  const refPrice = basePrice(ctx.agent.market);
  const edge = Math.max(35, Math.floor(Math.abs(z) * 80 + 40));
  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `Z-score ${z.toFixed(2)} on ${symbol}`,
    discoveryHeadline: pickHeadline(ctx.agent.market, "Mean reversion: stretched move detected"),
    discoveryDetail: `${symbol} ${Math.abs(z).toFixed(1)}σ from rolling mean across ${samples.length} sources.`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `Mean reversion: ${symbol}`,
      summary: pickBlurb("mean_reversion"),
      catalyst: "Mean-revert from stretched residual",
      evidence: [`z=${z.toFixed(2)}`, ...samples.slice(0,2).map(s => `${s.sourceId}.${s.field}=${s.value.toFixed(3)}`)],
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 4 + Math.random() * 30,
      risks: ["trend continuation", "regime change"],
    }),
  };
}

function sentimentEngine(ctx: EngineCtx): Candidate | null {
  const symbol = ctx.pickSymbol();
  const socialSrc = ctx.agent.dataSources.filter(s => ["twitter_x", "reddit", "google_trends", "newsapi"].includes(s));
  if (socialSrc.length === 0) return null;
  const samples = socialSrc.map(s => ctx.sample(s, symbol));
  const meanV = samples.reduce((a, b) => a + b.value, 0) / samples.length;
  const dispersion = Math.max(...samples.map(s => s.value)) - Math.min(...samples.map(s => s.value));
  // divergence: high mean velocity + high dispersion → informed flow ahead of retail
  const score = meanV * (1 + dispersion);
  if (score < 1.5) return null;
  const confidence = Math.min(0.95, 0.55 + score / 10);
  const side: "long" | "short" = ctx.rand() > 0.4 ? "long" : "short";
  const refPrice = basePrice(ctx.agent.market);
  const edge = Math.max(60, Math.floor(score * 80 + 60));
  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `Sentiment composite ${score.toFixed(2)} across ${socialSrc.length} social sources`,
    discoveryHeadline: pickHeadline(ctx.agent.market, "Sentiment agent: composite velocity surge"),
    discoveryDetail: `${symbol} aggregate sentiment composite ${score.toFixed(2)} (mean=${meanV.toFixed(2)}, disp=${dispersion.toFixed(2)})`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `Social-velocity composite on ${symbol}`,
      summary: pickBlurb("sentiment"),
      catalyst: "Velocity divergence across social sources",
      evidence: samples.map(s => `${s.sourceId}.${s.field}=${s.value.toFixed(2)}`),
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 6 + Math.random() * 36,
      risks: ["retail-driven mean reversion", "narrative reversal"],
    }),
  };
}

function statisticalEngine(ctx: EngineCtx): Candidate | null {
  const symbol = ctx.pickSymbol();
  const samples = ctx.agent.dataSources.map(s => ctx.sample(s, symbol));
  if (samples.length < 2) return null;
  const v = samples.map(s => s.value);
  // toy "residual" - difference between first and average of rest, normalized
  const tail = v.slice(1).reduce((a, b) => a + b, 0) / Math.max(1, v.length - 1);
  const residual = v[0] - tail;
  const sigma = Math.sqrt(v.reduce((a, b) => a + (b - tail) ** 2, 0) / v.length) + 0.05;
  const z = residual / sigma;
  if (Math.abs(z) < 1.6) return null;
  const confidence = Math.min(0.93, 0.55 + Math.abs(z) * 0.12);
  const side: "long" | "short" = z > 0 ? "short" : "long";
  const refPrice = basePrice(ctx.agent.market);
  const edge = Math.max(40, Math.floor(Math.abs(z) * 70 + 60));
  return {
    symbol,
    side,
    refPrice,
    confidence,
    expectedEdgeBps: edge,
    rationale: `Cross-sectional residual z=${z.toFixed(2)}`,
    discoveryHeadline: pickHeadline(ctx.agent.market, "Stat-arb: residual stretched"),
    discoveryDetail: `${symbol} residual ${residual.toFixed(3)} (z=${z.toFixed(2)}) across ${samples.length} factors.`,
    thesis: buildThesis(ctx, {
      symbol,
      title: `Cross-sectional residual on ${symbol}`,
      summary: pickBlurb("statistical"),
      catalyst: "PCA residual mean revert",
      evidence: samples.map(s => `${s.sourceId}.${s.field}=${s.value.toFixed(3)}`),
      confidence,
      expectedEdgeBps: edge,
      horizonHours: 12 + Math.random() * 36,
      risks: ["cointegration break", "factor regime shift"],
    }),
  };
}

function marketMakingEngine(ctx: EngineCtx): Candidate | null {
  // Very rough: rarely emits, low edge, high cadence.
  if (Math.random() > 0.25) return null;
  const symbol = ctx.pickSymbol();
  const refPrice = basePrice(ctx.agent.market);
  const confidence = 0.6 + Math.random() * 0.2;
  return {
    symbol,
    side: Math.random() > 0.5 ? "long" : "short",
    refPrice,
    confidence,
    expectedEdgeBps: 8 + Math.floor(Math.random() * 14),
    rationale: "Queue position favorable, adverse selection low",
    discoveryHeadline: "MM agent: passive fill opportunity",
    discoveryDetail: `${symbol} – queue depth + recent toxicity within bounds`,
    thesis: buildThesis(ctx, {
      symbol, title: `Passive ${symbol} fill`, summary: pickBlurb("market_making"),
      catalyst: "low toxicity tape", evidence: ["spread/queue scan"], confidence,
      expectedEdgeBps: 12, horizonHours: 0.5, risks: ["fast-market", "adverse selection burst"],
    }),
  };
}

function relativeValueEngine(ctx: EngineCtx): Candidate | null {
  return fundingRateEngine(ctx) ?? statisticalEngine(ctx);
}

const ENGINES: Record<StrategyType, (ctx: EngineCtx) => Candidate | null> = {
  arbitrage: arbitrageEngine,
  momentum: momentumEngine,
  mean_reversion: meanReversionEngine,
  event_driven: eventDrivenEngine,
  sentiment: sentimentEngine,
  statistical: statisticalEngine,
  market_making: marketMakingEngine,
  relative_value: relativeValueEngine,
};

export function runEngineFor(agent: Agent): Candidate | null {
  // Funding-rate agents in the seed are tagged relative_value/crypto;
  // the strategy field decides which engine runs.
  const ctx = makeCtx(agent);
  return ENGINES[agent.strategy](ctx);
}

export function makeDiscovery(agent: Agent, c: Candidate): DiscoveryEvent {
  return {
    id: makeId("d"),
    ts: new Date().toISOString(),
    agentId: agent.id,
    agentName: agent.name,
    market: agent.market,
    headline: c.discoveryHeadline,
    detail: c.discoveryDetail,
    severity: c.confidence > 0.78 ? "alert" : c.confidence > 0.66 ? "signal" : "info",
  };
}
