import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Growmore Dashboard",
  description: "Paper-trading analytics for the Growmore MCX bot",
};

const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/backtests", label: "Backtests" },
  { href: "/trades", label: "Trade Log" },
  { href: "/strategies", label: "Strategies" },
] as const;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans text-[15px]">
        <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
          <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="text-lg font-semibold text-[color:var(--text-primary)]">
                Growmore
              </h1>
              <p className="text-sm text-[color:var(--text-secondary)]">
                MCX paper-trading analytics — read-only against the bot&apos;s Neon Postgres
                schema.
              </p>
            </div>
            <nav
              aria-label="Main"
              className="flex flex-wrap gap-1 rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] p-1"
            >
              {NAV_ITEMS.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-md px-3 py-1.5 text-sm font-medium text-[color:var(--text-secondary)] hover:bg-[color:var(--gridline)] hover:text-[color:var(--text-primary)]"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </header>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
