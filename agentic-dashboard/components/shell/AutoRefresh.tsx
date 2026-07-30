"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Periodically calls router.refresh() so server-rendered pages stay live.
    Drop-in: <AutoRefresh ms={2500} /> */
export function AutoRefresh({ ms = 3000 }: { ms?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), ms);
    return () => clearInterval(id);
  }, [ms, router]);
  return null;
}
