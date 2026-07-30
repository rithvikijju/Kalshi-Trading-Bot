"use client";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Button, Field, Input, Select } from "@/components/ui/primitives";
import type { DataSource, MarketType, StrategyType } from "@/lib/types";
import { MARKET_LABEL, STRATEGY_LABEL } from "@/lib/utils";

const MARKETS: MarketType[] = [
  "prediction_markets","crypto","equities","macro","commodities","rates",
  "private_signals","sports","news_arb","cross_asset_rv",
];
const STRATEGIES: StrategyType[] = [
  "arbitrage","momentum","mean_reversion","event_driven","sentiment",
  "statistical","market_making","relative_value",
];

export function DeployAgentForm({ sources }: { sources: DataSource[] }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [form, setForm] = useState({
    name: "Cross-Venue Probability Arb",
    market: "prediction_markets" as MarketType,
    strategy: "arbitrage" as StrategyType,
    dataSources: ["kalshi", "polymarket", "binance_perps"] as string[],
    riskBudgetUsd: 75_000,
    maxPositionUsd: 15_000,
    thesisRefreshSec: 45,
    confidenceThreshold: 0.7,
    stopLossPct: 0.06,
    takeProfitPct: 0.12,
  });

  function toggle(id: string) {
    setForm(f => ({
      ...f,
      dataSources: f.dataSources.includes(id)
        ? f.dataSources.filter(x => x !== id)
        : [...f.dataSources, id],
    }));
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    start(async () => {
      const res = await fetch("/api/agents/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (res.ok) {
        const { id } = await res.json();
        router.push(`/agents/${id}`);
        router.refresh();
      } else {
        alert("Failed to deploy agent");
      }
    });
  }

  return (
    <form onSubmit={submit} className="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <div className="space-y-4">
        <Field label="Agent name">
          <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} required />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Market type">
            <Select value={form.market} onChange={e => setForm({ ...form, market: e.target.value as MarketType })}>
              {MARKETS.map(m => <option key={m} value={m}>{MARKET_LABEL[m]}</option>)}
            </Select>
          </Field>
          <Field label="Strategy">
            <Select value={form.strategy} onChange={e => setForm({ ...form, strategy: e.target.value as StrategyType })}>
              {STRATEGIES.map(m => <option key={m} value={m}>{STRATEGY_LABEL[m]}</option>)}
            </Select>
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Risk budget (USD)" hint="Max cumulative exposure across the agent's open positions.">
            <Input type="number" value={form.riskBudgetUsd}
                   onChange={e => setForm({ ...form, riskBudgetUsd: Number(e.target.value) })}/>
          </Field>
          <Field label="Max position (USD)" hint="Per-trade cap. Confidence further scales this down.">
            <Input type="number" value={form.maxPositionUsd}
                   onChange={e => setForm({ ...form, maxPositionUsd: Number(e.target.value) })}/>
          </Field>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Thesis refresh (sec)">
            <Input type="number" value={form.thesisRefreshSec}
                   onChange={e => setForm({ ...form, thesisRefreshSec: Number(e.target.value) })}/>
          </Field>
          <Field label="Confidence gate">
            <Input type="number" step="0.01" min="0" max="1" value={form.confidenceThreshold}
                   onChange={e => setForm({ ...form, confidenceThreshold: Number(e.target.value) })}/>
          </Field>
          <Field label="Stop / Take">
            <div className="flex gap-2">
              <Input type="number" step="0.005" value={form.stopLossPct}
                     onChange={e => setForm({ ...form, stopLossPct: Number(e.target.value) })}/>
              <Input type="number" step="0.005" value={form.takeProfitPct}
                     onChange={e => setForm({ ...form, takeProfitPct: Number(e.target.value) })}/>
            </div>
          </Field>
        </div>
      </div>

      <div>
        <Field label="Data sources to scan" hint="The agent will fuse these into trading theses. Add later via /api/agents/update.">
          <div className="rounded-md border border-default bg-panel-2 max-h-[420px] overflow-y-auto divide-y divide-[color-mix(in_oklab,var(--color-fg-dim)_10%,transparent)]">
            {sources.map((s) => {
              const on = form.dataSources.includes(s.id);
              return (
                <button type="button" key={s.id} onClick={() => toggle(s.id)}
                  className={"w-full text-left px-3 py-2 flex items-start gap-3 hover:bg-panel " +
                    (on ? "" : "")}>
                  <div className={"mt-1 h-3.5 w-3.5 rounded-sm border " + (on ? "bg-accent border-accent" : "border-default")}/>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium text-fg">{s.name}</span>
                      <span className="text-[10px] uppercase font-mono tracking-wider text-fg-dim">{s.category}</span>
                    </div>
                    <div className="text-[11.5px] text-fg-muted leading-snug">{s.description}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </Field>
      </div>

      <div className="lg:col-span-2 flex items-center justify-between gap-3 pt-2 border-t border-default">
        <div className="text-[11.5px] text-fg-dim">
          {form.dataSources.length} source{form.dataSources.length !== 1 && "s"} selected · paper trading only
        </div>
        <Button variant="primary" type="submit" disabled={pending || form.dataSources.length === 0}>
          {pending ? "Deploying…" : "Deploy agent"}
        </Button>
      </div>
    </form>
  );
}
