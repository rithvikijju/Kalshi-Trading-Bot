import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, Activity, AlertTriangle } from "lucide-react";
import { getState, recentLogs } from "@/lib/store";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { AgentLogPanel } from "@/components/logs/AgentLogPanel";
import { TradeTable } from "@/components/trades/TradeTable";
import { ThesisCard } from "@/components/theses/ThesisCard";
import { MARKET_LABEL, STRATEGY_LABEL, fmtUsd, fmtPct, fmtRelative, cn, tone as toneFor } from "@/lib/utils";
import { AutoRefresh } from "@/components/shell/AutoRefresh";

export default async function AgentDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const s = getState();
  const agent = s.agents[id];
  if (!agent) notFound();
  const agentTrades = Object.values(s.trades).filter(t => t.agentId === id).sort((a,b)=>b.openedAt.localeCompare(a.openedAt));
  const agentTheses = Object.values(s.theses).filter(t => t.agentId === id).sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt));
  const logs = recentLogs(id, 200);
  const open = agentTrades.filter(t => t.status === "open");
  const closed = agentTrades.filter(t => t.status === "closed");
  const unreal = open.reduce((acc, t) => acc + ((t.currentPrice - t.entryPrice) * (t.side === "long" ? 1 : -1) * t.qty - t.feesUsd), 0);
  const real = closed.reduce((acc, t) => acc + (t.realizedPnl ?? 0), 0);

  return (
    <div className="space-y-5">
      <AutoRefresh ms={2500} />
      <Link href="/agents" className="inline-flex items-center gap-1 text-fg-muted hover:text-fg text-sm">
        <ArrowLeft size={14} /> back to agents
      </Link>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{agent.name}</h1>
            <Badge tone={
              agent.status === "paper_trading" ? "pos" :
              agent.status === "researching" ? "info" :
              agent.status === "backtesting" ? "accent-2" :
              agent.status === "paused" ? "warn" : "neutral"}>{agent.status.replace("_"," ")}</Badge>
          </div>
          <div className="mt-1 text-[12.5px] text-fg-muted font-mono">
            {MARKET_LABEL[agent.market]} · {STRATEGY_LABEL[agent.strategy]} · last act {fmtRelative(agent.lastActivityAt)}
          </div>
        </div>
        <div className="flex gap-2">
          <form action="/api/agents/run" method="post">
            <input type="hidden" name="agentId" value={agent.id} />
            <Button type="submit" variant="secondary"><Activity size={13}/> run now</Button>
          </form>
          <form action="/api/agents/toggle" method="post">
            <input type="hidden" name="agentId" value={agent.id} />
            <Button type="submit" variant={agent.status === "paused" ? "primary" : "secondary"}>
              {agent.status === "paused" ? "Resume" : "Pause"}
            </Button>
          </form>
          <form action="/api/agents/clone" method="post">
            <input type="hidden" name="agentId" value={agent.id} />
            <Button type="submit" variant="secondary">Clone</Button>
          </form>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Paper P&L" value={fmtUsd(unreal+real, { signed: true, cents: true })} tone={unreal+real>0?"pos":unreal+real<0?"neg":undefined} />
        <Stat label="Open positions" value={String(open.length)} />
        <Stat label="Risk budget" value={fmtUsd(agent.riskBudgetUsd)} />
        <Stat label="Confidence gate" value={fmtPct(agent.confidenceThreshold, 0, false)} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2 space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>Recent theses</CardTitle>
              <span className="text-[11.5px] text-fg-muted">{agentTheses.length} total</span>
            </CardHeader>
            <CardBody className="space-y-2">
              {agentTheses.slice(0, 6).map(t => (
                <ThesisCard key={t.id} thesis={t} agent={agent} trades={agentTrades} />
              ))}
              {agentTheses.length === 0 && <div className="text-fg-dim text-sm">no theses generated yet</div>}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Trades</CardTitle>
              <span className="text-[11.5px] text-fg-muted">{agentTrades.length} total · {open.length} open</span>
            </CardHeader>
            <CardBody>
              <TradeTable trades={agentTrades.slice(0, 60)} agents={s.agents} showAgent={false} />
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Agent logs</CardTitle>
              <p className="text-[11.5px] text-fg-muted">What the agent is searching, deciding, executing.</p>
            </div>
            <Badge tone="info" className="gap-1.5">
              <AlertTriangle size={11}/> read-only
            </Badge>
          </CardHeader>
          <CardBody>
            <AgentLogPanel logs={logs} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="rounded-md border border-default bg-panel px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-wider font-mono text-fg-dim">{label}</div>
      <div className={cn("text-[20px] tabular font-semibold", tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-fg")}>{value}</div>
    </div>
  );
}
