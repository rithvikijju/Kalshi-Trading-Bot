/**
 * Background tick worker.
 *
 * One in-process loop runs every TICK_MS:
 *   1. For each non-paused agent, sample its world, decide, maybe emit a thesis.
 *   2. If risk approves the candidate, fire a paper trade.
 *   3. Mark every open trade to a drifted price, handle stops/targets.
 *   4. Append a regime + pnl snapshot.
 *   5. Roll old logs/discoveries/pnl out of the window.
 *
 * The worker is idempotent across hot-reload because we stash a singleton on
 * globalThis. Calling start() multiple times is safe.
 */
import { getState, mutate, makeId, calcTotalEquity } from "./store";
import type { PaperTrade, AgentLog, Thesis } from "./types";
import { runEngineFor, makeDiscovery } from "./strategyEngines";
import { evaluate, shouldExit } from "./riskManager";
import { placePaperTrade, markAndManage } from "./paperBroker";

const TICK_MS = 1500;

declare global {
  // eslint-disable-next-line no-var
  var __AGENTIC_WORKER__: { handle: NodeJS.Timeout; ticks: number } | undefined;
}

export function startWorker() {
  if (globalThis.__AGENTIC_WORKER__) return;
  console.log("[worker] starting tick loop");
  const handle = setInterval(tick, TICK_MS);
  globalThis.__AGENTIC_WORKER__ = { handle, ticks: 0 };
}

/* For each tick: choose a few agents to advance (so the activity feed
   doesn't burn the CPU with all of them at once). */
function tick() {
  const w = globalThis.__AGENTIC_WORKER__;
  if (!w) return;
  w.ticks++;
  try {
    const s = getState();
    const agents = Object.values(s.agents).filter(a => a.status !== "paused");
    if (agents.length === 0) {
      addRegimeAndPnl();
      return;
    }

    // mark book first so PnL reflects current "tape"
    markAndManage(driftedPrice);

    // pick ~2 agents per tick for engine runs
    const pickCount = Math.min(2 + Math.floor(Math.random() * 2), agents.length);
    const pool = [...agents].sort(() => Math.random() - 0.5).slice(0, pickCount);
    for (const a of pool) {
      try { runAgentOnce(a.id); } catch (e) { console.error("[worker] agent err", a.id, e); }
    }
    addRegimeAndPnl();
  } catch (e) {
    console.error("[worker] tick err", e);
  }
}

/* Move every open trade by a small random walk in its market's vol band.
   Real implementation: pull a live mark from a price connector. */
function driftedPrice(t: PaperTrade): number {
  const vol = MARKET_VOL[t.market] ?? 0.005;
  const eps = (Math.random() - 0.5) * 2 * vol;
  // small downward drift for stale theses (so the dashboard sees realized PnL both ways).
  const ageHours = (Date.now() - new Date(t.openedAt).getTime()) / 3.6e6;
  const aging = ageHours > 6 ? -vol * 0.05 : 0;
  return t.currentPrice * (1 + eps + aging);
}

const MARKET_VOL: Record<string, number> = {
  prediction_markets: 0.012,
  sports: 0.018,
  crypto: 0.006,
  equities: 0.0028,
  macro: 0.0015,
  commodities: 0.004,
  rates: 0.0012,
  private_signals: 0.008,
  news_arb: 0.005,
  cross_asset_rv: 0.0035,
};

/* Run an agent once: think → maybe-trade → log → update lastActivity. */
export function runAgentOnce(agentId: string) {
  const before = getState();
  const agent = before.agents[agentId];
  if (!agent || agent.status === "paused") return;

  // 1) think
  const log = (level: AgentLog["level"], message: string, meta?: Record<string, unknown>) => {
    mutate(s => {
      s.logs.push({
        id: makeId("l"),
        agentId,
        ts: new Date().toISOString(),
        level, message, meta,
      });
      s.agents[agentId].lastActivityAt = new Date().toISOString();
      s.agents[agentId].status = level === "trade" ? "paper_trading" : (s.agents[agentId].status === "paused" ? "paused" : "researching");
    });
  };

  log("trace", `scanning ${agent.dataSources.length} sources: ${agent.dataSources.join(", ")}`);

  const candidate = runEngineFor(agent);
  if (!candidate) {
    log("info", "no qualifying setup this cycle");
    return;
  }

  log("signal",
    `candidate ${candidate.side.toUpperCase()} ${candidate.symbol} ` +
    `conf=${(candidate.confidence * 100).toFixed(0)}% edge=${candidate.expectedEdgeBps}bps`,
    { rationale: candidate.rationale });

  // emit a discovery event
  const disc = makeDiscovery(agent, candidate);
  mutate(s => { s.discoveries.push(disc); });

  // 2) thesis
  const thesisId = makeId("th");
  const now = new Date().toISOString();
  const thesis: Thesis = {
    id: thesisId,
    agentId,
    ...candidate.thesis,
    status: "watching",
    createdAt: now,
    updatedAt: now,
    linkedTradeIds: [],
  };
  mutate(s => { s.theses[thesisId] = thesis; (s.discoveries[s.discoveries.length-1]).thesisId = thesisId; });

  // 3) risk + (maybe) trade
  const desired = agent.maxPositionUsd * Math.min(1, 0.5 + candidate.confidence * 0.6);
  const decision = evaluate(
    { agent, symbol: candidate.symbol, side: candidate.side,
      refPrice: candidate.refPrice, confidence: candidate.confidence, desiredUsd: desired },
    getState());

  if (!decision.ok) {
    log("info", `risk declined: ${decision.reasons.join("; ")}`);
    return;
  }

  const trade = placePaperTrade({
    agent, symbol: candidate.symbol, market: agent.market,
    side: candidate.side, qty: decision.qty, refPrice: candidate.refPrice,
    rationale: candidate.rationale, thesisId,
    stopLossPct: agent.stopLossPct, takeProfitPct: agent.takeProfitPct,
  });

  log("trade",
    `paper ${trade.side.toUpperCase()} ${trade.qty.toFixed(2)} ${trade.symbol} ` +
    `@ ${trade.entryPrice.toFixed(4)} (notional ${(trade.qty * trade.entryPrice).toFixed(0)})`,
    { tradeId: trade.id, thesisId });
}

export function deployAgent(input: {
  name: string; market: any; strategy: any; dataSources: string[];
  riskBudgetUsd: number; maxPositionUsd: number; thesisRefreshSec: number;
  confidenceThreshold: number; stopLossPct: number; takeProfitPct: number;
}) {
  const id = makeId("a");
  const now = new Date().toISOString();
  mutate(s => {
    s.agents[id] = {
      id, name: input.name, market: input.market, strategy: input.strategy,
      dataSources: input.dataSources, status: "researching",
      confidenceThreshold: input.confidenceThreshold,
      riskBudgetUsd: input.riskBudgetUsd, maxPositionUsd: input.maxPositionUsd,
      thesisRefreshSec: input.thesisRefreshSec,
      stopLossPct: input.stopLossPct, takeProfitPct: input.takeProfitPct,
      createdAt: now, lastActivityAt: now,
    };
    s.logs.push({
      id: makeId("l"), agentId: id, ts: now, level: "info",
      message: `agent deployed: ${input.name} on ${input.market}/${input.strategy}`,
    });
  });
  return id;
}

export function setAgentStatus(agentId: string, status: "researching" | "paused" | "idle" | "paper_trading" | "backtesting") {
  mutate(s => {
    const a = s.agents[agentId]; if (!a) return;
    a.status = status; a.lastActivityAt = new Date().toISOString();
    s.logs.push({
      id: makeId("l"), agentId, ts: a.lastActivityAt, level: "info",
      message: status === "paused" ? "agent paused" : `agent → ${status}`,
    });
  });
}

export function cloneAgent(agentId: string) {
  const src = getState().agents[agentId];
  if (!src) return null;
  return deployAgent({
    name: src.name + " (clone)",
    market: src.market, strategy: src.strategy,
    dataSources: [...src.dataSources],
    riskBudgetUsd: src.riskBudgetUsd, maxPositionUsd: src.maxPositionUsd,
    thesisRefreshSec: src.thesisRefreshSec, confidenceThreshold: src.confidenceThreshold,
    stopLossPct: src.stopLossPct, takeProfitPct: src.takeProfitPct,
  });
}

/* Update regime snapshot + log PnL once every ~6 ticks (~9s). */
let regimeCounter = 0;
function addRegimeAndPnl() {
  regimeCounter++;
  if (regimeCounter % 4 !== 0) return;
  mutate(s => {
    const { equity, unrealized, realized } = calcTotalEquity(s);
    s.pnlHistory.push({
      ts: new Date().toISOString(),
      totalEquity: equity,
      unrealizedPnl: unrealized,
      realizedPnl: realized,
    });

    // jiggle the regime so the home page feels alive
    if (s.regime.vix !== undefined) s.regime.vix = clampRange(s.regime.vix + (Math.random()-0.5)*0.3, 10, 32);
    if (s.regime.btcVol30d !== undefined) s.regime.btcVol30d = clampRange(s.regime.btcVol30d + (Math.random()-0.5)*0.02, 0.2, 0.95);
    s.regime.ts = new Date().toISOString();
    // small chance to switch regime
    if (Math.random() < 0.012) {
      const opts = ["easing", "tightening", "neutral"] as const;
      s.regime.ratesRegime = opts[Math.floor(Math.random()*3)];
    }
    s.regime.riskOn = (s.regime.vix ?? 16) < 19;
  });
}
function clampRange(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}
