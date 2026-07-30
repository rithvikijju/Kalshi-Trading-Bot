import { NextResponse } from "next/server";
import { cloneAgent } from "@/lib/worker";

export async function POST(req: Request) {
  const form = await req.formData();
  const agentId = String(form.get("agentId") ?? "");
  const id = cloneAgent(agentId);
  if (!id) return NextResponse.json({ error: "not found" }, { status: 404 });
  return NextResponse.redirect(new URL(`/agents/${id}`, req.url), 303);
}
