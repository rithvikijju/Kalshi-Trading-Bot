import { NextResponse } from "next/server";
import { deployAgent } from "@/lib/worker";

export async function POST(req: Request) {
  const body = await req.json();
  if (!body?.name || !body?.market || !body?.strategy || !Array.isArray(body.dataSources)) {
    return NextResponse.json({ error: "invalid payload" }, { status: 400 });
  }
  const id = deployAgent({
    name: String(body.name).slice(0, 80),
    market: body.market,
    strategy: body.strategy,
    dataSources: body.dataSources.slice(0, 16),
    riskBudgetUsd: Number(body.riskBudgetUsd) || 50_000,
    maxPositionUsd: Number(body.maxPositionUsd) || 10_000,
    thesisRefreshSec: Number(body.thesisRefreshSec) || 60,
    confidenceThreshold: Number(body.confidenceThreshold) || 0.7,
    stopLossPct: Number(body.stopLossPct) || 0.06,
    takeProfitPct: Number(body.takeProfitPct) || 0.12,
  });
  return NextResponse.json({ id });
}
