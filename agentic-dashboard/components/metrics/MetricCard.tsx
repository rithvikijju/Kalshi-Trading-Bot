import { Card, CardBody, Sparkline } from "@/components/ui/primitives";
import { cn, tone as toneFor } from "@/lib/utils";

export function MetricCard({
  label,
  value,
  delta,
  spark,
  hint,
  tone,
  emphasizeTone = false,
}: {
  label: string;
  value: string;
  delta?: string;
  spark?: number[];
  hint?: string;
  tone?: "pos" | "neg" | "warn" | "info" | "neutral";
  emphasizeTone?: boolean;
}) {
  const toneClass =
    tone === "pos" ? "text-pos"
    : tone === "neg" ? "text-neg"
    : tone === "warn" ? "text-warn"
    : tone === "info" ? "text-info"
    : "text-fg";
  return (
    <Card className="relative">
      <CardBody className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="text-[11px] uppercase tracking-wider text-fg-dim font-mono">
            {label}
          </div>
          {spark && spark.length > 1 && (
            <Sparkline values={spark} stroke="var(--color-accent)" />
          )}
        </div>
        <div className="mt-2 flex items-baseline gap-3">
          <div className={cn("text-[26px] tabular font-semibold tracking-tight",
            emphasizeTone ? toneClass : "text-fg")}>
            {value}
          </div>
          {delta && (
            <div className={cn("text-[12px] tabular font-mono", toneFor(parseFloat(delta) || 0))}>
              {delta}
            </div>
          )}
        </div>
        {hint && <div className="mt-1 text-[11.5px] text-fg-muted">{hint}</div>}
      </CardBody>
    </Card>
  );
}
