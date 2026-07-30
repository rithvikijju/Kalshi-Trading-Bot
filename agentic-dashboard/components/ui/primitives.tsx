import * as React from "react";
import { cn } from "@/lib/utils";

/* Tiny shadcn-style primitives, hand-rolled to avoid pulling the whole
   shadcn init script (newer Tailwind/Next combo is finicky with it). */

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("bg-panel rounded-md border border-default", className)}
      {...p}
    />
  );
}
export function CardHeader({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-start justify-between px-4 pt-4 pb-2", className)} {...p} />;
}
export function CardTitle({ className, ...p }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-sm font-medium tracking-tight text-fg", className)} {...p} />;
}
export function CardBody({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-4 pb-4", className)} {...p} />;
}

export function Badge({
  className,
  tone = "neutral",
  ...p
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: "neutral" | "pos" | "neg" | "warn" | "info" | "accent" | "accent-2" }) {
  const tones: Record<string, string> = {
    neutral: "border border-default text-fg-muted bg-panel-2",
    pos: "border border-default text-pos bg-pos/10",
    neg: "border border-default text-neg bg-neg/10",
    warn: "border border-default text-warn bg-warn/10",
    info: "border border-default text-info bg-info/10",
    accent: "border border-default text-accent bg-accent/10",
    "accent-2": "border border-default text-accent-2 bg-accent-2/10",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10.5px] uppercase tracking-wider font-mono",
        tones[tone],
        className
      )}
      {...p}
    />
  );
}

export function Button(
  props: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: "primary" | "secondary" | "ghost" | "danger";
    size?: "sm" | "md" | "lg";
  }
) {
  const { className, variant = "secondary", size = "md", ...rest } = props;
  const sizes = {
    sm: "h-7 px-2.5 text-xs",
    md: "h-9 px-3 text-sm",
    lg: "h-10 px-4 text-sm",
  };
  const variants: Record<string, string> = {
    primary: "bg-accent text-[#03110d] hover:bg-[#7ce8d1]",
    secondary: "bg-panel-2 text-fg border border-default hover:border-strong",
    ghost: "text-fg-muted hover:text-fg hover:bg-panel-2",
    danger: "bg-neg/10 text-neg border border-default hover:bg-neg/20",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md font-medium transition disabled:opacity-50 disabled:cursor-not-allowed",
        sizes[size], variants[variant], className,
      )}
      {...rest}
    />
  );
}

export function Input({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-default bg-panel-2 px-3 text-sm text-fg",
        "placeholder:text-fg-dim focus:outline-none focus:border-accent",
        className
      )}
      {...p}
    />
  );
}

export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 w-full rounded-md border border-default bg-panel-2 px-3 text-sm text-fg",
        "focus:outline-none focus:border-accent appearance-none",
        className
      )}
      {...p}
    >
      {children}
    </select>
  );
}

export function Field({
  label, hint, children, className,
}: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-[11px] uppercase tracking-wider text-fg-dim font-mono">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-fg-dim">{hint}</span>}
    </label>
  );
}

export function Divider({ className }: { className?: string }) {
  return <div className={cn("h-px w-full bg-[color-mix(in_oklab,var(--color-fg-dim)_18%,transparent)]", className)} />;
}

export function StatusDot({ kind }: { kind: "live" | "off" | "warn" }) {
  const c =
    kind === "live" ? "bg-pos" : kind === "warn" ? "bg-warn" : "bg-fg-dim";
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full pulse-dot", c)} />;
}

export function Sparkline({ values, stroke = "var(--color-accent)" }: { values: number[]; stroke?: string }) {
  if (!values || values.length < 2) return null;
  const w = 96, h = 22;
  const min = Math.min(...values), max = Math.max(...values);
  const range = Math.max(1e-6, max - min);
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg width={w} height={h} className="opacity-90">
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth={1.4} />
    </svg>
  );
}
