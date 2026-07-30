import { getState, calcTotalEquity } from "@/lib/store";
import { TradeTable } from "@/components/trades/TradeTable";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { MetricCard } from "@/components/metrics/MetricCard";
import { AutoRefresh } from "@/components/shell/AutoRefresh";
import { fmtUsd, fmtPct } from "@/lib/utils";

export default function TradesPage() {
  const s = getState();
  const all = Object.values(s.trades).sort((a, b) => b.openedAt.localeCompare(a.openedAt));
  const open = all.filter(t => t.status === "open");
  const closed = all.filter(t => t.status === "closed");
  const { unrealized, realized, exposure } = calcTotalEquity(s);
  const fees = all.reduce((acc, t) => acc + t.feesUsd, 0);

  return (
    <div className="space-y-5">
      <AutoRefresh ms={2500} />
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Paper Trading</h1>
          <p className="text-sm text-fg-muted mt-0.5">All paper trades from every agent. Stops & targets enforced by riskManager.</p>
        </div>
        <Badge tone="warn">paper · no live execution</Badge>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Open positions" value={String(open.length)} hint={fmtUsd(exposure) + " gross exposure"} />
        <MetricCard label="Closed trades" value={String(closed.length)} hint={`${fmtUsd(fees,{cents:true})} fees paid`} />
        <MetricCard label="Realized P&L" value={fmtUsd(realized, { signed: true, cents: true })}
                    tone={realized > 0 ? "pos" : realized < 0 ? "neg" : "neutral"} emphasizeTone />
        <MetricCard label="Unrealized P&L" value={fmtUsd(unrealized, { signed: true, cents: true })}
                    tone={unrealized > 0 ? "pos" : unrealized < 0 ? "neg" : "neutral"} emphasizeTone />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Open positions</CardTitle>
          <span className="text-[11.5px] text-fg-muted">{open.length} live</span>
        </CardHeader>
        <CardBody><TradeTable trades={open} agents={s.agents} /></CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Closed positions</CardTitle>
          <span className="text-[11.5px] text-fg-muted">{closed.length} closed</span>
        </CardHeader>
        <CardBody><TradeTable trades={closed.slice(0, 80)} agents={s.agents} /></CardBody>
      </Card>
    </div>
  );
}
