import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { MetricCard } from "@/components/metrics/MetricCard";
import { ActivityFeed } from "@/components/feed/ActivityFeed";
import { PerformanceChart } from "@/components/charts/PerformanceChart";
import { AutoRefresh } from "@/components/shell/AutoRefresh";
import {
  getState, calcTotalEquity, winRate, sharpeEstimate, recentDiscoveries,
} from "@/lib/store";
import { cn, fmtUsd, fmtPct, MARKET_LABEL, tone } from "@/lib/utils";
import Link from "next/link";
import { ArrowRight, Bot } from "lucide-react";

export default function HomePage() {
  const s = getState();
  const { equity, unrealized, realized, exposure } = calcTotalEquity(s);
  const wr = winRate(s);
  const sharpe = sharpeEstimate(s);
  const agents = Object.values(s.agents);
  const trades = Object.values(s.trades);
  const openTrades = trades.filter(t => t.status === "open");

  const theses = Object.values(s.theses);
  const activeTheses = theses.filter(t => t.status === "active" || t.status === "watching");
  const recentTheses = [...theses].sort((a,b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, 6);

  const equitySpark = s.pnlHistory.slice(-40).map(p => p.totalEquity);
  const equitySeries = s.pnlHistory.slice(-200).map(p => ({ ts: p.ts, equity: p.totalEquity }));

  const pnlByAgent = new Map<string, number>();
  for (const t of trades) {
    const v = t.status === "open"
      ? (t.currentPrice - t.entryPrice) * t.qty * (t.side === "long" ? 1 : -1) - t.feesUsd
      : (t.realizedPnl ?? 0);
    pnlByAgent.set(t.agentId, (pnlByAgent.get(t.agentId) ?? 0) + v);
  }
  const topAgents = [...agents]
    .map(a => ({ a, pnl: pnlByAgent.get(a.id) ?? 0 }))
    .sort((x, y) => y.pnl - x.pnl)
    .slice(0, 5);

  return (
    <div className="space-y-5">
      <AutoRefresh ms={2500} />

      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Command Center</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            Live agent activity across {agents.length} agents, {activeTheses.length} active theses.
          </p>
        </div>
        <Link href="/deploy" className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline">
          Deploy a new agent <ArrowRight size={14} />
        </Link>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <MetricCard label="Total agents" value={String(agents.length)} hint={`${agents.filter(a=>a.status!=="paused").length} active`} />
        <MetricCard label="Active theses" value={String(activeTheses.length)} hint={`${theses.length} total tracked`} />
        <MetricCard label="Paper P&L" value={fmtUsd(unrealized + realized, { signed: true, cents: true })}
                    tone={unrealized+realized>0?"pos":unrealized+realized<0?"neg":"neutral"} emphasizeTone
                    spark={equitySpark} hint={`realized ${fmtUsd(realized,{signed:true,cents:true})}, unreal ${fmtUsd(unrealized,{signed:true,cents:true})}`} />
        <MetricCard label="Win rate" value={fmtPct(wr.rate, 1, false)}
                    hint={`${wr.wins} W / ${wr.losses} L`}
                    tone={wr.rate > 0.5 ? "pos" : wr.rate < 0.45 && wr.wins+wr.losses > 5 ? "neg" : "neutral"} />
        <MetricCard label="Sharpe est" value={sharpe ? sharpe.toFixed(2) : "—"}
                    hint="Per-trade Sharpe × √(252·3)" tone={sharpe && sharpe > 1 ? "pos" : "neutral"} />
        <MetricCard label="Exposure" value={fmtUsd(exposure)} hint={`${openTrades.length} open positions`} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <Card className="xl:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Equity curve</CardTitle>
              <p className="text-[11.5px] text-fg-muted mt-0.5">Live equity from the in-process broker · 1.5s tick</p>
            </div>
            <Badge tone="accent">paper · {fmtUsd(equity, { cents: true })}</Badge>
          </CardHeader>
          <CardBody>
            {equitySeries.length > 1 ? (
              <PerformanceChart data={equitySeries} height={260} />
            ) : (
              <div className="h-[260px] flex items-center justify-center text-fg-dim text-sm">
                Worker spinning up… curve appears after first ticks.
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Market regime</CardTitle>
              <p className="text-[11.5px] text-fg-muted mt-0.5">Conditioning context for every agent.</p>
            </div>
            <Badge tone={s.regime.riskOn ? "pos" : "warn"}>{s.regime.riskOn ? "risk-on" : "risk-off"}</Badge>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-2 gap-2 mb-3">
              <Tile label="VIX" value={s.regime.vix?.toFixed(2) ?? "—"} />
              <Tile label="BTC 30d vol" value={s.regime.btcVol30d ? `${(s.regime.btcVol30d*100).toFixed(0)}%` : "—"} />
              <Tile label="Rates regime" value={s.regime.ratesRegime} />
              <Tile label="Cash" value={fmtUsd(s.cashUsd)} />
            </div>
            <ul className="space-y-1 text-[12.5px]">
              {s.regime.notes.map((n,i) => <li key={i} className="text-fg-muted">• {n}</li>)}
            </ul>
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <Card>
          <CardHeader>
            <CardTitle>Top performing agents</CardTitle>
            <Link href="/agents" className="text-[11.5px] text-fg-muted hover:text-fg">view all</Link>
          </CardHeader>
          <CardBody className="px-2">
            <div className="divide-y divide-[color-mix(in_oklab,var(--color-fg-dim)_10%,transparent)]">
              {topAgents.map(({a, pnl}) => (
                <Link href={`/agents/${a.id}`} key={a.id} className="flex items-center justify-between px-2 py-2 hover:bg-panel-2 rounded-md">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Bot size={14} className="text-fg-muted shrink-0" />
                    <div className="min-w-0">
                      <div className="text-[13px] text-fg truncate">{a.name}</div>
                      <div className="text-[11px] text-fg-dim font-mono">{MARKET_LABEL[a.market]}</div>
                    </div>
                  </div>
                  <div className={cn("text-[12.5px] tabular font-mono", tone(pnl))}>
                    {fmtUsd(pnl, { signed: true, cents: true })}
                  </div>
                </Link>
              ))}
              {topAgents.length === 0 && <div className="px-2 py-3 text-fg-dim text-sm">no agents yet</div>}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latest thesis alerts</CardTitle>
            <Link href="/theses" className="text-[11.5px] text-fg-muted hover:text-fg">explore</Link>
          </CardHeader>
          <CardBody className="px-2">
            <div className="divide-y divide-[color-mix(in_oklab,var(--color-fg-dim)_10%,transparent)]">
              {recentTheses.map(t => (
                <Link key={t.id} href={`/theses#${t.id}`} className="flex flex-col px-2 py-2 hover:bg-panel-2 rounded-md">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[13px] text-fg truncate">{t.title}</div>
                    <Badge tone={t.status === "profitable" ? "pos" : t.status === "losing" ? "neg" : t.status === "active" ? "accent" : "info"}>{t.status}</Badge>
                  </div>
                  <div className="text-[11px] text-fg-dim font-mono mt-0.5">
                    {MARKET_LABEL[t.market]} · edge {t.expectedEdgeBps}bps · conf {(t.confidence*100).toFixed(0)}%
                  </div>
                </Link>
              ))}
              {recentTheses.length === 0 && <div className="px-2 py-3 text-fg-dim text-sm">waiting for first thesis</div>}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent discoveries</CardTitle>
            <Link href="/feed" className="text-[11.5px] text-fg-muted hover:text-fg">feed</Link>
          </CardHeader>
          <CardBody>
            <ActivityFeed items={recentDiscoveries(10)} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-default bg-panel-2 px-2.5 py-2">
      <div className="text-[10.5px] uppercase tracking-wider font-mono text-fg-dim">{label}</div>
      <div className="text-[14px] tabular font-medium text-fg mt-0.5">{value}</div>
    </div>
  );
}
