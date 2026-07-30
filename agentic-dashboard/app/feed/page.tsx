import { getState, recentDiscoveries } from "@/lib/store";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { ActivityFeed } from "@/components/feed/ActivityFeed";
import { AutoRefresh } from "@/components/shell/AutoRefresh";

export default function FeedPage() {
  const s = getState();
  const all = recentDiscoveries(200);
  const counts = {
    alert: all.filter(d => d.severity === "alert").length,
    signal: all.filter(d => d.severity === "signal").length,
    info: all.filter(d => d.severity === "info").length,
  };
  return (
    <div className="space-y-5">
      <AutoRefresh ms={2500} />
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Market Discovery Feed</h1>
        <p className="text-sm text-fg-muted mt-0.5">
          Live stream of every interesting signal agents pick up across {Object.keys(s.agents).length} agents.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Real-time</CardTitle>
          <div className="flex items-center gap-1">
            <Badge tone="neg">alerts · {counts.alert}</Badge>
            <Badge tone="accent">signals · {counts.signal}</Badge>
            <Badge tone="info">info · {counts.info}</Badge>
          </div>
        </CardHeader>
        <CardBody>
          {all.length === 0
            ? <div className="text-fg-dim text-sm">Tick worker warming up — first signals appear within ~5 seconds.</div>
            : <ActivityFeed items={all} />}
        </CardBody>
      </Card>
    </div>
  );
}
