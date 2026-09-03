import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SummaryCards } from "./SummaryCards";
import type { PnlSummary } from "@/lib/format";

function makeSummary(overrides: Partial<PnlSummary> = {}): PnlSummary {
  return {
    openPositionCount: 0,
    totalUnrealizedPnl: 0,
    totalRealizedPnl: 0,
    netPnl: 0,
    closedTradeCount: 0,
    winRatePct: null,
    bestTrade: null,
    worstTrade: null,
    ...overrides,
  };
}

describe("SummaryCards", () => {
  it("renders the open position count and both P&L totals", () => {
    render(
      <SummaryCards
        summary={makeSummary({ openPositionCount: 3, totalUnrealizedPnl: 1234.5, totalRealizedPnl: -200, netPnl: 1034.5 })}
      />
    );

    expect(screen.getByText("Open positions")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Unrealized P&L")).toBeInTheDocument();
    expect(screen.getByText("Realized P&L")).toBeInTheDocument();
    // Negative realized P&L should render with a leading minus sign.
    const realizedCard = screen.getByText("Realized P&L").closest('[role="listitem"]');
    expect(realizedCard?.textContent).toMatch(/-/);
  });

  it("shows zeros without throwing when there is no activity yet", () => {
    render(<SummaryCards summary={makeSummary()} />);
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    // No closed trades yet -- win rate/best/worst show a dash, not 0%/₹0.
    expect(screen.getByText("Win rate")).toBeInTheDocument();
    const winRateCard = screen.getByText("Win rate").closest('[role="listitem"]');
    expect(winRateCard?.textContent).toMatch(/—/);
  });

  it("renders net P&L and win/loss stats once trades have closed", () => {
    render(
      <SummaryCards
        summary={makeSummary({
          closedTradeCount: 4,
          winRatePct: 75,
          bestTrade: 500,
          worstTrade: -200,
          netPnl: 1234,
        })}
      />
    );

    expect(screen.getByText("Net P&L")).toBeInTheDocument();
    expect(screen.getByText("Closed trades")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("75.00%")).toBeInTheDocument();
    expect(screen.getByText("Best trade")).toBeInTheDocument();
    expect(screen.getByText("Worst trade")).toBeInTheDocument();
  });
});
