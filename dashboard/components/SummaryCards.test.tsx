import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SummaryCards } from "./SummaryCards";

describe("SummaryCards", () => {
  it("renders the open position count and both P&L totals", () => {
    render(
      <SummaryCards
        summary={{ openPositionCount: 3, totalUnrealizedPnl: 1234.5, totalRealizedPnl: -200 }}
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
    render(
      <SummaryCards
        summary={{ openPositionCount: 0, totalUnrealizedPnl: 0, totalRealizedPnl: 0 }}
      />
    );
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
