import Link from "next/link";
import { getState } from "@/lib/store";
import { AgentCard } from "@/components/agents/AgentCard";
import { Button } from "@/components/ui/primitives";
import { AutoRefresh } from "@/components/shell/AutoRefresh";

export default function AgentsPage() {
  const s = getState();
  const agents = Object.values(s.agents).sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
  const trades = Object.values(s.trades);
  const theses = Object.values(s.theses);

  return (
    <div className="space-y-5">
      <AutoRefresh ms={2500} />
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agent Monitor</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            {agents.length} agents deployed · {agents.filter(a => a.status === "paper_trading").length} actively trading
          </p>
        </div>
        <Link href="/deploy"><Button variant="primary">Deploy new agent</Button></Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {agents.map(a => (
          <AgentCard key={a.id} agent={a} trades={trades} theses={theses} />
        ))}
      </div>
    </div>
  );
}
