"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, Lightbulb } from "lucide-react";
import { Badge, Card, CardBody } from "@/components/ui/primitives";
import { MARKET_LABEL, STATUS_TONE, fmtBps, fmtPct, fmtRelative, cn } from "@/lib/utils";
import type { Thesis, PaperTrade, Agent } from "@/lib/types";

export function ThesisCard({
  thesis, agent, trades,
}: { thesis: Thesis; agent?: Agent; trades: PaperTrade[] }) {
  const [open, setOpen] = useState(false);
  const linked = trades.filter(t => thesis.linkedTradeIds.includes(t.id));
  const realized = linked.filter(t => t.status === "closed")
    .reduce((acc, t) => acc + (t.realizedPnl ?? 0), 0);

  return (
    <Card>
      <CardBody className="p-4">
        <button onClick={() => setOpen(o => !o)} className="w-full text-left flex items-start gap-3">
          <div className="mt-0.5">
            {open ? <ChevronDown size={14} className="text-fg-muted" /> : <ChevronRight size={14} className="text-fg-muted" />}
          </div>
          <div className="flex-1">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-2">
                <Lightbulb size={14} className="text-accent mt-0.5" />
                <div>
                  <div className="text-sm font-medium text-fg leading-snug">{thesis.title}</div>
                  <div className="text-[11px] text-fg-dim font-mono mt-0.5">
                    {MARKET_LABEL[thesis.market]} · {thesis.symbol}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <Badge tone={
                  thesis.status === "profitable" ? "pos" :
                  thesis.status === "losing" ? "neg" :
                  thesis.status === "invalidated" ? "neutral" :
                  thesis.status === "active" ? "accent" : "info"
                }>{thesis.status}</Badge>
                <span className="text-[11px] font-mono text-fg-muted tabular">
                  {(thesis.confidence*100).toFixed(0)}%
                </span>
              </div>
            </div>
            <div className="mt-1.5 text-[13px] text-fg-muted line-clamp-2">{thesis.summary}</div>
            <div className="mt-2 flex items-center gap-3 text-[11px] text-fg-dim">
              <span className="font-mono">edge {fmtBps(thesis.expectedEdgeBps)}</span>
              <span className="font-mono">horizon {thesis.horizonHours.toFixed(1)}h</span>
              <span className="font-mono">{linked.length} trade{linked.length !== 1 && "s"}</span>
              <span className="font-mono">{fmtRelative(thesis.updatedAt)}</span>
            </div>
          </div>
        </button>

        {open && (
          <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-3 text-[12.5px]">
            <Section title="Catalyst">{thesis.catalyst}</Section>
            <Section title="Evidence">
              <ul className="space-y-1 font-mono text-[11.5px]">
                {thesis.evidence.map((e, i) => (
                  <li key={i} className="text-fg-muted">• {e}</li>
                ))}
              </ul>
            </Section>
            <Section title="Risks">
              <ul className="space-y-1 text-[12px]">
                {thesis.risks.map((r, i) => <li key={i} className="text-fg-muted">– {r}</li>)}
              </ul>
            </Section>
            <Section title="Data sources">
              <div className="flex flex-wrap gap-1">
                {thesis.dataSources.map(s => (
                  <span key={s} className="text-[10.5px] font-mono uppercase tracking-wider rounded-sm bg-panel-2 border border-default px-1.5 py-0.5 text-fg-muted">
                    {s}
                  </span>
                ))}
              </div>
            </Section>
            <Section title="Agent">
              <div className="text-fg-muted">{agent ? agent.name : "—"}</div>
            </Section>
            <Section title="Linked PnL">
              <div className={cn("font-mono tabular", realized > 0 ? "text-pos" : realized < 0 ? "text-neg" : "text-fg-muted")}>
                {realized.toFixed(2)} USD realized · {linked.filter(t => t.status === "open").length} open
              </div>
            </Section>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase font-mono tracking-wider text-fg-dim mb-1">{title}</div>
      <div className="text-fg">{children}</div>
    </div>
  );
}
