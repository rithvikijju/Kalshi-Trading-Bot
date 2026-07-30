/**
 * Singleton in-process store with JSON-file persistence.
 *
 * Why not Prisma/SQLite? For this dashboard the working set is tiny and
 * the user wants speed-to-first-render and the freedom to wipe data. A
 * single mutex-guarded JSON snapshot is good enough — and easy to swap
 * for a real DB by replacing this module with one that wraps Drizzle
 * or Prisma against the same `AppState` shape.
 *
 * The store survives Next.js dev's HMR by stashing the instance on
 * `globalThis`.
 */
import fs from "node:fs";
import path from "node:path";
import { nanoid } from "nanoid";
import type {
  AppState, Agent, Thesis, PaperTrade, AgentLog,
  DiscoveryEvent, PnlPoint, DataSource,
} from "./types";
import { DEFAULT_DATA_SOURCES } from "./dataConnectors";
import { SEED_AGENTS } from "./seed";

const DATA_DIR = path.join(process.cwd(), "data");
const STATE_FILE = path.join(DATA_DIR, "state.json");

const MAX_LOGS = 4000;
const MAX_DISCOVERIES = 1500;
const MAX_PNL = 4000;

interface Holder {
  state: AppState;
  saveTimer?: NodeJS.Timeout;
  dirty: boolean;
}

declare global {
  // eslint-disable-next-line no-var
  var __AGENTIC_STORE__: Holder | undefined;
}

function emptyState(): AppState {
  const now = new Date().toISOString();
  return {
    agents: {},
    theses: {},
    trades: {},
    logs: [],
    discoveries: [],
    pnlHistory: [{ ts: now, totalEquity: 1_000_000, realizedPnl: 0, unrealizedPnl: 0 }],
    regime: {
      ts: now,
      vix: 14.2,
      btcVol30d: 0.42,
      ratesRegime: "neutral",
      riskOn: true,
      notes: ["Equities chopping, breadth narrow", "BTC funding compressed", "Front-end yields range-bound"],
    },
    dataSources: DEFAULT_DATA_SOURCES,
    startingEquity: 1_000_000,
    cashUsd: 1_000_000,
    updatedAt: now,
  };
}

function ensureDir() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

function load(): AppState {
  ensureDir();
  if (!fs.existsSync(STATE_FILE)) {
    const s = emptyState();
    seedAgentsInto(s);
    return s;
  }
  try {
    const raw = fs.readFileSync(STATE_FILE, "utf8");
    const parsed = JSON.parse(raw) as AppState;
    // Refresh data sources every boot (so newly-added connectors show up).
    parsed.dataSources = DEFAULT_DATA_SOURCES;
    return parsed;
  } catch (e) {
    console.error("[store] load failed, starting fresh", e);
    const s = emptyState();
    seedAgentsInto(s);
    return s;
  }
}

function seedAgentsInto(s: AppState) {
  for (const def of SEED_AGENTS) {
    const id = nanoid(10);
    const now = new Date().toISOString();
    const agent: Agent = {
      id,
      name: def.name,
      market: def.market,
      strategy: def.strategy,
      dataSources: def.dataSources,
      status: def.status ?? "researching",
      confidenceThreshold: def.confidenceThreshold ?? 0.68,
      riskBudgetUsd: def.riskBudgetUsd ?? 75_000,
      maxPositionUsd: def.maxPositionUsd ?? 20_000,
      thesisRefreshSec: def.thesisRefreshSec ?? 45,
      stopLossPct: def.stopLossPct ?? 0.06,
      takeProfitPct: def.takeProfitPct ?? 0.12,
      createdAt: now,
      lastActivityAt: now,
      pinned: def.pinned,
    };
    s.agents[id] = agent;
  }
}

function persistNow(holder: Holder) {
  ensureDir();
  const tmp = STATE_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(holder.state));
  fs.renameSync(tmp, STATE_FILE);
  holder.dirty = false;
}

function getHolder(): Holder {
  if (globalThis.__AGENTIC_STORE__) return globalThis.__AGENTIC_STORE__;
  const h: Holder = { state: load(), dirty: false };
  globalThis.__AGENTIC_STORE__ = h;
  return h;
}

export interface MutateOpts {
  /** if true, do not bump updatedAt or trigger persist (for read-modify-read patterns) */
  silent?: boolean;
}

export function getState(): AppState {
  return getHolder().state;
}

export function mutate<T>(fn: (s: AppState) => T, opts?: MutateOpts): T {
  const h = getHolder();
  const out = fn(h.state);
  if (!opts?.silent) {
    h.state.updatedAt = new Date().toISOString();
    h.dirty = true;
    scheduleSave(h);
  }
  // Cap logs/discoveries/pnl in-place
  if (h.state.logs.length > MAX_LOGS)
    h.state.logs = h.state.logs.slice(-MAX_LOGS);
  if (h.state.discoveries.length > MAX_DISCOVERIES)
    h.state.discoveries = h.state.discoveries.slice(-MAX_DISCOVERIES);
  if (h.state.pnlHistory.length > MAX_PNL)
    h.state.pnlHistory = h.state.pnlHistory.slice(-MAX_PNL);
  return out;
}

function scheduleSave(h: Holder) {
  if (h.saveTimer) return;
  h.saveTimer = setTimeout(() => {
    h.saveTimer = undefined;
    try { persistNow(h); } catch (e) { console.error("[store] save err", e); }
  }, 800);
}

export function forceSave() {
  const h = getHolder();
  try { persistNow(h); } catch (e) { console.error("[store] forceSave err", e); }
}

export function makeId(prefix?: string) {
  return prefix ? `${prefix}_${nanoid(10)}` : nanoid(12);
}

// ── Helpers for derived metrics ──────────────────────────────────────────
export function calcTotalEquity(s: AppState): { equity: number; unrealized: number; realized: number; exposure: number } {
  let unrealized = 0;
  let realized = 0;
  let exposure = 0;
  for (const t of Object.values(s.trades)) {
    if (t.status === "open") {
      const delta = (t.currentPrice - t.entryPrice) * (t.side === "long" ? 1 : -1);
      unrealized += delta * t.qty - t.feesUsd;
      exposure += Math.abs(t.qty * t.currentPrice);
    } else {
      realized += t.realizedPnl ?? 0;
    }
  }
  return { equity: s.cashUsd + unrealized, unrealized, realized, exposure };
}

export function winRate(s: AppState): { wins: number; losses: number; rate: number } {
  let wins = 0, losses = 0;
  for (const t of Object.values(s.trades)) {
    if (t.status !== "closed") continue;
    const p = t.realizedPnl ?? 0;
    if (p > 0) wins++;
    else if (p < 0) losses++;
  }
  const total = wins + losses;
  return { wins, losses, rate: total === 0 ? 0 : wins / total };
}

export function sharpeEstimate(s: AppState): number | null {
  const pnls = Object.values(s.trades)
    .filter(t => t.status === "closed")
    .map(t => t.realizedPnl ?? 0);
  if (pnls.length < 4) return null;
  const mean = pnls.reduce((a, b) => a + b, 0) / pnls.length;
  const variance = pnls.reduce((a, b) => a + (b - mean) ** 2, 0) / pnls.length;
  const std = Math.sqrt(variance);
  if (std === 0) return null;
  // assume ~3 trades/day average → annualization factor ~sqrt(252*3)
  return (mean / std) * Math.sqrt(252 * 3);
}

export function recentLogs(agentId?: string, limit = 100): AgentLog[] {
  const s = getState();
  const filtered = agentId ? s.logs.filter(l => l.agentId === agentId) : s.logs;
  return filtered.slice(-limit).reverse();
}

export function recentDiscoveries(limit = 60): DiscoveryEvent[] {
  return getState().discoveries.slice(-limit).reverse();
}
