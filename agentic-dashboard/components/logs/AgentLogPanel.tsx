import { cn, fmtRelative, STATUS_TONE } from "@/lib/utils";
import type { AgentLog } from "@/lib/types";

export function AgentLogPanel({ logs }: { logs: AgentLog[] }) {
  if (logs.length === 0) {
    return <div className="text-fg-dim text-sm font-mono">— no log lines yet —</div>;
  }
  return (
    <div className="rounded-md border border-default bg-panel font-mono text-[11.5px] divide-y divide-[color-mix(in_oklab,var(--color-fg-dim)_10%,transparent)]">
      {logs.map((l) => (
        <div key={l.id} className="grid grid-cols-[80px_70px_1fr] gap-2 px-3 py-1.5 hover:bg-panel-2">
          <span className="text-fg-dim tabular">{fmtRelative(l.ts)}</span>
          <span className={cn("uppercase tracking-wider text-[10.5px] self-center", STATUS_TONE[l.level])}>{l.level}</span>
          <span className="text-fg break-all">{l.message}</span>
        </div>
      ))}
    </div>
  );
}
