import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LevelGauge } from "./LevelGauge";

describe("LevelGauge", () => {
  it("renders a marker's label and formatted value", () => {
    render(<LevelGauge min={0} max={100} markers={[{ value: 26.94, label: "RSI" }]} />);
    expect(screen.getByText(/RSI: 26.94/)).toBeInTheDocument();
  });

  it("renders reference line labels with their values", () => {
    render(
      <LevelGauge
        min={0}
        max={100}
        markers={[{ value: 50, label: "Current" }]}
        referenceLines={[
          { value: 30, label: "Oversold" },
          { value: 70, label: "Overbought" },
        ]}
      />
    );
    expect(screen.getByText(/Oversold \(30\)/)).toBeInTheDocument();
    expect(screen.getByText(/Overbought \(70\)/)).toBeInTheDocument();
  });

  it("clamps a marker outside the domain rather than overflowing", () => {
    const { container } = render(
      <LevelGauge min={0} max={100} markers={[{ value: 150, label: "Way over" }]} />
    );
    // Should still render without throwing, positioned at the max edge (100%).
    expect(container.querySelector('[style*="left: 100%"]')).toBeTruthy();
  });

  it("renders multiple markers independently (e.g. MACD line vs signal line)", () => {
    render(
      <LevelGauge
        min={-10}
        max={10}
        markers={[
          { value: -2, label: "MACD" },
          { value: 1, label: "Signal" },
        ]}
      />
    );
    expect(screen.getByText(/MACD: -2/)).toBeInTheDocument();
    expect(screen.getByText(/Signal: 1/)).toBeInTheDocument();
  });

  it("handles a degenerate zero-width domain without dividing by zero", () => {
    expect(() =>
      render(<LevelGauge min={5} max={5} markers={[{ value: 5, label: "Only" }]} />)
    ).not.toThrow();
  });
});
