import { NextResponse } from "next/server";
import { getState } from "@/lib/store";
import { setAgentStatus } from "@/lib/worker";

export async function POST(req: Request) {
  const form = await req.formData();
  const agentId = String(form.get("agentId") ?? "");
  const a = getState().agents[agentId];
  if (!a) return NextResponse.json({ error: "not found" }, { status: 404 });
  setAgentStatus(agentId, a.status === "paused" ? "researching" : "paused");
  return NextResponse.redirect(new URL(req.headers.get("referer") || "/agents", req.url), 303);
}
