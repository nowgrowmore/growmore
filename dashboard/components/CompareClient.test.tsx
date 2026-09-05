import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CompareClient } from "./CompareClient";
import type { BotConfig } from "@/lib/types";

function makeConfig(overrides: Partial<BotConfig>): BotConfig {
  return {
    id: "c1",
    strategy_id: "s1",
    instrument_id: "i1",
    enabled: true,
    max_position_size: "1",
    daily_loss_limit: "15000",
    daily_loss_limit_enabled: true,
    mode: "paper",
    updated_at: "2026-01-01T00:00:00Z",
    strategy_name: "macd_trend",
    instrument_symbol: "GOLDM",
    strategy_params: { fast_period: 12, slow_period: 26, signal_period: 9 },
    ...overrides,
  };
}

describe("CompareClient", () => {
  it("shows a placeholder when there are no configs at all", () => {
    render(<CompareClient configs={[]} backtestRuns={[]} paperOrders={[]} liveOrders={[]} />);
    expect(screen.getAllByText("Pick a config above")).toHaveLength(2);
  });

  it("defaults to the first two configs and shows their strategy names", () => {
    const configs = [
      makeConfig({ id: "c1", strategy_name: "macd_trend", instrument_symbol: "GOLDM" }),
      makeConfig({ id: "c2", strategy_name: "rsi_mean_reversion", instrument_symbol: "SILVERM" }),
    ];
    render(<CompareClient configs={configs} backtestRuns={[]} paperOrders={[]} liveOrders={[]} />);
    expect(screen.getByText("macd_trend")).toBeInTheDocument();
    expect(screen.getByText("rsi_mean_reversion")).toBeInTheDocument();
  });

  it("switching the Config A dropdown updates which card renders", () => {
    const configs = [
      makeConfig({ id: "c1", strategy_name: "macd_trend", instrument_symbol: "GOLDM" }),
      makeConfig({ id: "c2", strategy_name: "rsi_mean_reversion", instrument_symbol: "SILVERM" }),
      makeConfig({ id: "c3", strategy_name: "sma_crossover", instrument_symbol: "COPPER" }),
    ];
    render(<CompareClient configs={configs} backtestRuns={[]} paperOrders={[]} liveOrders={[]} />);

    const [selectA] = screen.getAllByRole("combobox");
    fireEvent.change(selectA, { target: { value: "c3" } });

    expect(screen.getByText("sma_crossover")).toBeInTheDocument();
  });
});
