import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BotStatusBanner } from "./BotStatusBanner";
import type { BotStatus } from "@/lib/types";

function makeStatus(overrides: Partial<BotStatus> = {}): BotStatus {
  return {
    id: "status-1",
    live_trading_enabled: false,
    last_tick_at: new Date().toISOString(),
    available_balance: "217009.94",
    utilized_margin: "32401",
    ...overrides,
  };
}

describe("BotStatusBanner", () => {
  it("renders a fallback when there's no status row yet", () => {
    render(<BotStatusBanner status={null} />);
    expect(screen.getByText(/has not reported in yet/)).toBeInTheDocument();
  });

  it("shows disarmed for live_trading_enabled=false", () => {
    render(<BotStatusBanner status={makeStatus({ live_trading_enabled: false })} />);
    expect(screen.getByText("Live trading disarmed")).toBeInTheDocument();
  });

  it("shows armed for live_trading_enabled=true", () => {
    render(<BotStatusBanner status={makeStatus({ live_trading_enabled: true })} />);
    expect(screen.getByText("LIVE TRADING ARMED")).toBeInTheDocument();
  });

  it("flags a stale last tick", () => {
    const staleTs = new Date(Date.now() - 10 * 60_000).toISOString();
    render(<BotStatusBanner status={makeStatus({ last_tick_at: staleTs })} />);
    expect(screen.getByText(/— stale/)).toBeInTheDocument();
  });

  it("shows available balance and utilized margin when present", () => {
    render(<BotStatusBanner status={makeStatus()} />);
    expect(screen.getByText(/Available:/)).toBeInTheDocument();
    expect(screen.getByText(/Utilized margin:/)).toBeInTheDocument();
  });
});
