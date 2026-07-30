"use client";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Point = { ts: string; equity: number };

export function PerformanceChart({ data, height = 280 }: { data: Point[]; height?: number }) {
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 12, right: 12, left: -8, bottom: 0 }}>
          <defs>
            <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.55} />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis dataKey="ts" stroke="var(--color-fg-dim)"
                 tickFormatter={(v) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                 tick={{ fontSize: 10 }} />
          <YAxis stroke="var(--color-fg-dim)" tick={{ fontSize: 10 }}
                 tickFormatter={(v) => `$${(v/1000).toFixed(0)}K`} domain={["dataMin - 500", "dataMax + 500"]} />
          <Tooltip
            contentStyle={{ background: "#0b0f15", border: "1px solid #1a212d", borderRadius: 6, fontSize: 11 }}
            labelStyle={{ color: "#8b9bb3" }}
            formatter={(v: any) => [`$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, "equity"]}
          />
          <Area type="monotone" dataKey="equity" stroke="var(--color-accent)" strokeWidth={1.8} fill="url(#eq)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function GenericBarChart({
  data, xKey, yKey, color = "var(--color-accent)", height = 240, label,
}: {
  data: Record<string, any>[]; xKey: string; yKey: string; color?: string; height?: number; label?: string;
}) {
  // Defer require so the chunk only ships when used
  const { Bar, BarChart, CartesianGrid: Grid, ResponsiveContainer: RC, Tooltip: T, XAxis: X, YAxis: Y } = require("recharts");
  return (
    <div style={{ width: "100%", height }}>
      <RC>
        <BarChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}>
          <Grid stroke="var(--color-border)" strokeDasharray="3 3" />
          <X dataKey={xKey} stroke="var(--color-fg-dim)" tick={{ fontSize: 10 }} interval={0} angle={-20} dy={10} />
          <Y stroke="var(--color-fg-dim)" tick={{ fontSize: 10 }} />
          <T contentStyle={{ background: "#0b0f15", border: "1px solid #1a212d", borderRadius: 6, fontSize: 11 }} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
          <Bar dataKey={yKey} fill={color} radius={[3, 3, 0, 0]} />
        </BarChart>
      </RC>
    </div>
  );
}
