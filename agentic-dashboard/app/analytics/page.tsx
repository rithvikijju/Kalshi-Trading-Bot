import { getState } from "@/lib/store";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { PerformanceChart, GenericBarChart } from "@/components/charts/PerformanceChart";
import { AutoRefresh } from "@/components/shell/AutoRefresh";
import { MARKET_LABEL, STRATEGY_LABEL, fmtUsd, fmtPct, cn, tone as toneFor } from "@/lib/utils";

export default function AnalyticsPage() {
  const s = getState();
  const agents = Object.values(s.agents);
  const trades = Object.values(s.trades);
  const theses = Object.values(s.theses);

  // PnL series
  const equity = s.pnlHistory.slice(-400).map(p => ({ ts: p.ts, equity: p.totalEquity }));

  // Per-agent PnL
  const pnlByAgent: Record<string, number> = {};
  for (const t of trades) {
    const v = t.status === "open"
      ? (t.currentPrice - t.entryPrice) * t.qty * (t.side === "long" ? 1 : -1) - t.feesUsd
      : (t.realizedPnl ?? 0);
    pnlByAgent[t.agentId] = (pnlByAgent[t.agentId] ?? 0) + v;
  }
  const agentRank = agents.map(a => ({
    name: a.name.replace("Agent","").trim(),
    pnl: Math.round((pnlByAgent[a.id] ?? 0) * 100) / 100,
  })).sort((a,b) => b.pnl - a.pnl);

  // Thesis success rate
  const profit = theses.filter(t => t.status === "profitable").length;
  const losing = theses.filter(t => t.status === "losing").length;
  const denominator = profit + losing;
  const successRate = denominator > 0 ? profit / denominator : 0;

  // Market type performance
  const byMarket: Record<string, number> = {};
  for (const t of trades) {
    const v = t.status === "open"
      ? (t.currentPrice - t.entryPrice) * t.qty * (t.side === "long" ? 1 : -1) - t.feesUsd
      : (t.realizedPnl ?? 0);
    byMarket[t.market] = (byMarket[t.market] ?? 0) + v;
  }
  const marketRank = Object.entries(byMarket).map(([m, v]) => ({
    name: MARKET_LABEL[m] ?? m, pnl: Math.round(v * 100) / 100,
  })).sort((a,b) => b.pnl - a.pnl);

  // Strategy type performance
  const byStrategy: Record<string, number> = {};
  for (const t of trades) {
    const a = s.agents[t.agentId]; if (!a) continue;
    const v = t.status === "open"
      ? (t.currentPrice - t.entryPrice) * t.qty * (t.side === "long" ? 1 : -1) - t.feesUsd
      : (t.realizedPnl ?? 0);
    byStrategy[a.strategy] = (byStrategy[a.strategy] ?? 0) + v;
  }
  const strategyRank = Object.entries(byStrategy).map(([s, v]) => ({
    name: STRATEGY_LABEL[s] ?? s, pnl: Math.round(v * 100) / 100,
  })).sort((a,b) => b.pnl - a.pnl);

  // Drawdown
  let peak = -Infinity, drawdown = 0, currentDD = 0;
  for (const p of s.pnlHistory) {
    peak = Math.max(peak, p.totalEquity);
    drawdown = Math.max(drawdown, peak - p.totalEquity);
    currentDD = peak - p.totalEquity;
  }

  // Confidence vs realized PnL scatter — keep as table for simplicity
  const closed = trades.filter(t => t.status === "closed").slice(-30).reverse();

  // Exposure by market (current)
  const exposureByMarket: Record<string, number> = {};
  for (const t of trades) {
    if (t.status !== "open") continue;
    exposureByMarket[t.market] = (exposureByMarket[t.market] ?? 0) + Math.abs(t.qty * t.currentPrice);
  }
  const exposureRank = Object.entries(exposureByMarket).map(([m, v]) => ({
    name: MARKET_LABEL[m] ?? m, pnl: Math.round(v),
  })).sort((a,b) => b.pnl - a.pnl);

  return (
    <div className="space-y-5">
      <AutoRefresh ms={3500} />
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
        <p className="text-sm text-fg-muted mt-0.5">Cross-cutting performance views over the in-process state.</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Paper P&L over time</CardTitle>
            <Badge tone="accent">{s.pnlHistory.length} ticks</Badge>
          </CardHeader>
          <CardBody>
            {equity.length > 1 ? <PerformanceChart data={equity} height={300} /> :
             <div className="h-[300px] flex items-center justify-center text-fg-dim text-sm">Need more ticks…</div>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>Drawdown</CardTitle></CardHeader>
          <CardBody>
            <div className="grid grid-cols-2 gap-2 text-[12.5px]">
              <KV k="Peak equity" v={fmtUsd(Math.max(...s.pnlHistory.map(p => p.totalEquity)))} />
              <KV k="Max drawdown" v={fmtUsd(drawdown, { cents: true })} tone={drawdown > 0 ? "neg" : undefined}/>
              <KV k="Current DD" v={fmtUsd(currentDD, { cents: true })} tone={currentDD > 0 ? "neg" : undefined}/>
              <KV k="Drawdown %" v={fmtPct((peak > 0 ? drawdown / peak : 0), 2, false)} tone={drawdown > 0 ? "neg" : undefined}/>
            </div>
            <div className="mt-3 text-[11.5px] text-fg-muted">
              Drawdown measured against rolling peak across the sampled equity history (tick worker).
            </div>
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <Card>
          <CardHeader>
            <CardTitle>Agent performance ranking</CardTitle>
            <span className="text-[11.5px] text-fg-muted">{agents.length} agents</span>
          </CardHeader>
          <CardBody>
            <GenericBarChart data={agentRank} xKey="name" yKey="pnl" color="var(--color-accent)" height={260}/>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Market type performance</CardTitle>
            <span className="text-[11.5px] text-fg-muted">aggregate P&L by market</span>
          </CardHeader>
          <CardBody>
            <GenericBarChart data={marketRank} xKey="name" yKey="pnl" color="var(--color-accent-2)" height={260}/>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Strategy type performance</CardTitle>
            <span className="text-[11.5px] text-fg-muted">aggregate P&L by strategy</span>
          </CardHeader>
          <CardBody>
            <GenericBarChart data={strategyRank} xKey="name" yKey="pnl" color="var(--color-info)" height={260}/>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Current exposure by market</CardTitle>
            <span className="text-[11.5px] text-fg-muted">gross USD across open positions</span>
          </CardHeader>
          <CardBody>
            <GenericBarChart data={exposureRank} xKey="name" yKey="pnl" color="var(--color-warn)" height={260}/>
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <Card>
          <CardHeader>
            <CardTitle>Thesis success rate</CardTitle>
            <Badge tone={successRate >= 0.5 ? "pos" : "neg"}>{(successRate*100).toFixed(0)}%</Badge>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-2 gap-2">
              <KV k="Profitable theses" v={String(profit)} tone="pos" />
              <KV k="Losing theses" v={String(losing)} tone="neg" />
              <KV k="Active / watching" v={String(theses.filter(t => t.status === "active" || t.status === "watching").length)} />
              <KV k="Invalidated" v={String(theses.filter(t => t.status === "invalidated").length)} />
            </div>
          </CardBody>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Confidence vs realized PnL (last 30 closed)</CardTitle>
            <span className="text-[11.5px] text-fg-muted">are higher-confidence trades actually winning?</span>
          </CardHeader>
          <CardBody>
            <div className="overflow-x-auto">
              <table className="min-w-full text-[12.5px]">
                <thead>
                  <tr className="text-[10.5px] font-mono uppercase tracking-wider text-fg-dim text-left border-b border-default">
                    <th className="px-3 py-2">Symbol</th>
                    <th className="px-3 py-2 text-right">Confidence</th>
                    <th className="px-3 py-2 text-right">Realized</th>
                    <th className="px-3 py-2">Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {closed.map(t => {
                    const th = t.thesisId ? s.theses[t.thesisId] : undefined;
                    return (
                      <tr key={t.id} className="border-t border-default">
                        <td className="px-3 py-1.5 font-mono">{t.symbol}</td>
                        <td className="px-3 py-1.5 text-right font-mono tabular text-fg-muted">{th ? (th.confidence*100).toFixed(0) + "%" : "—"}</td>
                        <td className={cn("px-3 py-1.5 text-right tabular font-mono", toneFor(t.realizedPnl ?? 0))}>
                          {fmtUsd(t.realizedPnl, { signed: true, cents: true })}
                        </td>
                        <td className="px-3 py-1.5 text-fg-muted">{s.agents[t.agentId]?.name ?? "—"}</td>
                      </tr>
                    );
                  })}
                  {closed.length === 0 && (
                    <tr><td colSpan={4} className="px-3 py-4 text-center text-fg-dim">no closed trades yet</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function KV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" }) {
  return (
    <div className="rounded-md border border-default bg-panel-2 px-2.5 py-2">
      <div className="text-[10.5px] uppercase tracking-wider font-mono text-fg-dim">{k}</div>
      <div className={cn("text-[14px] tabular font-medium mt-0.5",
        tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-fg")}>{v}</div>
    </div>
  );
}
