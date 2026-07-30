/**
 * Risk manager.
 *
 * The contract here is *advisory*: an agent submits a candidate trade,
 * the risk manager either approves a sized version, scales it, or
 * rejects with a reason. Everything is in dollars; the broker handles
 * fees and conversion to qty/contracts.
 *
 * Real-money production would extend this with VaR/CVaR, exposure caps
 * per sector, per-venue limits, and live margin calls.
 */
import type { Agent, AppState, PaperTrade } from "./types";

export interface RiskRequest {
  agent: Agent;
  symbol: string;
  side: "long" | "short";
  refPrice: number;
  confidence: number;
  desiredUsd: number;
}

export interface RiskDecision {
  ok: boolean;
  approvedUsd: number;
  qty: number;
  reasons: string[];
}

export function evaluate(req: RiskRequest, state: AppState): RiskDecision {
  const reasons: string[] = [];
  const { agent, refPrice, confidence, desiredUsd } = req;

  if (agent.status === "paused") {
    return { ok: false, approvedUsd: 0, qty: 0, reasons: ["agent paused"] };
  }
  if (confidence < agent.confidenceThreshold) {
    return { ok: false, approvedUsd: 0, qty: 0, reasons: [`confidence ${confidence.toFixed(2)} < threshold ${agent.confidenceThreshold}`] };
  }
  if (refPrice <= 0) {
    return { ok: false, approvedUsd: 0, qty: 0, reasons: ["non-positive ref price"] };
  }

  // 1. Per-agent risk budget cap (open exposure across this agent's trades).
  const openByAgent = Object.values(state.trades)
    .filter(t => t.agentId === agent.id && t.status === "open")
    .reduce((acc, t) => acc + Math.abs(t.qty * t.currentPrice), 0);
  const budgetHeadroom = Math.max(0, agent.riskBudgetUsd - openByAgent);
  if (budgetHeadroom <= 0) {
    return { ok: false, approvedUsd: 0, qty: 0, reasons: ["agent risk budget exhausted"] };
  }

  // 2. Per-trade max position size.
  let approved = Math.min(desiredUsd, agent.maxPositionUsd, budgetHeadroom);

  // 3. Confidence scaling: trade closer to max as confidence increases.
  const confScale = (confidence - agent.confidenceThreshold) / Math.max(1e-6, 1 - agent.confidenceThreshold);
  approved *= Math.max(0.25, Math.min(1, 0.5 + 0.5 * confScale));

  if (approved < 100) {
    return { ok: false, approvedUsd: 0, qty: 0, reasons: ["sized too small after scaling"] };
  }

  // 4. Portfolio-level exposure cap (simple — 70% of starting equity).
  const portfolioExposure = Object.values(state.trades)
    .filter(t => t.status === "open")
    .reduce((acc, t) => acc + Math.abs(t.qty * t.currentPrice), 0);
  const portfolioCap = state.startingEquity * 0.7;
  if (portfolioExposure + approved > portfolioCap) {
    approved = Math.max(0, portfolioCap - portfolioExposure);
    if (approved < 100) {
      return { ok: false, approvedUsd: 0, qty: 0, reasons: ["portfolio exposure cap hit"] };
    }
    reasons.push("scaled to portfolio cap");
  }

  const qty = approved / refPrice;
  return { ok: true, approvedUsd: approved, qty, reasons };
}

/** Should an open trade be exited right now? */
export function shouldExit(t: PaperTrade): { exit: boolean; reason: string } {
  const move = (t.currentPrice - t.entryPrice) / t.entryPrice;
  const directional = t.side === "long" ? move : -move;
  if (directional <= -t.stopLossPct) return { exit: true, reason: "stop loss" };
  if (directional >= t.takeProfitPct) return { exit: true, reason: "take profit" };
  return { exit: false, reason: "" };
}
