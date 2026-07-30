import Link from "next/link";
import { Activity, AlertCircle, Zap } from "lucide-react";
import { fmtRelative, MARKET_LABEL, cn } from "@/lib/utils";
import type { DiscoveryEvent } from "@/lib/types";

export function ActivityFeed({ items, compact = false }: { items: DiscoveryEvent[]; compact?: boolean }) {
  if (items.length === 0) return <div className="text-fg-dim text-sm">no activity yet</div>;
  return (
    <div className="space-y-1">
      {items.map((e) => {
        const Icon = e.severity === "alert" ? AlertCircle : e.severity === "signal" ? Zap : Activity;
        const sev = e.severity === "alert" ? "text-neg" : e.severity === "signal" ? "text-accent" : "text-info";
        return (
          <div key={e.id} className="flex items-start gap-2.5 rounded-md hover:bg-panel-2 px-2 py-1.5">
            <div className="pt-0.5"><Icon size={13} className={cn(sev)} /></div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] text-fg">{e.headline}</span>
                {!compact && (
                  <span className="text-[10.5px] font-mono uppercase tracking-wider text-fg-dim">
                    {MARKET_LABEL[e.market]}
                  </span>
                )}
              </div>
              {!compact && (
                <div className="text-[12px] text-fg-muted line-clamp-1">{e.detail}</div>
              )}
              <div className="text-[10.5px] text-fg-dim font-mono flex items-center gap-2 mt-0.5">
                <Link href={`/agents/${e.agentId}`} className="hover:text-fg-muted">{e.agentName}</Link>
                <span>·</span>
                <span>{fmtRelative(e.ts)}</span>
                {e.thesisId && (
                  <>
                    <span>·</span>
                    <Link href={`/theses#${e.thesisId}`} className="hover:text-fg-muted">thesis</Link>
                  </>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
