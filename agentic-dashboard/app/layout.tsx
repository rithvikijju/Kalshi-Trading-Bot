import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";

export const metadata: Metadata = {
  title: "QUANTA — Agentic Research Console",
  description: "Agentic market research & paper trading dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-bg text-fg">
        <div className="flex">
          <Sidebar />
          <div className="flex-1 min-w-0">
            <TopBar />
            <main className="px-6 py-5">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
