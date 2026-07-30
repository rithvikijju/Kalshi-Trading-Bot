import { NextResponse } from "next/server";
import { runAgentOnce } from "@/lib/worker";

export async function POST(req: Request) {
  const form = await req.formData();
  const agentId = String(form.get("agentId") ?? "");
  runAgentOnce(agentId);
  return NextResponse.redirect(new URL(req.headers.get("referer") || `/agents/${agentId}`, req.url), 303);
}
