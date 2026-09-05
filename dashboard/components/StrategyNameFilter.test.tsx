import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StrategyNameFilter } from "./StrategyNameFilter";

const OPTIONS = [
  { value: "macd_trend", label: "MACD Trend" },
  { value: "regime_switch", label: "Regime-Switch" },
];

describe("StrategyNameFilter", () => {
  it("renders nothing when there's only one (or zero) strategy to filter by", () => {
    const { container } = render(
      <StrategyNameFilter
        options={[{ value: "macd_trend", label: "MACD Trend" }]}
        selected={new Set(["macd_trend"])}
        onToggle={vi.fn()}
        onSelectAll={vi.fn()}
        onSelectNone={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("calls onToggle with the clicked strategy's value", () => {
    const onToggle = vi.fn();
    render(
      <StrategyNameFilter
        options={OPTIONS}
        selected={new Set(["macd_trend", "regime_switch"])}
        onToggle={onToggle}
        onSelectAll={vi.fn()}
        onSelectNone={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("MACD Trend"));
    expect(onToggle).toHaveBeenCalledWith("macd_trend");
  });

  it("shows 'Select none' when everything is selected, and calls onSelectNone", () => {
    const onSelectNone = vi.fn();
    render(
      <StrategyNameFilter
        options={OPTIONS}
        selected={new Set(["macd_trend", "regime_switch"])}
        onToggle={vi.fn()}
        onSelectAll={vi.fn()}
        onSelectNone={onSelectNone}
      />
    );
    fireEvent.click(screen.getByText("Select none"));
    expect(onSelectNone).toHaveBeenCalled();
  });

  it("shows 'Select all' when something is deselected, and calls onSelectAll", () => {
    const onSelectAll = vi.fn();
    render(
      <StrategyNameFilter
        options={OPTIONS}
        selected={new Set(["macd_trend"])}
        onToggle={vi.fn()}
        onSelectAll={onSelectAll}
        onSelectNone={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("Select all"));
    expect(onSelectAll).toHaveBeenCalled();
  });

  it("marks a deselected strategy as aria-pressed=false", () => {
    render(
      <StrategyNameFilter
        options={OPTIONS}
        selected={new Set(["macd_trend"])}
        onToggle={vi.fn()}
        onSelectAll={vi.fn()}
        onSelectNone={vi.fn()}
      />
    );
    expect(screen.getByText("MACD Trend")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Regime-Switch")).toHaveAttribute("aria-pressed", "false");
  });

  it("defaults its label to 'Strategy:' but accepts a custom one for reuse (e.g. instruments)", () => {
    const { rerender } = render(
      <StrategyNameFilter
        options={OPTIONS}
        selected={new Set(["macd_trend", "regime_switch"])}
        onToggle={vi.fn()}
        onSelectAll={vi.fn()}
        onSelectNone={vi.fn()}
      />
    );
    expect(screen.getByText("Strategy:")).toBeInTheDocument();

    rerender(
      <StrategyNameFilter
        options={OPTIONS}
        selected={new Set(["macd_trend", "regime_switch"])}
        onToggle={vi.fn()}
        onSelectAll={vi.fn()}
        onSelectNone={vi.fn()}
        label="Instrument:"
      />
    );
    expect(screen.getByText("Instrument:")).toBeInTheDocument();
  });
});
