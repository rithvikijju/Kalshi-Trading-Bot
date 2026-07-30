import { getState } from "@/lib/store";
import { DeployAgentForm } from "@/components/agents/DeployAgentForm";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";

export default function DeployPage() {
  const s = getState();
  return (
    <div className="space-y-5 max-w-6xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Deploy agent</h1>
        <p className="text-sm text-fg-muted mt-0.5">
          Stand up a new research agent. It will start scanning its selected sources
          immediately, generate theses, and (when confidence beats your gate) fire
          paper trades.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <Badge tone="warn">paper trading mode</Badge>
        </CardHeader>
        <CardBody>
          <DeployAgentForm sources={s.dataSources} />
        </CardBody>
      </Card>
    </div>
  );
}
