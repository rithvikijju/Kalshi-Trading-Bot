"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Bot, Rocket, Lightbulb, LineChart, Activity, FileText, BarChart3, ShieldCheck
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Command Center", icon: LayoutDashboard },
  { href: "/agents", label: "Agent Monitor", icon: Bot },
  { href: "/deploy", label: "Deploy Agent", icon: Rocket },
  { href: "/theses", label: "Thesis Explorer", icon: Lightbulb },
  { href: "/trades", label: "Paper Trading", icon: LineChart },
  { href: "/feed", label: "Discovery Feed", icon: Activity },
  { href: "/logs", label: "Agent Logs", icon: FileText },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="h-screen w-60 shrink-0 border-r border-default bg-panel sticky top-0 flex flex-col">
      <div className="px-4 pt-5 pb-3">
        <div className="flex items-center gap-2">
          <div className="h-7 w-7 rounded-md bg-accent/10 border border-default flex items-center justify-center">
            <ShieldCheck size={15} className="text-accent" />
          </div>
          <div className="leading-tight">
            <div className="text-[13px] font-semibold tracking-tight">QUANTA</div>
            <div className="text-[10px] uppercase tracking-widest text-fg-dim font-mono">research console</div>
          </div>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5 px-2 mt-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition",
                active
                  ? "bg-panel-2 text-fg border border-default"
                  : "text-fg-muted hover:text-fg hover:bg-panel-2"
              )}
            >
              <Icon size={14} className={cn(active ? "text-accent" : "")} />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto px-4 py-4 text-[10.5px] text-fg-dim font-mono">
        <div>v0.1 — paper only</div>
        <div className="mt-1">tick worker · {`<live>`}</div>
      </div>
    </aside>
  );
}
