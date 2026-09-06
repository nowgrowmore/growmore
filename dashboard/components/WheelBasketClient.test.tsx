import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WheelBasketClient } from "./WheelBasketClient";
import type {
  WheelBasketConfig,
  WheelBasketLeg,
  WheelBasketPosition,
  WheelBasketSelection,
} from "@/lib/types";

function config(overrides: Partial<WheelBasketConfig> = {}): WheelBasketConfig {
  return {
    id: "config-1",
    strategy_id: "strategy-1",
    enabled: true,
    mode: "paper",
    total_virtual_capital: "1000000",
    top_iv_frac: "0.33",
    rotation_hysteresis_pct: "0.10",
    call_basis_buffer_tiers: [[60, 0.05], [40, 0.02], [0, 0]],
    updated_at: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

describe("WheelBasketClient", () => {
  it("renders the config summary and toggle", () => {
    render(
      <WheelBasketClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("IV-rich wheel basket")).toBeInTheDocument();
    expect(screen.getByText(/Top 33%/)).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("renders an open position's state and unrealized P&L", () => {
    const position: WheelBasketPosition = {
      id: "pos-1", config_id: "config-1", symbol: "RELIANCE", status: "open",
      state: "holding_shares", basis: "2500", shares: "250", lots: "1",
      opened_at: "2026-09-01T00:00:00Z", closed_at: null,
      realized_pnl: "0", unrealized_pnl: "1250.50",
    };
    render(
      <WheelBasketClient
        configs={[config()]}
        positionsByConfigId={{ "config-1": [position] }}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("Holding (uncovered)")).toBeInTheDocument();
  });

  it("renders the selection log with the reason text, including rejected candidates", () => {
    const selections: WheelBasketSelection[] = [
      {
        id: "sel-1", config_id: "config-1", cycle_date: "2026-09-01", symbol: "RELIANCE",
        selected: true, avg_iv: "0.45", iv_percentile: "0.85", rsi: "62", macd_bullish: true,
        score: "0.85", reason: "RELIANCE: IV percentile 85%, RSI 62, MACD bullish -- selected",
        created_at: "2026-09-01T00:00:00Z",
      },
      {
        id: "sel-2", config_id: "config-1", cycle_date: "2026-09-01", symbol: "TCS",
        selected: false, avg_iv: "0.15", iv_percentile: "0.10", rsi: null, macd_bullish: null,
        score: "0.10", reason: "TCS: IV percentile 10% -- not selected",
        created_at: "2026-09-01T00:00:00Z",
      },
    ];
    render(
      <WheelBasketClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": selections }}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/RELIANCE: IV percentile 85%/)).toBeInTheDocument();
    expect(screen.getByText(/TCS: IV percentile 10%/)).toBeInTheDocument();
    expect(screen.getAllByText("Selected").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Rejected").length).toBeGreaterThan(0);
  });

  it("renders trade history rows with outcome labels", () => {
    const leg: WheelBasketLeg = {
      id: "leg-1", position_id: "pos-1", symbol: "RELIANCE", cycle_expiry: "2026-09-24",
      opt_type: "PE", strike: "2500", premium: "35.5", lots: "1", action: "sell_put",
      opened_at: "2026-08-27T00:00:00Z", settled_at: "2026-09-24T00:00:00Z",
      assigned: true, called_away: false, pnl: "3500",
    };
    render(
      <WheelBasketClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{ "config-1": [leg] }}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("sell put")).toBeInTheDocument();
    expect(screen.getByText("Assigned")).toBeInTheDocument();
  });

  it("renders one section per config when there are several", () => {
    render(
      <WheelBasketClient
        configs={[config({ id: "config-1" }), config({ id: "config-2", mode: "live" })]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getAllByText("IV-rich wheel basket")).toHaveLength(2);
  });
});
