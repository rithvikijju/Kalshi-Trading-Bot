import { getState } from "@/lib/store";
import { ThesisCard } from "@/components/theses/ThesisCard";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { AutoRefresh } from "@/components/shell/AutoRefresh";

const STATUS_ORDER = ["active", "watching", "profitable", "losing", "invalidated"] as const;

export default function ThesesPage() {
  const s = getState();
  const theses = Object.values(s.theses).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const trades = Object.values(s.trades);

  const grouped: Record<string, typeof theses> = {};
  for (const t of theses) (grouped[t.status] = grouped[t.status] || []).push(t);

  const counts = Object.fromEntries(STATUS_ORDER.map(k => [k, (grouped[k] ?? []).length]));

  return (
    <div className="space-y-5">
      <AutoRefresh ms={3000} />
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Thesis Explorer</h1>
        <p className="text-sm text-fg-muted mt-0.5">
          {theses.length} theses discovered by agents · click any row to expand
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>By status</CardTitle>
          <div className="flex flex-wrap gap-1">
            {STATUS_ORDER.map(k => (
              <Badge key={k} tone={k === "profitable" ? "pos" : k === "losing" ? "neg" : k === "invalidated" ? "neutral" : k === "active" ? "accent" : "info"}>
                {k} · {counts[k]}
              </Badge>
            ))}
          </div>
        </CardHeader>
        <CardBody className="space-y-2">
          {theses.length === 0 ? (
            <div className="text-fg-dim text-sm">No theses yet — agents are still scanning their data sources.</div>
          ) : theses.slice(0, 80).map(t => (
            <div id={t.id} key={t.id}>
              <ThesisCard thesis={t} agent={s.agents[t.agentId]} trades={trades} />
            </div>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}
