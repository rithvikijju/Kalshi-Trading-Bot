import { getState, recentLogs } from "@/lib/store";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { AgentLogPanel } from "@/components/logs/AgentLogPanel";
import { AutoRefresh } from "@/components/shell/AutoRefresh";
import Link from "next/link";

export default function LogsPage() {
  const s = getState();
  const agents = Object.values(s.agents);
  const allLogs = recentLogs(undefined, 200);
  return (
    <div className="space-y-5">
      <AutoRefresh ms={2500} />
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agent Logs</h1>
        <p className="text-sm text-fg-muted mt-0.5">
          Raw log stream across all agents. Each line shows what was searched, found, decided, executed, or monitored next.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All agents</CardTitle>
          <Badge tone="info">live · 200 most recent</Badge>
        </CardHeader>
        <CardBody><AgentLogPanel logs={allLogs} /></CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Per-agent quick view</CardTitle>
          <span className="text-[11.5px] text-fg-muted">Last 40 lines per agent · open full log on the agent page</span>
        </CardHeader>
        <CardBody className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {agents.map(a => (
            <div key={a.id}>
              <div className="flex items-center justify-between mb-2">
                <Link href={`/agents/${a.id}`} className="text-sm text-fg hover:underline">{a.name}</Link>
                <span className="text-[10.5px] font-mono uppercase tracking-wider text-fg-dim">{a.status.replace("_"," ")}</span>
              </div>
              <AgentLogPanel logs={recentLogs(a.id, 40)} />
            </div>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}
