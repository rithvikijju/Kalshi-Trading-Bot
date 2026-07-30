import Link from "next/link";
import { Badge } from "@/components/ui/primitives";
import { cn, fmtUsd, fmtPct, fmtRelative, MARKET_LABEL, tone } from "@/lib/utils";
import type { PaperTrade, Agent } from "@/lib/types";

export function TradeTable({
  trades, agents, showAgent = true,
}: { trades: PaperTrade[]; agents: Record<string, Agent>; showAgent?: boolean }) {
  return (
    <div className="overflow-x-auto rounded-md border border-default bg-panel">
      <table className="min-w-full text-[12.5px]">
        <thead>
          <tr className="text-left text-[10.5px] font-mono uppercase tracking-wider text-fg-dim border-b border-default">
            <th className="px-3 py-2.5">Status</th>
            <th className="px-3 py-2.5">Symbol</th>
            <th className="px-3 py-2.5">Market</th>
            <th className="px-3 py-2.5">Side</th>
            <th className="px-3 py-2.5 text-right">Qty</th>
            <th className="px-3 py-2.5 text-right">Entry</th>
            <th className="px-3 py-2.5 text-right">Current</th>
            <th className="px-3 py-2.5 text-right">Exit</th>
            <th className="px-3 py-2.5 text-right">P&L</th>
            <th className="px-3 py-2.5">Stop / Tgt</th>
            {showAgent && <th className="px-3 py-2.5">Agent</th>}
            <th className="px-3 py-2.5">Opened</th>
            <th className="px-3 py-2.5">Rationale</th>
          </tr>
        </thead>
        <tbody>
          {trades.length === 0 ? (
            <tr><td colSpan={13} className="px-3 py-6 text-center text-fg-dim">no trades</td></tr>
          ) : trades.map((t) => {
            const directional = (t.currentPrice - t.entryPrice) / t.entryPrice * (t.side === "long" ? 1 : -1);
            const liveOrFinal = t.status === "open"
              ? (t.currentPrice - t.entryPrice) * t.qty * (t.side === "long" ? 1 : -1) - t.feesUsd
              : t.realizedPnl ?? 0;
            return (
              <tr key={t.id} className="border-t border-default hover:bg-panel-2/60">
                <td className="px-3 py-2"><Badge tone={t.status === "open" ? "info" : "neutral"}>{t.status}</Badge></td>
                <td className="px-3 py-2 font-mono text-fg">{t.symbol}</td>
                <td className="px-3 py-2 text-fg-muted">{MARKET_LABEL[t.market]}</td>
                <td className="px-3 py-2">
                  <span className={cn("font-mono uppercase text-[11px]", t.side === "long" ? "text-pos" : "text-neg")}>{t.side}</span>
                </td>
                <td className="px-3 py-2 text-right tabular font-mono text-fg-muted">{t.qty.toFixed(2)}</td>
                <td className="px-3 py-2 text-right tabular font-mono text-fg-muted">{t.entryPrice.toFixed(4)}</td>
                <td className="px-3 py-2 text-right tabular font-mono">{t.currentPrice.toFixed(4)}</td>
                <td className="px-3 py-2 text-right tabular font-mono text-fg-muted">
                  {t.exitPrice !== undefined ? t.exitPrice.toFixed(4) : "—"}
                </td>
                <td className={cn("px-3 py-2 text-right tabular font-mono", tone(liveOrFinal))}>
                  {fmtUsd(liveOrFinal, { signed: true, cents: true })}
                </td>
                <td className="px-3 py-2 text-fg-muted font-mono text-[11px]">
                  -{(t.stopLossPct*100).toFixed(1)}% / +{(t.takeProfitPct*100).toFixed(1)}%
                </td>
                {showAgent && (
                  <td className="px-3 py-2">
                    <Link href={`/agents/${t.agentId}`} className="text-fg-muted hover:text-fg">
                      {agents[t.agentId]?.name ?? "—"}
                    </Link>
                  </td>
                )}
                <td className="px-3 py-2 text-fg-dim font-mono text-[11px]">{fmtRelative(t.openedAt)}</td>
                <td className="px-3 py-2 max-w-[280px] truncate text-fg-muted" title={t.rationale}>{t.rationale}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
