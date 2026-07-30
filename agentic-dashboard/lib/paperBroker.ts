/**
 * Paper broker. Simulates trade execution end-to-end.
 *
 * Real brokers (Alpaca, IBKR, Kalshi) plug in by implementing the same
 * `Broker` interface: `place`, `mark`, `close`. The agent layer above
 * never knows whether trades hit a real venue or this simulator.
 *
 * Fees / slippage are deterministic so PnL is reproducible.
 */
import type { Agent, AppState, MarketType, PaperTrade } from "./types";
import { mutate, makeId } from "./store";

export interface PlaceArgs {
  agent: Agent;
  symbol: string;
  market: MarketType;
  side: "long" | "short";
  qty: number;
  refPrice: number;
  rationale: string;
  thesisId?: string;
  stopLossPct: number;
  takeProfitPct: number;
}

/* Fees vary by market (mimics real-world structure). */
const FEE_BPS: Record<MarketType, number> = {
  prediction_markets: 25,
  crypto: 6,
  equities: 1,
  macro: 2,
  commodities: 3,
  rates: 1,
  private_signals: 0,
  sports: 30,
  news_arb: 5,
  cross_asset_rv: 5,
};
const SLIP_BPS: Record<MarketType, number> = {
  prediction_markets: 30, crypto: 8, equities: 3, macro: 2, commodities: 4,
  rates: 2, private_signals: 0, sports: 35, news_arb: 6, cross_asset_rv: 5,
};

export function placePaperTrade(args: PlaceArgs): PaperTrade {
  const { agent, market, side, qty, refPrice, rationale, thesisId, stopLossPct, takeProfitPct } = args;
  const slip = (SLIP_BPS[market] / 1e4) * refPrice * (side === "long" ? 1 : -1);
  const entryPrice = refPrice + slip;
  const notional = qty * entryPrice;
  const fees = (FEE_BPS[market] / 1e4) * Math.abs(notional);

  const trade: PaperTrade = {
    id: makeId("t"),
    agentId: agent.id,
    thesisId,
    symbol: args.symbol,
    market,
    side,
    qty,
    entryPrice,
    currentPrice: entryPrice,
    openedAt: new Date().toISOString(),
    rationale,
    status: "open",
    feesUsd: fees,
    stopLossPct,
    takeProfitPct,
  };

  mutate(s => {
    s.trades[trade.id] = trade;
    s.cashUsd -= fees;
    if (thesisId && s.theses[thesisId]) {
      s.theses[thesisId].linkedTradeIds.push(trade.id);
      s.theses[thesisId].status = "active";
      s.theses[thesisId].updatedAt = trade.openedAt;
    }
  });
  return trade;
}

/** Mark every open position to the live ref price. Triggers stops. */
export function markAndManage(getRef: (t: PaperTrade) => number): { closed: PaperTrade[]; updates: PaperTrade[] } {
  const closed: PaperTrade[] = [];
  const updates: PaperTrade[] = [];
  mutate(s => {
    for (const t of Object.values(s.trades)) {
      if (t.status !== "open") continue;
      const nextPx = Math.max(0.0001, getRef(t));
      t.currentPrice = nextPx;
      // stops
      const move = (nextPx - t.entryPrice) / t.entryPrice;
      const directional = t.side === "long" ? move : -move;
      if (directional <= -t.stopLossPct || directional >= t.takeProfitPct) {
        closeTradeMut(s, t, "stop/tp");
        closed.push({ ...t });
      } else {
        updates.push({ ...t });
      }
    }
  });
  return { closed, updates };
}

export function manualClose(tradeId: string, reason = "manual") {
  mutate(s => {
    const t = s.trades[tradeId];
    if (!t || t.status === "closed") return;
    closeTradeMut(s, t, reason);
  });
}

function closeTradeMut(s: AppState, t: PaperTrade, reason: string) {
  const exitPrice = t.currentPrice;
  const direction = t.side === "long" ? 1 : -1;
  const gross = (exitPrice - t.entryPrice) * t.qty * direction;
  const exitFee = (FEE_BPS[t.market] / 1e4) * Math.abs(exitPrice * t.qty);
  const realized = gross - exitFee;

  t.exitPrice = exitPrice;
  t.closedAt = new Date().toISOString();
  t.status = "closed";
  t.realizedPnl = realized;
  t.feesUsd += exitFee;
  t.rationale += ` · closed (${reason})`;

  s.cashUsd += realized - exitFee;

  if (t.thesisId && s.theses[t.thesisId]) {
    const th = s.theses[t.thesisId];
    th.status = realized > 0 ? "profitable" : realized < 0 ? "losing" : th.status;
    th.updatedAt = t.closedAt!;
  }
}
