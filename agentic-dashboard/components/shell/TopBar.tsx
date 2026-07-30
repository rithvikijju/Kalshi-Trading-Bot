"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, Search, Sun, Moon } from "lucide-react";
import { Badge, StatusDot } from "@/components/ui/primitives";

export function TopBar() {
  const [clock, setClock] = useState<string>("");
  useEffect(() => {
    const t = setInterval(() => {
      const d = new Date();
      setClock(
        d.toLocaleTimeString("en-US", { hour12: false, timeZone: "UTC" }) + " UTC"
      );
    }, 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="sticky top-0 z-30 flex items-center justify-between gap-4 border-b border-default bg-bg/85 backdrop-blur px-5 h-12">
      <div className="flex items-center gap-3">
        <Badge tone="warn" className="gap-1.5">
          <AlertTriangle size={11} className="-mt-px" />
          Paper Trading Mode
        </Badge>
        <span className="text-xs text-fg-dim">No real-money execution wired</span>
      </div>

      <div className="hidden md:flex relative items-center w-[280px]">
        <Search size={13} className="absolute left-2.5 text-fg-dim" />
        <input
          placeholder="Search agents, theses, markets…"
          className="h-7 w-full rounded-md border border-default bg-panel-2 pl-7 pr-2 text-xs text-fg placeholder:text-fg-dim focus:outline-none focus:border-strong"
        />
      </div>

      <div className="flex items-center gap-3 text-[11.5px] text-fg-dim font-mono">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot kind="live" /> tick worker
        </span>
        <span className="hidden md:inline">{clock || "—"}</span>
        <span className="inline-flex items-center gap-1 text-fg-muted">
          <Moon size={11} /> dark
        </span>
      </div>
    </div>
  );
}
