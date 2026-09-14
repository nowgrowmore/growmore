import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MCXOptionsClient } from "./MCXOptionsClient";
import type {
  MCXOptionsConfig,
  MCXOptionsLeg,
  MCXOptionsPosition,
  MCXOptionsSelection,
} from "@/lib/types";

function config(overrides: Partial<MCXOptionsConfig> = {}): MCXOptionsConfig {
  return {
    id: "config-1",
    strategy_id: "strategy-1",
    enabled: true,
    mode: "paper",
    symbol: "GOLDM",
    lots: 1,
    consolidating_target_delta: "0.30",
    trend_favorable_target_delta: "0.50",
    min_open_interest: 100,
    margin_multiple_of_premium: "3.0",
    updated_at: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

describe("MCXOptionsClient", () => {
  it("renders the config summary and toggle", () => {
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("GOLDM options wheel")).toBeInTheDocument();
    expect(screen.getByText(/Lots 1/)).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("renders an open long-futures position's basis and unrealized P&L", () => {
    const position: MCXOptionsPosition = {
      id: "pos-1",
      config_id: "config-1",
      status: "open",
      state: "long_futures",
      basis: "62000",
      futures_qty: "100",
      futures_contract_expiry: "2026-10-28",
      opened_at: "2026-09-01T00:00:00Z",
      closed_at: null,
      realized_pnl: "0",
      unrealized_pnl: "1250.50",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{ "config-1": [position] }}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("Long futures (covered)")).toBeInTheDocument();
  });

  it("renders the selection log with the reason text, including skipped cycles", () => {
    const selections: MCXOptionsSelection[] = [
      {
        id: "sel-1",
        config_id: "config-1",
        cycle_date: "2026-09-01",
        regime: "consolidating",
        target_delta: "0.30",
        selected_strike: "61000",
        reason: "Consolidating regime, target delta 0.30 -- sold 61000 PE",
        created_at: "2026-09-01T00:00:00Z",
      },
      {
        id: "sel-2",
        config_id: "config-1",
        cycle_date: "2026-08-25",
        regime: "trend_unfavorable",
        target_delta: null,
        selected_strike: null,
        reason: "Trend unfavorable -- no entry",
        created_at: "2026-08-25T00:00:00Z",
      },
    ];
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": selections }}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/sold 61000 PE/)).toBeInTheDocument();
    expect(screen.getByText(/no entry/)).toBeInTheDocument();
    expect(screen.getByText("Consolidating")).toBeInTheDocument();
    expect(screen.getByText("Trend unfavorable")).toBeInTheDocument();
  });

  it("renders trade history rows with outcome labels", () => {
    const leg: MCXOptionsLeg = {
      id: "leg-1",
      position_id: "pos-1",
      cycle_expiry: "2026-09-24",
      opt_type: "PE",
      strike: "61000",
      premium: "350",
      lots: "1",
      action: "sell_put",
      opened_at: "2026-08-27T00:00:00Z",
      settled_at: "2026-09-24T00:00:00Z",
      assigned: true,
      called_away: false,
      pnl: "35000",
    };
    render(
      <MCXOptionsClient
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
      <MCXOptionsClient
        configs={[config({ id: "config-1", symbol: "GOLDM" }), config({ id: "config-2", symbol: "SILVERM" })]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("GOLDM options wheel")).toBeInTheDocument();
    expect(screen.getByText("SILVERM options wheel")).toBeInTheDocument();
  });
});
