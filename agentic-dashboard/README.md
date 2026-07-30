# QUANTA — Agentic Research Console

A Bloomberg-terminal-flavored Next.js dashboard for an **agentic market research
& paper trading** system. AI agents continuously scan prediction markets,
crypto, equities, macro, alt-data, news, and social signals — surface theses,
fire paper trades through an in-process broker, and log everything so you
can audit what they saw, what they decided, and why.

> Paper trading only. No real-money execution code lives in this app.
> The banner across the top of every page reinforces the mode.

---

## Quick start

```bash
npm install
npm run dev    # http://localhost:3000
```

The first boot seeds 8 sample agents (Prediction Market Arb, Crypto Funding,
Macro Rates, AI-Infra Equity, Private Signal, News Catalyst, Cross-Asset RV,
Sports Inefficiency). A background tick worker boots from
`instrumentation.ts` and starts generating discoveries, theses, and trades
within a few seconds. The state survives restarts (saved to
`data/state.json`).

To wipe the state and reseed:
```bash
rm data/state.json
```

---

## Pages

| Path           | Purpose                                                                     |
| -------------- | --------------------------------------------------------------------------- |
| `/`            | Command Center — KPIs, equity curve, top agents, latest theses, regime, feed |
| `/agents`      | Agent Monitor — every agent as a card with PnL, status, sources, controls    |
| `/agents/[id]` | Agent detail — recent theses, trades, logs, manual run/pause/clone           |
| `/deploy`      | Deploy a new agent (form posts to `/api/agents/create`)                      |
| `/theses`      | Thesis Explorer — expandable research cards, filters, linked trades          |
| `/trades`      | Paper Trading — open & closed trade tables with stops, fees, P&L            |
| `/feed`        | Discovery Feed — chronological alerts/signals/info                          |
| `/logs`        | Raw log stream + per-agent quick views                                       |
| `/analytics`   | Equity, drawdown, by-agent / by-market / by-strategy performance             |

---

## Architecture

```
lib/
  types.ts            — domain models (Agent, Thesis, PaperTrade, ...)
  store.ts            — in-process singleton store w/ JSON persistence
  dataConnectors.ts   — connector registry + mock signal generators
  strategyEngines.ts  — one decide() per strategy type (arb, momentum, …)
  paperBroker.ts      — simulated execution, fees, mark-to-market, stops
  riskManager.ts      — confidence gating, budget caps, exposure limits
  worker.ts           — 1.5s tick loop: think → trade → mark → log
  seed.ts             — initial agents, symbol universe, blurbs

instrumentation.ts    — Next.js hook that boots worker on server start

components/
  shell/  Sidebar, TopBar (Paper Mode banner), AutoRefresh
  ui/     hand-rolled shadcn-style primitives (Card, Badge, Button, ...)
  metrics/ agents/ theses/ trades/ logs/ feed/ charts/

app/                  — App Router pages + API routes
```

### Abstraction layers

The four layers you asked for, and where they live:

- **`dataConnectors`** — `lib/dataConnectors.ts`. Registry of upstream
  sources with mock generators. To wire a real API, replace the body of
  `sampleConnector` (or the registry per-id) with an authenticated fetch
  and keep the return shape `{sourceId, ts, symbol, field, value}`.
- **`strategyEngines`** — `lib/strategyEngines.ts`. Each `StrategyType`
  maps to a `decide()` that turns connector samples into a candidate trade.
- **`paperBroker`** — `lib/paperBroker.ts`. Owns trade lifecycle (open,
  mark, close), fees, slippage. Real brokers (Alpaca, IBKR, Kalshi) plug
  in by satisfying the same surface area.
- **`riskManager`** — `lib/riskManager.ts`. Advisory layer — given a
  candidate, returns approved size or a reject reason. Per-agent budget,
  per-trade max, portfolio-wide exposure cap.

### How to wire a real data source

```ts
// in lib/dataConnectors.ts
export function sampleConnector(id, symbol) {
  if (id === "kalshi") {
    // REAL: call Kalshi REST/WS, return { sourceId, ts, symbol, field, value }
    // Recommended: cache top-of-book into Redis/sqlite and read from it here.
  }
  // ...
}
```

Likely targets to wire next:
- **Kalshi** — `kalshi_v2/client.py` in the parent repo already has RSA-PSS auth.
- **Polymarket** — Gamma REST + `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- **Alpaca / Polygon** — equity ticks (Alpaca has a paper endpoint).
- **SEC EDGAR** — free, no auth, but set a UA header.
- **FRED** — free macro time series.
- **NewsAPI / Refinitiv / Benzinga** — paid wires for catalyst clustering.

---

## Notes

- Persistence: `data/state.json`. Replace with Drizzle/Prisma+SQLite or
  Supabase by swapping `lib/store.ts`; nothing upstream depends on the on-disk
  format — only on `getState` / `mutate`.
- The tick worker runs **in-process**. For multi-process deploys, move it to
  a separate Node script and point both at the same store backend.
- Charts: Recharts. No external service.
- Styling: Tailwind 4 — design tokens in `app/globals.css` `@theme`.

---

## Disclaimers

- All connectors here emit synthetic data. None of the dashboard numbers
  are tradable signals.
- The `riskManager` is a toy. Wire VaR, sector caps, and venue limits
  before pointing this at real funds.
