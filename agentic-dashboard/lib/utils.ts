import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtUsd(n: number | null | undefined, opts?: { signed?: boolean; cents?: boolean }) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : opts?.signed ? "+" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(2)}K`;
  return opts?.cents
    ? `${sign}$${abs.toFixed(2)}`
    : `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function fmtPct(n: number | null | undefined, decimals = 2, signed = true) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(decimals)}%`;
}

export function fmtBps(n: number | null | undefined) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(0)} bps`;
}

export function fmtRelative(iso: string | undefined) {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function tone(n: number | undefined | null) {
  if (n === undefined || n === null || Number.isNaN(n)) return "text-fg-muted";
  return n > 0 ? "text-pos" : n < 0 ? "text-neg" : "text-fg-muted";
}

export function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

export const MARKET_LABEL: Record<string, string> = {
  prediction_markets: "Prediction Mkts",
  crypto: "Crypto",
  equities: "Equities",
  macro: "Macro",
  commodities: "Commodities",
  rates: "Rates",
  private_signals: "Private Signals",
  sports: "Sports",
  news_arb: "News Arb",
  cross_asset_rv: "Cross-Asset RV",
};

export const STRATEGY_LABEL: Record<string, string> = {
  arbitrage: "Arbitrage",
  momentum: "Momentum",
  mean_reversion: "Mean Reversion",
  event_driven: "Event-Driven",
  sentiment: "Sentiment",
  statistical: "Statistical",
  market_making: "Market Making",
  relative_value: "Relative Value",
};

export const STATUS_TONE: Record<string, string> = {
  idle: "text-fg-muted",
  researching: "text-info",
  backtesting: "text-accent-2",
  paper_trading: "text-pos",
  paused: "text-warn",
  watching: "text-info",
  active: "text-accent",
  profitable: "text-pos",
  losing: "text-neg",
  invalidated: "text-fg-muted",
  open: "text-info",
  closed: "text-fg-muted",
  trace: "text-fg-muted",
  info: "text-info",
  signal: "text-accent",
  trade: "text-pos",
  warn: "text-warn",
  error: "text-neg",
};
