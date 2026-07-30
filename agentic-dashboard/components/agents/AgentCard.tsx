import Link from "next/link";
import { Activity, Cpu, Pause, Play, Copy, Eye } from "lucide-react";
import { Badge, Card, CardBody } from "@/components/ui/primitives";
import { MARKET_LABEL, STRATEGY_LABEL, STATUS_TONE, fmtRelative, fmtUsd, fmtPct, cn } from "@/lib/utils";
import type { Agent, PaperTrade, Thesis } from "@/lib/types";

export function AgentCard({
  agent, trades, theses,
}: { agent: Agent; trades: PaperTrade[]; theses: Thesis[] }) {
  const open = trades.filter(t => t.status === "open" && t.agentId === agent.id);
  const closed = trades.filter(t => t.status === "closed" && t.agentId === agent.id);
  const unreal = open.reduce((acc, t) => acc + ((t.currentPrice - t.entryPrice) * (t.side === "long" ? 1 : -1) * t.qty - t.feesUsd), 0);
  const real = closed.reduce((acc, t) => acc + (t.realizedPnl ?? 0), 0);
  const pnl = unreal + real;
  const lastThesis = theses
    .filter(t => t.agentId === agent.id)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0];

  return (
    <Card>
      <CardBody className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <div className="h-7 w-7 rounded-md border border-default bg-panel-2 flex items-center justify-center">
                <Cpu size={13} className="text-accent" />
              </div>
              <div className="leading-tight">
                <div className="text-sm font-medium text-fg">{agent.name}</div>
                <div className="text-[11px] text-fg-dim font-mono">
                  {MARKET_LABEL[agent.market]} · {STRATEGY_LABEL[agent.strategy]}
                </div>
              </div>
            </div>
          </div>
          <Badge tone={
            agent.status === "paper_trading" ? "pos" :
            agent.status === "researching" ? "info" :
            agent.status === "backtesting" ? "accent-2" :
            agent.status === "paused" ? "warn" : "neutral"
          }>
            {agent.status.replace("_", " ")}
          </Badge>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2">
          <Metric label="Paper P&L" value={fmtUsd(pnl, { signed: true })} tone={pnl > 0 ? "pos" : pnl < 0 ? "neg" : undefined} />
          <Metric label="Open" value={String(open.length)} />
          <Metric label="Conf gate" value={fmtPct(agent.confidenceThreshold, 0, false)} />
        </div>

        <div className="mt-3 rounded-md border border-default bg-panel-2 p-2.5">
          <div className="text-[10.5px] text-fg-dim font-mono uppercase">current thesis</div>
          <div className="mt-1 text-[12.5px] text-fg leading-snug line-clamp-2">
            {lastThesis ? lastThesis.title : "—  no active thesis yet"}
          </div>
          {lastThesis && (
            <div className="mt-1 text-[11px] text-fg-muted">
              Edge ~ {lastThesis.expectedEdgeBps}bps · conf {(lastThesis.confidence*100).toFixed(0)}%
            </div>
          )}
        </div>

        <div className="mt-3 flex flex-wrap gap-1">
          {agent.dataSources.slice(0, 5).map(s => (
            <span key={s} className="text-[10.5px] font-mono uppercase tracking-wider rounded-sm bg-panel-2 border border-default px-1.5 py-0.5 text-fg-muted">
              {s}
            </span>
          ))}
          {agent.dataSources.length > 5 && (
            <span className="text-[10.5px] text-fg-dim font-mono">+{agent.dataSources.length - 5}</span>
          )}
        </div>

        <div className="mt-3 flex items-center justify-between text-[11px] text-fg-dim">
          <span className="font-mono">last act · {fmtRelative(agent.lastActivityAt)}</span>
          <div className="flex items-center gap-1">
            <Link href={`/agents/${agent.id}`} className="inline-flex items-center gap-1 px-2 py-1 rounded-md hover:bg-panel-2 text-fg-muted hover:text-fg">
              <Eye size={12} /> view
            </Link>
            <ActionForm action="/api/agents/toggle" agentId={agent.id}>
              {agent.status === "paused"
                ? <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md hover:bg-panel-2 text-fg-muted hover:text-fg"><Play size={12} /> resume</span>
                : <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md hover:bg-panel-2 text-fg-muted hover:text-fg"><Pause size={12} /> pause</span>}
            </ActionForm>
            <ActionForm action="/api/agents/clone" agentId={agent.id}>
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md hover:bg-panel-2 text-fg-muted hover:text-fg"><Copy size={12} /> clone</span>
            </ActionForm>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="rounded-md border border-default bg-panel-2 p-2">
      <div className="text-[10.5px] text-fg-dim font-mono uppercase">{label}</div>
      <div className={cn("text-[14px] tabular font-medium", tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-fg")}>
        {value}
      </div>
    </div>
  );
}

function ActionForm({ action, agentId, children }: { action: string; agentId: string; children: React.ReactNode }) {
  return (
    <form action={action} method="post" className="inline">
      <input type="hidden" name="agentId" value={agentId} />
      <button className="inline-block" type="submit">{children}</button>
    </form>
  );
}
